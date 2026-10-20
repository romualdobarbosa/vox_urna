import hashlib
import logging
import os
import re
from collections import Counter
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import requests

from vox_urna.bronze.ckan import Recurso, resolver
from vox_urna.bronze.http import com_retry
from vox_urna.bronze.manifest import Entrada, Manifest, Status

log = logging.getLogger(__name__)

_SHA512_HEX = re.compile(r"^[0-9a-f]{128}$")
_PULADO = "pulado"


def ingerir(
    dir_bronze: Path,
    anos: Iterable[int],
    *,
    turnos: Iterable[int] | None = None,
    ufs: Iterable[str] | None = None,
    force: bool = False,
    workers: int = 4,
    session: requests.Session | None = None,
    tentativas: int = 5,
    espera_base: float = 1.0,
) -> Counter[str]:
    """Baixa os BUs pedidos para `dir_bronze` e devolve a contagem de cada desfecho."""
    session = session or requests.Session()
    retry = {"tentativas": tentativas, "espera_base": espera_base}
    manifest = Manifest(dir_bronze / "manifest.csv")
    turnos = set(turnos) if turnos else None
    ufs = {uf.upper() for uf in ufs} if ufs else None

    recursos = [
        recurso
        for ano in anos
        for recurso in resolver(session, ano, **retry)
        if (turnos is None or recurso.turno in turnos) and (ufs is None or recurso.uf in ufs)
    ]
    log.info("%d BUs resolvidos no CKAN", len(recursos))

    def processar(recurso: Recurso) -> str:
        anterior = manifest.get(recurso.url)
        if (
            not force
            and anterior is not None
            and anterior.status is Status.SUCCESS
            and (dir_bronze / anterior.caminho).exists()
        ):
            return _PULADO
        entrada = _baixar(session, recurso, dir_bronze, retry)
        manifest.registrar(entrada)
        log.info("%s %s", entrada.status, recurso.arquivo)
        return entrada.status

    with ThreadPoolExecutor(max_workers=workers) as executor:
        return Counter(executor.map(processar, recursos))


def _baixar(session: requests.Session, recurso: Recurso, dir_bronze: Path, retry: dict) -> Entrada:
    caminho = Path(f"ano={recurso.ano}") / f"turno={recurso.turno}" / recurso.arquivo
    destino = dir_bronze / caminho
    parcial = destino.with_name(destino.name + ".part")
    destino.parent.mkdir(parents=True, exist_ok=True)

    def entrada(status: Status, sha512: str = "", tamanho: int = 0) -> Entrada:
        return Entrada(
            url=recurso.url,
            ano=recurso.ano,
            turno=recurso.turno,
            uf=recurso.uf,
            caminho=caminho.as_posix(),
            sha512=sha512,
            bytes=tamanho,
            downloaded_at=datetime.now(UTC).isoformat(timespec="seconds"),
            status=status,
        )

    try:
        oficial = com_retry(lambda: _sha512_oficial(session, recurso.url_sha512), **retry)
        calculado, tamanho = com_retry(lambda: _stream(session, recurso.url, parcial), **retry)
    except (requests.RequestException, ValueError) as erro:
        log.error("falha em %s: %s", recurso.arquivo, erro)
        parcial.unlink(missing_ok=True)
        return entrada(Status.FAILED)

    if calculado != oficial:
        log.error(
            "sha512 não confere em %s: esperado %s, obtido %s", recurso.arquivo, oficial, calculado
        )
        parcial.unlink(missing_ok=True)
        return entrada(Status.CORRUPTED, calculado, tamanho)

    os.replace(parcial, destino)
    return entrada(Status.SUCCESS, calculado, tamanho)


def _sha512_oficial(session: requests.Session, url: str) -> str:
    resposta = session.get(url, timeout=60)
    resposta.raise_for_status()
    # Formato do sha512sum: "<hash>  <arquivo>"
    tokens = resposta.text.split()
    if not tokens or not _SHA512_HEX.match(tokens[0].lower()):
        raise ValueError(f"conteúdo inesperado em {url}: {resposta.text[:80]!r}")
    return tokens[0].lower()


def _stream(session: requests.Session, url: str, parcial: Path) -> tuple[str, int]:
    digest = hashlib.sha512()
    tamanho = 0
    with session.get(url, stream=True, timeout=(30, 300)) as resposta:
        resposta.raise_for_status()
        with parcial.open("wb") as arquivo:
            for bloco in resposta.iter_content(chunk_size=1 << 20):
                arquivo.write(bloco)
                digest.update(bloco)
                tamanho += len(bloco)
    return digest.hexdigest(), tamanho
