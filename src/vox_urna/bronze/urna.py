"""Arquivos de urna do site de resultados: o BU de cada seção, em ASN.1, como saiu da urna.

É a mesma granularidade do BU do CKAN (bweb), mas publicada na noite da eleição, dias antes dele.
Por UF, o TSE publica uma lista de seções (config); por seção, um índice (aux.json) com o hash
de cada urna que gerou resultado ali, e na pasta do hash, o BU.
"""

import hashlib
import json
import logging
import os
from collections import Counter
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

import asn1tools
import requests

from vox_urna.bronze.http import com_retry
from vox_urna.bronze.manifest import Manifest, Status

log = logging.getLogger(__name__)

BASE = "https://resultados.tse.jus.br/oficial"
SPEC = Path(__file__).parent / "asn1" / "bu.asn1"

# (ano, turno) → código do pleito no site de resultados. O BU traz todas as eleições do pleito;
# a de Presidente é escolhida no silver.
PLEITOS = {(2026, 2): 3221}
UFS = (
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
    "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO", "ZZ",
)  # fmt: skip

# O BU da urna (tp "bu") ou, onde se votou em cédula (só no exterior), o do Sistema de Apuração
# (tp "busa"), no mesmo ASN.1. Nessa ordem de preferência.
TIPOS_BU = ("bu", "busa")
# Situações da seção e do hash que trazem um BU válido. A "Recebida" tem o BU que chegou ao TSE
# e não passou a "Totalizada" no site (em 2026/2, ~10 mil seções só com esse hash); entre hashes,
# o "Totalizado" tem precedência.
SITUACOES_SECAO = ("Totalizada", "Recebida")
SITUACOES_HASH = ("Totalizado", "Recebido")
_PULADO = "pulado"
# A cada quantas seções o manifest da UF é regravado; se o run cair, o resto é baixado de novo.
_SALVAR_A_CADA = 2000


@dataclass(frozen=True)
class Pleito:
    ano: int
    turno: int
    codigo: int

    @property
    def url_base(self) -> str:
        return f"{BASE}/ele{self.ano}/arquivo-urna/{self.codigo}"

    @property
    def particao(self) -> Path:
        return Path(f"ano={self.ano}") / f"turno={self.turno}"

    def url_config(self, uf: str) -> str:
        return f"{self.url_base}/config/{uf.lower()}/{self.nome_config(uf)}"

    def nome_config(self, uf: str) -> str:
        return f"{uf.lower()}-p{self.codigo:06d}-cs.json"


@dataclass(frozen=True)
class Secao:
    pleito: Pleito
    uf: str  # maiúscula, como no BU; ZZ é o exterior
    cd_municipio: str  # 5 dígitos, como na config do TSE
    zona: str  # 4 dígitos
    secao: str  # 4 dígitos

    @property
    def url_pasta(self) -> str:
        return (
            f"{self.pleito.url_base}/dados/{self.uf.lower()}/"
            f"{self.cd_municipio}/{self.zona}/{self.secao}"
        )

    @property
    def nome_aux(self) -> str:
        return (
            f"p{self.pleito.codigo:06d}-{self.uf.lower()}"
            f"-m{self.cd_municipio}-z{self.zona}-s{self.secao}-aux.json"
        )

    @property
    def url_aux(self) -> str:
        return f"{self.url_pasta}/{self.nome_aux}"

    @property
    def pasta(self) -> Path:
        """Pasta da seção no bronze, relativa à raiz: uma por município, como no TSE."""
        return self.pleito.particao / self.uf / self.cd_municipio


@dataclass(frozen=True)
class EntradaUrna:
    url: str  # a do aux.json, que é estável; a do BU muda se a urna for trocada
    ano: int
    turno: int
    uf: str
    cd_municipio: str
    zona: str
    secao: str
    hash: str  # pasta do BU escolhido no aux.json
    hashes: int  # quantos hashes o aux.json lista; mais de um é urna trocada
    caminho: str  # do BU
    sha512: str
    bytes: int
    downloaded_at: str
    status: Status
    # "bu" ou "busa"; vazio sem BU escolhido. Nos manifests de antes do campo, sai do caminho.
    tipo_bu: str = ""
    # A situação da seção no aux.json (`st`). Nos manifests de antes do campo, só a "Totalizada"
    # virava success.
    st_secao: str = ""

    def __post_init__(self) -> None:
        if not self.tipo_bu and self.caminho:
            object.__setattr__(self, "tipo_bu", self.caminho.rsplit("-", 1)[1].split(".")[0])
        if not self.st_secao and self.status is Status.SUCCESS:
            object.__setattr__(self, "st_secao", SITUACOES_SECAO[0])

    @property
    def chave(self) -> tuple[int, int, str, str, str, str]:
        return (self.ano, self.turno, self.uf, self.cd_municipio, self.zona, self.secao)


@cache
def _compilado() -> asn1tools.compiler.Specification:
    return asn1tools.compile_files(str(SPEC), codec="ber")


def decodificar_bu(conteudo: bytes) -> dict:
    """Abre o envelope e decodifica o BU, como dict do asn1tools (escolhas viram tuplas)."""
    especificacao = _compilado()
    envelope = especificacao.decode("EntidadeEnvelopeGenerico", conteudo)
    return especificacao.decode("EntidadeBoletimUrna", envelope["conteudo"])


def secoes(pleito: Pleito, config: dict) -> list[Secao]:
    """Cada seção da config de uma UF que tem BU próprio.

    Uma seção agregada a outra (com `nsp`, a principal) votou na urna da principal e não tem
    aux.json; a principal lista as agregadas em `nsa`.
    """
    return [
        Secao(pleito, abrangencia["cd"].upper(), municipio["cd"], zona["cd"], secao["ns"])
        for abrangencia in config["abr"]
        for municipio in abrangencia["mu"]
        for zona in municipio["zon"]
        for secao in zona["sec"]
        if "nsp" not in secao
    ]


def ingerir_urna(
    dir_bronze: Path,
    ano: int = 2026,
    turno: int = 2,
    *,
    ufs: Iterable[str] | None = None,
    force: bool = False,
    workers: int = 16,
    session: requests.Session | None = None,
    tentativas: int = 5,
    espera_base: float = 1.0,
) -> Counter[str]:
    """Baixa o aux.json e o BU de cada seção das UFs pedidas e devolve a contagem dos desfechos.

    A config de cada UF é baixada de novo a cada run (é um JSON só); um manifest por UF
    registra as seções.
    """
    session = session or requests.Session()
    # Um pool maior que o padrão de 10 conexões, senão as threads disputam o socket.
    adaptador = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=workers)
    session.mount("https://", adaptador)
    retry = {"tentativas": tentativas, "espera_base": espera_base}
    pleito = Pleito(ano, turno, PLEITOS[(ano, turno)])
    resumo: Counter[str] = Counter()

    for uf in [uf.upper() for uf in ufs] if ufs else UFS:
        try:
            config = _baixar_config(session, pleito, uf, dir_bronze, retry)
        except (requests.RequestException, ValueError, KeyError) as erro:
            log.error("%s: sem a config de seções (%s), UF pulada", uf, erro)
            resumo[Status.FAILED] += 1
            continue

        manifest = Manifest(dir_bronze / pleito.particao / uf / "manifest.csv", EntradaUrna)
        todas = secoes(pleito, config)
        log.info("%s/%s %s: %d seções", ano, turno, uf, len(todas))

        def processar(secao: Secao, manifest: Manifest = manifest) -> EntradaUrna | None:
            anterior = manifest.get(secao.url_aux)
            if (
                not force
                and anterior is not None
                and anterior.status is Status.SUCCESS
                and (dir_bronze / anterior.caminho).exists()
            ):
                return None
            return _baixar(session, secao, dir_bronze, retry)

        # Os workers só baixam; quem grava o manifest é esta thread, de tempos em tempos.
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for i, entrada in enumerate(executor.map(processar, todas), start=1):
                if entrada is None:
                    resumo[_PULADO] += 1
                    continue
                resumo[entrada.status] += 1
                manifest.registrar(entrada, salvar=i % _SALVAR_A_CADA == 0)
        manifest.salvar()
        log.info("%s/%s %s: %s", ano, turno, uf, dict(resumo))
    return resumo


def _baixar_config(
    session: requests.Session, pleito: Pleito, uf: str, dir_bronze: Path, retry: dict
) -> dict:
    conteudo = com_retry(lambda: _get(session, pleito.url_config(uf)), **retry)
    config = json.loads(conteudo)
    if config["cdp"] != str(pleito.codigo) or [a["cd"].upper() for a in config["abr"]] != [uf]:
        raise ValueError(f"config não é do pleito {pleito.codigo} em {uf}")
    _gravar(dir_bronze / pleito.particao / "config" / pleito.nome_config(uf), conteudo)
    return config


def _baixar(session: requests.Session, secao: Secao, dir_bronze: Path, retry: dict) -> EntradaUrna:
    def entrada(
        status: Status,
        hash_: str = "",
        hashes: int = 0,
        nome_bu: str = "",
        tipo_bu: str = "",
        st_secao: str = "",
        conteudo: bytes = b"",
    ) -> EntradaUrna:
        return EntradaUrna(
            url=secao.url_aux,
            ano=secao.pleito.ano,
            turno=secao.pleito.turno,
            uf=secao.uf,
            cd_municipio=secao.cd_municipio,
            zona=secao.zona,
            secao=secao.secao,
            hash=hash_,
            hashes=hashes,
            caminho=(secao.pasta / nome_bu).as_posix() if nome_bu else "",
            sha512=hashlib.sha512(conteudo).hexdigest() if conteudo else "",
            bytes=len(conteudo),
            downloaded_at=datetime.now(UTC).isoformat(timespec="seconds"),
            status=status,
            tipo_bu=tipo_bu,
            st_secao=st_secao,
        )

    rotulo = f"{secao.uf} {secao.cd_municipio}/{secao.zona}/{secao.secao}"
    try:
        conteudo_aux = com_retry(lambda: _get(session, secao.url_aux), **retry)
    except requests.RequestException as erro:
        log.error("falha no aux.json de %s: %s", rotulo, erro)
        return entrada(Status.FAILED)

    try:
        aux = json.loads(conteudo_aux)
        hashes = aux["hashes"]
        st_secao = aux["st"]
        escolhido = _hash_valido(hashes)
        if st_secao not in SITUACOES_SECAO or escolhido is None:
            log.warning("%s: seção sem BU válido (%s)", rotulo, st_secao)
            return entrada(Status.INCOMPLETE, hashes=len(hashes), st_secao=st_secao)
        tipo_bu, nome_bu = _arquivo_bu(escolhido["arq"])
    except (ValueError, KeyError, TypeError) as erro:
        log.error("aux.json inesperado em %s: %s", rotulo, erro)
        return entrada(Status.CORRUPTED)
    if len(hashes) > 1:
        log.warning("%s: %d hashes no aux.json, usando %s", rotulo, len(hashes), escolhido["hash"])

    url_bu = f"{secao.url_pasta}/{escolhido['hash']}/{nome_bu}"
    resto = {
        "hash_": escolhido["hash"],
        "hashes": len(hashes),
        "nome_bu": nome_bu,
        "tipo_bu": tipo_bu,
        "st_secao": st_secao,
    }
    try:
        conteudo_bu = com_retry(lambda: _get(session, url_bu), **retry)
    except requests.RequestException as erro:
        log.error("falha no BU de %s: %s", rotulo, erro)
        return entrada(Status.FAILED, **resto)

    try:
        _conferir(secao, decodificar_bu(conteudo_bu))
    except (asn1tools.Error, ValueError, KeyError, TypeError) as erro:
        log.error("BU inesperado em %s: %s", rotulo, erro)
        return entrada(Status.CORRUPTED, conteudo=conteudo_bu, **resto)

    _gravar(dir_bronze / secao.pasta / secao.nome_aux, conteudo_aux)
    _gravar(dir_bronze / secao.pasta / nome_bu, conteudo_bu)
    return entrada(Status.SUCCESS, conteudo=conteudo_bu, **resto)


def _arquivo_bu(arquivos: list[dict]) -> tuple[str, str]:
    """O tipo e o nome do BU entre os arquivos de um hash; falha se não houver nenhum."""
    por_tipo = {arq["tp"]: arq["nm"] for arq in arquivos}
    for tipo in TIPOS_BU:
        if tipo in por_tipo:
            return tipo, por_tipo[tipo]
    raise ValueError(f"nenhum BU entre {sorted(por_tipo)}")


def _hash_valido(hashes: list[dict]) -> dict | None:
    """O hash mais recente da melhor situação (Totalizado, senão Recebido).

    Com urna trocada, o aux.json lista mais de um hash.
    """
    for situacao in SITUACOES_HASH:
        candidatos = [h for h in hashes if h["st"] == situacao]
        if candidatos:
            return max(
                candidatos,
                key=lambda h: datetime.strptime(f"{h['dr']} {h['hr']}", "%d/%m/%Y %H:%M:%S"),
            )
    return None


def _conferir(secao: Secao, bu: dict) -> None:
    """Falha se o BU não for o da seção e do pleito pedidos."""
    pleito = bu["cabecalho"]["idEleitoral"]
    identificacao = bu["identificacaoSecao"]
    obtido = (
        pleito,
        identificacao["municipioZona"]["municipio"],
        identificacao["municipioZona"]["zona"],
        identificacao["secao"],
    )
    esperado = (
        ("idPleito", secao.pleito.codigo),
        int(secao.cd_municipio),
        int(secao.zona),
        int(secao.secao),
    )
    if obtido != esperado:
        raise ValueError(f"esperava {esperado}, veio {obtido}")


def _get(session: requests.Session, url: str) -> bytes:
    resposta = session.get(url, timeout=60)
    resposta.raise_for_status()
    return resposta.content


def _gravar(destino: Path, conteudo: bytes) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".part")
    parcial.write_bytes(conteudo)
    os.replace(parcial, destino)
