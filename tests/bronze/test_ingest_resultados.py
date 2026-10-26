import json

import pytest
import requests
import responses

from vox_urna.bronze.manifest import Manifest, Status
from vox_urna.bronze.resultados import (
    URL_ELEICOES,
    Arquivo,
    Eleicao,
    EntradaResultado,
    ingerir_resultados,
    resolver_eleicao,
)

T2 = Eleicao(2026, 2, 6258)
BASE_T2 = "https://resultados.tse.jus.br/oficial/ele2026/6258"
CONFIG = f"{BASE_T2}/config/mun-e006258-cm.json"
BR = f"{BASE_T2}/dados/br/br-c0001-e006258-u.json"
AC = f"{BASE_T2}/dados/ac/ac-c0001-e006258-u.json"
ZZ = f"{BASE_T2}/dados/zz/zz-c0001-e006258-u.json"
ACRELANDIA = f"{BASE_T2}/dados/ac/ac01120-c0001-e006258-u.json"
BUJARI = f"{BASE_T2}/dados/ac/ac01007-c0001-e006258-u.json"
ABU_DHABI = f"{BASE_T2}/dados/zz/zz29262-c0001-e006258-u.json"


def config_eleicoes() -> dict:
    """Recorte da config do TSE: o 1º turno, o 2º e uma eleição que não é de Presidente."""

    def eleicao(cd: str, turno: str, cargos: list[str]) -> dict:
        return {"cd": cd, "t": turno, "abr": [{"cd": "br", "cp": [{"cd": c} for c in cargos]}]}

    return {
        "pl": [
            {"c": "ele2026", "e": [eleicao("6257", "1", ["1"]), eleicao("6259", "1", ["3"])]},
            {"c": "ele2026", "e": [eleicao("6284", "1", ["11"]), eleicao("6258", "2", ["1"])]},
            {"c": "ele2024", "e": [eleicao("619", "1", ["11"])]},
        ]
    }


def lista_municipios() -> dict:
    return {
        "dg": "25/10/2026",
        "hg": "09:00:00",
        "abr": [
            {"cd": "ac", "mu": [{"cd": "01120", "cdi": "1200013", "nm": "ACRELÂNDIA"},
                                {"cd": "01007", "cdi": "1200138", "nm": "BUJARI"}]},
            {"cd": "zz", "mu": [{"cd": "29262", "cdi": "", "nm": "ABU DHABI"}]},
        ],
    }  # fmt: skip


def resultado(tpabr: str, cdabr: str, *, tf: str = "s", ele: str = "6258") -> dict:
    return {
        "ele": ele,
        "t": "2",
        "tpabr": tpabr,
        "cdabr": cdabr,
        "tf": tf,
        "dg": "25/10/2026",
        "hg": "23:27:02",
        "carg": [{"cd": "1"}],
    }


RESULTADOS = {
    BR: resultado("br", "br"),
    AC: resultado("uf", "ac"),
    ZZ: resultado("uf", "zz"),
    ACRELANDIA: resultado("mu", "01120"),
    BUJARI: resultado("mu", "01007"),
    ABU_DHABI: resultado("mu", "29262"),
}


def registrar_tse(**trocas) -> None:
    responses.get(URL_ELEICOES, json=config_eleicoes())
    responses.get(CONFIG, json=lista_municipios())
    for url, corpo in {**RESULTADOS, **trocas}.items():
        if isinstance(corpo, int):
            responses.get(url, status=corpo)
        else:
            responses.get(url, json=corpo)


def chamadas_a(url: str) -> int:
    return sum(1 for chamada in responses.calls if chamada.request.url == url)


def manifest(tmp_path) -> Manifest:
    return Manifest(tmp_path / "manifest.csv", EntradaResultado)


def rodar(tmp_path, **kwargs):
    return ingerir_resultados(tmp_path, [2026], turnos=[2], espera_base=0, workers=1, **kwargs)


@responses.activate
def test_resolver_acha_a_eleicao_de_presidente_de_cada_turno():
    responses.get(URL_ELEICOES, json=config_eleicoes())
    session = requests.Session()
    assert resolver_eleicao(session, 2026, 1) == Eleicao(2026, 1, 6257)
    assert resolver_eleicao(session, 2026, 2) == T2


@responses.activate
def test_resolver_falha_se_nao_houver_eleicao_de_presidente():
    responses.get(URL_ELEICOES, json=config_eleicoes())
    with pytest.raises(ValueError, match="Presidente"):
        resolver_eleicao(requests.Session(), 2024, 1)


def test_urls_seguem_o_padrao_do_tse():
    assert Arquivo(T2, "config").url == CONFIG
    assert Arquivo(T2, "brasil").url == BR
    assert Arquivo(T2, "uf", "AC").url == AC
    assert Arquivo(T2, "municipio", "ZZ", "29262").url == ABU_DHABI
    assert Arquivo(T2, "municipio", "AC", "01120").caminho.as_posix() == (
        "ano=2026/turno=2/ac01120-c0001-e006258-u.json"
    )


@responses.activate
def test_baixa_lista_brasil_ufs_e_municipios_e_registra_no_manifest(tmp_path):
    registrar_tse()

    assert rodar(tmp_path) == {Status.SUCCESS: 7}

    arquivo = tmp_path / "ano=2026/turno=2/ac01120-c0001-e006258-u.json"
    assert json.loads(arquivo.read_bytes()) == RESULTADOS[ACRELANDIA]
    entrada = manifest(tmp_path).get(ACRELANDIA)
    assert entrada.status is Status.SUCCESS
    assert (entrada.abrangencia, entrada.uf, entrada.cd_municipio) == ("municipio", "AC", "01120")
    assert entrada.gerado_em == "2026-10-25T23:27:02-03:00"
    assert entrada.bytes == arquivo.stat().st_size
    assert manifest(tmp_path).get(CONFIG).abrangencia == "config"


@responses.activate
def test_rerun_nao_baixa_de_novo(tmp_path):
    registrar_tse()
    rodar(tmp_path)

    assert rodar(tmp_path) == {"pulado": 7}
    assert chamadas_a(ACRELANDIA) == 1


@responses.activate
def test_force_baixa_de_novo(tmp_path):
    registrar_tse()
    rodar(tmp_path)

    assert rodar(tmp_path, force=True) == {Status.SUCCESS: 7}
    assert chamadas_a(ACRELANDIA) == 2


@responses.activate
def test_filtra_por_uf_sem_o_brasil(tmp_path):
    registrar_tse()

    assert rodar(tmp_path, ufs=["zz"]) == {Status.SUCCESS: 3}
    assert chamadas_a(BR) == chamadas_a(AC) == chamadas_a(ACRELANDIA) == 0
    assert chamadas_a(ABU_DHABI) == 1


@responses.activate
def test_arquivo_de_outro_municipio_marca_corrupted_e_nao_fica_no_disco(tmp_path):
    registrar_tse(**{BUJARI: resultado("mu", "01120")})

    assert rodar(tmp_path) == {Status.SUCCESS: 6, Status.CORRUPTED: 1}
    assert manifest(tmp_path).get(BUJARI).status is Status.CORRUPTED
    assert not (tmp_path / "ano=2026/turno=2/ac01007-c0001-e006258-u.json").exists()


@responses.activate
def test_arquivo_de_outra_eleicao_marca_corrupted(tmp_path):
    registrar_tse(**{BR: resultado("br", "br", ele="6257")})

    assert rodar(tmp_path) == {Status.SUCCESS: 6, Status.CORRUPTED: 1}


@responses.activate
def test_json_invalido_marca_corrupted(tmp_path):
    registrar_tse(**{AC: {"ele": "6258"}})

    assert rodar(tmp_path) == {Status.SUCCESS: 6, Status.CORRUPTED: 1}


@responses.activate
def test_totalizacao_nao_final_marca_incomplete_e_baixa_de_novo_depois(tmp_path):
    registrar_tse(**{AC: resultado("uf", "ac", tf="n")})
    assert rodar(tmp_path) == {Status.SUCCESS: 6, Status.INCOMPLETE: 1}
    assert not (tmp_path / "ano=2026/turno=2/ac-c0001-e006258-u.json").exists()

    responses.reset()
    registrar_tse()
    assert rodar(tmp_path) == {"pulado": 6, Status.SUCCESS: 1}


@responses.activate
def test_erro_transitorio_e_repetido(tmp_path):
    registrar_tse(**{ACRELANDIA: 503})
    responses.get(ACRELANDIA, json=RESULTADOS[ACRELANDIA])

    assert rodar(tmp_path) == {Status.SUCCESS: 7}
    assert chamadas_a(ACRELANDIA) == 2


@responses.activate
def test_municipio_inexistente_marca_failed_sem_repetir(tmp_path):
    registrar_tse(**{ABU_DHABI: 404})

    assert rodar(tmp_path) == {Status.SUCCESS: 6, Status.FAILED: 1}
    assert chamadas_a(ABU_DHABI) == 1


@responses.activate
def test_sem_a_lista_de_municipios_o_turno_e_pulado(tmp_path):
    responses.get(URL_ELEICOES, json=config_eleicoes())
    responses.get(CONFIG, status=404)

    assert rodar(tmp_path) == {Status.FAILED: 1}
    assert chamadas_a(BR) == 0
