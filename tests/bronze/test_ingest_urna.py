import csv
import json
from pathlib import Path

import responses

from vox_urna.bronze.manifest import Manifest, Status
from vox_urna.bronze.urna import EntradaUrna, Pleito, Secao, ingerir_urna, secoes

FIXTURES = Path(__file__).parents[1] / "fixtures" / "urna"
BU = (FIXTURES / "o03221ac0107400040001-bu.dat").read_bytes()
AUX = json.loads((FIXTURES / "p003221-ac-m01074-z0004-s0001-aux.json").read_bytes())
HASH = AUX["hashes"][0]["hash"]

P = Pleito(2026, 2, 3221)
BASE = "https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna/3221"
CONFIG = f"{BASE}/config/ac/ac-p003221-cs.json"
PASTA_1 = f"{BASE}/dados/ac/01074/0004/0001"
AUX_1 = f"{PASTA_1}/p003221-ac-m01074-z0004-s0001-aux.json"
BU_1 = f"{PASTA_1}/{HASH}/o03221ac0107400040001-bu.dat"
PASTA_2 = f"{BASE}/dados/ac/01074/0004/0002"
AUX_2 = f"{PASTA_2}/p003221-ac-m01074-z0004-s0002-aux.json"
BU_2 = f"{PASTA_2}/abc/o03221ac0107400040002-bu.dat"

# Exterior, em Puerto Ordaz (29297/0001/0495): votou em cédula, então o BU é o do Sistema de
# Apuração (busa), sem arquivo "bu".
BUSA = (FIXTURES / "o03221zz2929700010495-busa.dat").read_bytes()
AUX_BUSA = json.loads((FIXTURES / "p003221-zz-m29297-z0001-s0495-aux.json").read_bytes())
PASTA_ZZ = f"{BASE}/dados/zz/29297/0001/0495"
URL_AUX_ZZ = f"{PASTA_ZZ}/p003221-zz-m29297-z0001-s0495-aux.json"
URL_BUSA = f"{PASTA_ZZ}/{AUX_BUSA['hashes'][0]['hash']}/o03221zz2929700010495-busa.dat"


def config(*, com_secao_2: bool = False) -> dict:
    """Recorte da config do AC: a seção do fixture e uma agregada a ela, sem BU próprio."""
    sec = [
        {"ns": "0001", "nsa": ["0003"], "da": "25/10/2026", "ha": "21:26:45"},
        {"ns": "0003", "nsp": "0001"},
    ]
    if com_secao_2:
        sec.append({"ns": "0002", "da": "25/10/2026", "ha": "21:26:45"})
    return {
        "dg": "25/10/2026",
        "hg": "23:22:31",
        "cdp": "3221",
        "abr": [
            {
                "cd": "ac",
                "mu": [
                    {"cd": "01074", "nm": "CRUZEIRO DO SUL", "zon": [{"cd": "0004", "sec": sec}]}
                ],
            }
        ],
    }


def aux(hashes: list[dict], st: str = "Totalizada") -> dict:
    return {"dg": "25/10/2026", "hg": "23:27:51", "st": st, "hashes": hashes}


def hash_(nome: str, hr: str, st: str = "Totalizado", secao: str = "0001") -> dict:
    bu = f"o03221ac010740004{secao}-bu.dat"
    return {"hash": nome, "dr": "25/10/2026", "hr": hr, "st": st, "arq": [{"nm": bu, "tp": "bu"}]}


def registrar_tse(*, aux_1: dict | int = AUX, bu_1: bytes | int = BU) -> None:
    responses.get(CONFIG, json=config())
    for url, corpo in ((AUX_1, aux_1), (BU_1, bu_1)):
        if isinstance(corpo, int):
            responses.get(url, status=corpo)
        elif isinstance(corpo, bytes):
            responses.get(url, body=corpo)
        else:
            responses.get(url, json=corpo)


def chamadas_a(url: str) -> int:
    return sum(1 for chamada in responses.calls if chamada.request.url == url)


def manifest(tmp_path) -> Manifest:
    return Manifest(tmp_path / "ano=2026/turno=2/AC/manifest.csv", EntradaUrna)


def rodar(tmp_path, **kwargs):
    return ingerir_urna(tmp_path, 2026, 2, ufs=["ac"], espera_base=0, workers=1, **kwargs)


def test_urls_seguem_o_padrao_do_tse():
    secao = Secao(P, "AC", "01074", "0004", "0001")
    assert P.url_config("AC") == CONFIG
    assert secao.url_aux == AUX_1
    assert secao.pasta.as_posix() == "ano=2026/turno=2/AC/01074"


def test_secao_agregada_fica_de_fora():
    assert secoes(P, config()) == [Secao(P, "AC", "01074", "0004", "0001")]


@responses.activate
def test_baixa_config_aux_e_bu_e_registra_no_manifest(tmp_path):
    registrar_tse()

    assert rodar(tmp_path) == {Status.SUCCESS: 1}

    pasta = tmp_path / "ano=2026/turno=2/AC/01074"
    assert (pasta / "o03221ac0107400040001-bu.dat").read_bytes() == BU
    assert json.loads((pasta / "p003221-ac-m01074-z0004-s0001-aux.json").read_bytes()) == AUX
    assert (tmp_path / "ano=2026/turno=2/config/ac-p003221-cs.json").exists()
    entrada = manifest(tmp_path).get(AUX_1)
    assert entrada.status is Status.SUCCESS
    assert (entrada.cd_municipio, entrada.zona, entrada.secao) == ("01074", "0004", "0001")
    assert (entrada.hash, entrada.hashes, entrada.bytes) == (HASH, 1, len(BU))
    assert entrada.caminho == "ano=2026/turno=2/AC/01074/o03221ac0107400040001-bu.dat"
    assert (entrada.tipo_bu, entrada.st_secao) == ("bu", "Totalizada")


@responses.activate
def test_rerun_nao_baixa_de_novo_e_force_baixa(tmp_path):
    registrar_tse()
    rodar(tmp_path)

    assert rodar(tmp_path) == {"pulado": 1}
    assert chamadas_a(BU_1) == 1
    assert rodar(tmp_path, force=True) == {Status.SUCCESS: 1}
    assert chamadas_a(BU_1) == 2


@responses.activate
def test_secao_nao_totalizada_marca_incomplete_e_nao_fica_no_disco(tmp_path):
    registrar_tse(aux_1=aux([hash_(HASH, "20:31:31", st="Recebido")], st="Não totalizada"))

    assert rodar(tmp_path) == {Status.INCOMPLETE: 1}
    assert chamadas_a(BU_1) == 0
    assert not (tmp_path / "ano=2026/turno=2/AC/01074").exists()


@responses.activate
def test_com_urna_trocada_usa_o_hash_totalizado_mais_recente(tmp_path):
    hashes = [
        hash_("velho", "18:00:00"),
        hash_(HASH, "20:31:31"),
        hash_("excluido", "21:00:00", st="Excluído"),
    ]
    registrar_tse(aux_1=aux(hashes))

    assert rodar(tmp_path) == {Status.SUCCESS: 1}
    entrada = manifest(tmp_path).get(AUX_1)
    assert (entrada.hash, entrada.hashes) == (HASH, 3)


@responses.activate
def test_bu_de_outra_secao_marca_corrupted_e_nao_fica_no_disco(tmp_path):
    responses.get(CONFIG, json=config(com_secao_2=True))
    responses.get(AUX_1, json=AUX)
    responses.get(BU_1, body=BU)
    responses.get(AUX_2, json=aux([hash_("abc", "20:00:00", secao="0002")]))
    responses.get(BU_2, body=BU)  # o BU da seção 1 publicado na pasta da 2

    assert rodar(tmp_path) == {Status.SUCCESS: 1, Status.CORRUPTED: 1}
    assert manifest(tmp_path).get(AUX_2).status is Status.CORRUPTED
    assert not (tmp_path / "ano=2026/turno=2/AC/01074/o03221ac0107400040002-bu.dat").exists()


@responses.activate
def test_bu_invalido_marca_corrupted(tmp_path):
    registrar_tse(bu_1=b"isto nao e asn1")

    assert rodar(tmp_path) == {Status.CORRUPTED: 1}


@responses.activate
def test_erro_transitorio_e_repetido(tmp_path):
    registrar_tse(bu_1=503)
    responses.get(BU_1, body=BU)

    assert rodar(tmp_path) == {Status.SUCCESS: 1}
    assert chamadas_a(BU_1) == 2


@responses.activate
def test_aux_inexistente_marca_failed_sem_repetir(tmp_path):
    registrar_tse(aux_1=404)

    assert rodar(tmp_path) == {Status.FAILED: 1}
    assert chamadas_a(AUX_1) == 1


@responses.activate
def test_sem_a_config_a_uf_e_pulada(tmp_path):
    responses.get(CONFIG, status=404)

    assert rodar(tmp_path) == {Status.FAILED: 1}
    assert chamadas_a(AUX_1) == 0


def registrar_tse_zz(aux_zz: dict = AUX_BUSA) -> None:
    secao = {"ns": "0495", "da": "25/10/2026", "ha": "19:25:12"}
    municipio = {"cd": "29297", "nm": "PUERTO ORDAZ", "zon": [{"cd": "0001", "sec": [secao]}]}
    responses.get(
        f"{BASE}/config/zz/zz-p003221-cs.json",
        json={"cdp": "3221", "abr": [{"cd": "zz", "mu": [municipio]}]},
    )
    responses.get(URL_AUX_ZZ, json=aux_zz)
    responses.get(URL_BUSA, body=BUSA)


@responses.activate
def test_secao_em_cedula_usa_o_busa_e_registra_o_tipo(tmp_path):
    registrar_tse_zz()

    assert ingerir_urna(tmp_path, 2026, 2, ufs=["zz"], workers=1) == {Status.SUCCESS: 1}

    entrada = Manifest(tmp_path / "ano=2026/turno=2/ZZ/manifest.csv", EntradaUrna).get(URL_AUX_ZZ)
    assert (entrada.status, entrada.tipo_bu) == (Status.SUCCESS, "busa")
    assert (tmp_path / entrada.caminho).read_bytes() == BUSA


@responses.activate
def test_hash_sem_bu_nem_busa_marca_corrupted(tmp_path):
    [hash_zz] = AUX_BUSA["hashes"]
    so_log = [arq for arq in hash_zz["arq"] if arq["tp"] not in ("bu", "busa")]
    registrar_tse_zz(AUX_BUSA | {"hashes": [hash_zz | {"arq": so_log}]})

    assert ingerir_urna(tmp_path, 2026, 2, ufs=["zz"], workers=1) == {Status.CORRUPTED: 1}


@responses.activate
def test_manifest_de_antes_dos_campos_novos_deduz_tipo_e_situacao(tmp_path):
    registrar_tse()
    rodar(tmp_path)
    caminho = tmp_path / "ano=2026/turno=2/AC/manifest.csv"
    with caminho.open(newline="") as arquivo:
        linhas = list(csv.DictReader(arquivo))
    with caminho.open("w", newline="") as arquivo:
        colunas = [c for c in linhas[0] if c not in ("tipo_bu", "st_secao")]
        escritor = csv.DictWriter(arquivo, fieldnames=colunas, extrasaction="ignore")
        escritor.writeheader()
        escritor.writerows(linhas)

    entrada = manifest(tmp_path).get(AUX_1)
    assert (entrada.tipo_bu, entrada.st_secao) == ("bu", "Totalizada")


@responses.activate
def test_secao_recebida_usa_o_hash_recebido_e_registra_a_situacao(tmp_path):
    registrar_tse(aux_1=aux([hash_(HASH, "11:28:16", st="Recebido")], st="Recebida"))

    assert rodar(tmp_path) == {Status.SUCCESS: 1}
    entrada = manifest(tmp_path).get(AUX_1)
    assert (entrada.hash, entrada.st_secao) == (HASH, "Recebida")
    assert (tmp_path / entrada.caminho).read_bytes() == BU


@responses.activate
def test_hash_totalizado_tem_precedencia_sobre_recebido_mais_recente(tmp_path):
    hashes = [hash_(HASH, "20:31:31"), hash_("recebido", "23:00:00", st="Recebido")]
    registrar_tse(aux_1=aux(hashes))

    assert rodar(tmp_path) == {Status.SUCCESS: 1}
    assert manifest(tmp_path).get(AUX_1).hash == HASH


@responses.activate
def test_secao_nao_instalada_continua_incomplete_com_a_situacao(tmp_path):
    registrar_tse(aux_1=aux([], st="Nao instalada"))

    assert rodar(tmp_path) == {Status.INCOMPLETE: 1}
    assert manifest(tmp_path).get(AUX_1).st_secao == "Nao instalada"
