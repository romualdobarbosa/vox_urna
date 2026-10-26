"""Resultados divulgados pelo TSE em resultados.tse.jus.br: um JSON por Brasil, UF e município.

É a fonte do site de resultados, publicada na noite da eleição, antes dos BUs do CKAN. Não chega
à seção. A API só mantém o ciclo eleitoral corrente, então 2018 e 2022 não estão mais nela.
"""

import hashlib
import json
import logging
import os
from collections import Counter
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import requests

from vox_urna.bronze.http import com_retry
from vox_urna.bronze.manifest import Manifest, Status

log = logging.getLogger(__name__)

BASE = "https://resultados.tse.jus.br/oficial"
URL_ELEICOES = f"{BASE}/comum/config/ele-c.json"
ANOS = (2026,)

_CARGO_PRESIDENTE = "1"
_BRASILIA = timezone(timedelta(hours=-3))
_PULADO = "pulado"
# A cada quantos arquivos o manifest é regravado; se o run cair, o resto é baixado de novo.
_SALVAR_A_CADA = 500

# Abrangência do arquivo → valor de `tpabr` que o TSE grava nele.
_TPABR = {"brasil": "br", "uf": "uf", "municipio": "mu"}


@dataclass(frozen=True)
class Eleicao:
    ano: int
    turno: int
    codigo: int

    @property
    def url_base(self) -> str:
        return f"{BASE}/ele{self.ano}/{self.codigo}"


@dataclass(frozen=True)
class Arquivo:
    """Um JSON da eleição: a lista de municípios (`config`) ou o resultado de uma abrangência."""

    eleicao: Eleicao
    abrangencia: str  # config, brasil, uf ou municipio
    uf: str = ""  # maiúscula, como no BU; ZZ é o exterior
    cd_municipio: str = ""  # código do TSE com 5 dígitos, como na lista do TSE

    @property
    def nome(self) -> str:
        e = f"e{self.eleicao.codigo:06d}"
        return {
            "config": f"mun-{e}-cm.json",
            "brasil": f"br-c0001-{e}-u.json",
            "uf": f"{self.uf.lower()}-c0001-{e}-u.json",
            "municipio": f"{self.uf.lower()}{self.cd_municipio}-c0001-{e}-u.json",
        }[self.abrangencia]

    @property
    def url(self) -> str:
        if self.abrangencia == "config":
            pasta = "config"
        else:
            pasta = "dados/" + ("br" if self.abrangencia == "brasil" else self.uf.lower())
        return f"{self.eleicao.url_base}/{pasta}/{self.nome}"

    @property
    def caminho(self) -> Path:
        return Path(f"ano={self.eleicao.ano}") / f"turno={self.eleicao.turno}" / self.nome


@dataclass(frozen=True)
class EntradaResultado:
    url: str
    ano: int
    turno: int
    abrangencia: str
    uf: str
    cd_municipio: str
    caminho: str
    sha512: str
    bytes: int
    gerado_em: str  # data e hora em que o TSE gerou o arquivo (dg e hg), em Brasília
    downloaded_at: str
    status: Status

    @property
    def chave(self) -> tuple[int, int, str, str, str]:
        return (self.ano, self.turno, self.abrangencia, self.uf, self.cd_municipio)


def resolver_eleicao(session: requests.Session, ano: int, turno: int, **retry) -> Eleicao:
    """Acha, na config do TSE, o código da eleição de Presidente daquele ano e turno."""

    def chamar() -> dict:
        resposta = session.get(URL_ELEICOES, timeout=60)
        resposta.raise_for_status()
        return resposta.json()

    config = com_retry(chamar, **retry)
    codigos = [
        int(eleicao["cd"])
        for pleito in config["pl"]
        if pleito["c"] == f"ele{ano}"
        for eleicao in pleito["e"]
        if eleicao["t"] == str(turno)
        and any(
            cargo["cd"] == _CARGO_PRESIDENTE
            for abrangencia in eleicao["abr"]
            for cargo in abrangencia.get("cp", [])
        )
    ]
    if len(codigos) != 1:
        raise ValueError(f"esperava 1 eleição de Presidente em {ano}/{turno}, achei {codigos}")
    return Eleicao(ano, turno, codigos[0])


def municipios(config: dict) -> list[tuple[str, str]]:
    """(UF, código do município) de cada município da lista do TSE, exterior incluído."""
    return [
        (abrangencia["cd"].upper(), municipio["cd"])
        for abrangencia in config["abr"]
        for municipio in abrangencia["mu"]
    ]


def ingerir_resultados(
    dir_bronze: Path,
    anos: Iterable[int] = ANOS,
    *,
    turnos: Iterable[int] | None = None,
    ufs: Iterable[str] | None = None,
    force: bool = False,
    workers: int = 4,
    session: requests.Session | None = None,
    tentativas: int = 5,
    espera_base: float = 1.0,
) -> Counter[str]:
    """Baixa os JSONs de Presidente para `dir_bronze` e devolve a contagem de cada desfecho.

    Por turno: a lista de municípios primeiro, depois o Brasil, as UFs e os municípios. Com
    `ufs`, baixa só as UFs pedidas e os seus municípios, sem o Brasil.
    """
    session = session or requests.Session()
    retry = {"tentativas": tentativas, "espera_base": espera_base}
    manifest = Manifest(dir_bronze / "manifest.csv", EntradaResultado)
    ufs = {uf.upper() for uf in ufs} if ufs else None
    resumo: Counter[str] = Counter()

    def processar(arquivo: Arquivo) -> EntradaResultado | None:
        anterior = manifest.get(arquivo.url)
        if (
            not force
            and anterior is not None
            and anterior.status is Status.SUCCESS
            and (dir_bronze / anterior.caminho).exists()
        ):
            return None
        return _baixar(session, arquivo, dir_bronze, retry)

    def registrar(entrada: EntradaResultado | None, salvar: bool) -> None:
        if entrada is None:
            resumo[_PULADO] += 1
            return
        resumo[entrada.status] += 1
        manifest.registrar(entrada, salvar=salvar)

    for ano in anos:
        for turno in turnos or (1, 2):
            eleicao = resolver_eleicao(session, ano, turno, **retry)
            config = Arquivo(eleicao, "config")
            registrar(processar(config), salvar=True)
            entrada_config = manifest.get(config.url)
            if entrada_config is None or entrada_config.status is not Status.SUCCESS:
                log.error("%s/%s: sem a lista de municípios, turno pulado", ano, turno)
                continue

            lista = json.loads((dir_bronze / entrada_config.caminho).read_bytes())
            todos = municipios(lista)
            arquivos = [Arquivo(eleicao, "brasil")] if ufs is None else []
            arquivos += [
                Arquivo(eleicao, "uf", uf)
                for uf in sorted({uf for uf, _ in todos})
                if ufs is None or uf in ufs
            ]
            arquivos += [
                Arquivo(eleicao, "municipio", uf, cd)
                for uf, cd in todos
                if ufs is None or uf in ufs
            ]
            log.info("%s/%s: eleição %d, %d arquivos", ano, turno, eleicao.codigo, len(arquivos))

            # Os workers só baixam; quem grava o manifest é esta thread, de tempos em tempos.
            with ThreadPoolExecutor(max_workers=workers) as executor:
                for i, entrada in enumerate(executor.map(processar, arquivos), start=1):
                    registrar(entrada, salvar=i % _SALVAR_A_CADA == 0)
            manifest.salvar()
            log.info("%s/%s: %s", ano, turno, dict(resumo))
    return resumo


def _baixar(
    session: requests.Session, arquivo: Arquivo, dir_bronze: Path, retry: dict
) -> EntradaResultado:
    destino = dir_bronze / arquivo.caminho
    parcial = destino.with_name(destino.name + ".part")
    destino.parent.mkdir(parents=True, exist_ok=True)

    def entrada(status: Status, conteudo: bytes = b"", gerado_em: str = "") -> EntradaResultado:
        return EntradaResultado(
            url=arquivo.url,
            ano=arquivo.eleicao.ano,
            turno=arquivo.eleicao.turno,
            abrangencia=arquivo.abrangencia,
            uf=arquivo.uf,
            cd_municipio=arquivo.cd_municipio,
            caminho=arquivo.caminho.as_posix(),
            sha512=hashlib.sha512(conteudo).hexdigest() if conteudo else "",
            bytes=len(conteudo),
            gerado_em=gerado_em,
            downloaded_at=datetime.now(UTC).isoformat(timespec="seconds"),
            status=status,
        )

    def chamar() -> bytes:
        resposta = session.get(arquivo.url, timeout=60)
        resposta.raise_for_status()
        return resposta.content

    try:
        conteudo = com_retry(chamar, **retry)
    except requests.RequestException as erro:
        log.error("falha em %s: %s", arquivo.nome, erro)
        return entrada(Status.FAILED)

    try:
        dados = json.loads(conteudo)
        gerado_em = _gerado_em(dados)
        _conferir(arquivo, dados)
    except (ValueError, KeyError, TypeError) as erro:
        log.error("conteúdo inesperado em %s: %s", arquivo.nome, erro)
        return entrada(Status.CORRUPTED, conteudo)

    if arquivo.abrangencia != "config" and dados.get("tf") != "s":
        log.warning("%s: totalização ainda não é final", arquivo.nome)
        return entrada(Status.INCOMPLETE, conteudo, gerado_em)

    parcial.write_bytes(conteudo)
    os.replace(parcial, destino)
    return entrada(Status.SUCCESS, conteudo, gerado_em)


def _gerado_em(dados: dict) -> str:
    momento = datetime.strptime(f"{dados['dg']} {dados['hg']}", "%d/%m/%Y %H:%M:%S")
    return momento.replace(tzinfo=_BRASILIA).isoformat()


def _conferir(arquivo: Arquivo, dados: dict) -> None:
    """Falha se o JSON não for o resultado de Presidente que foi pedido."""
    if arquivo.abrangencia == "config":
        if not dados["abr"] or any(not abrangencia["mu"] for abrangencia in dados["abr"]):
            raise ValueError("lista de municípios vazia")
        return

    esperado = {
        "ele": str(arquivo.eleicao.codigo),
        "t": str(arquivo.eleicao.turno),
        "tpabr": _TPABR[arquivo.abrangencia],
        "cdabr": {"brasil": "br", "uf": arquivo.uf.lower()}.get(
            arquivo.abrangencia, arquivo.cd_municipio
        ),
    }
    obtido = {campo: dados[campo] for campo in esperado}
    if obtido != esperado:
        raise ValueError(f"esperava {esperado}, veio {obtido}")
    if not any(cargo["cd"] == _CARGO_PRESIDENTE for cargo in dados["carg"]):
        raise ValueError("sem o cargo de Presidente")
