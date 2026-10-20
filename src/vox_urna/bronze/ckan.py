import re
from dataclasses import dataclass

import requests

from vox_urna.bronze.http import com_retry

CKAN_API = "https://dadosabertos.tse.jus.br/api/3/action"

# BWEB_1t_AC_101020181938.zip (2018) e bweb_1t_AC_051020261403.zip (2022+); o exterior é ZZ.
_ARQUIVO_BU = re.compile(r"^bweb_(?P<turno>[12])t_(?P<uf>[A-Z]{2})_\d+\.zip$", re.IGNORECASE)


@dataclass(frozen=True)
class Recurso:
    ano: int
    turno: int
    uf: str
    url: str

    @property
    def url_sha512(self) -> str:
        return self.url + ".sha512"

    @property
    def arquivo(self) -> str:
        return self.url.rsplit("/", 1)[1]


def dataset_id(ano: int) -> str:
    return f"resultados-{ano}-boletim-de-urna"


def resolver(session: requests.Session, ano: int, **retry) -> list[Recurso]:
    """Lista os zips de BU de um ano pela API CKAN, exigindo o .sha512 oficial de cada um."""

    def chamar() -> dict:
        resposta = session.get(
            f"{CKAN_API}/package_show", params={"id": dataset_id(ano)}, timeout=60
        )
        resposta.raise_for_status()
        return resposta.json()

    corpo = com_retry(chamar, **retry)
    if not corpo.get("success"):
        raise RuntimeError(f"CKAN recusou {dataset_id(ano)}: {corpo.get('error')}")

    urls = {recurso["url"] for recurso in corpo["result"]["resources"]}
    recursos = []
    for url in sorted(urls):
        casamento = _ARQUIVO_BU.match(url.rsplit("/", 1)[1])
        if casamento is None:
            continue
        if url + ".sha512" not in urls:
            raise ValueError(f"BU sem .sha512 oficial no CKAN: {url}")
        recursos.append(Recurso(ano, int(casamento["turno"]), casamento["uf"].upper(), url))
    return recursos
