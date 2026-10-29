import hashlib
import json
import shutil
from pathlib import Path

import duckdb
import pytest
import responses

from vox_urna.bronze.manifest import Entrada, Manifest, Status
from vox_urna.bronze.urna import EntradaUrna, decodificar_bu, ingerir_urna
from vox_urna.silver.conferencia import Diferenca, conferir_secoes
from vox_urna.silver.construcao import _carregar, construir, construir_fonte
from vox_urna.silver.urna import extrair_presidente_urna, linhas_presidente

FIXTURES = Path(__file__).parents[1] / "fixtures" / "urna"
BU = (FIXTURES / "o03221ac0107400040001-bu.dat").read_bytes()
AUX = json.loads((FIXTURES / "p003221-ac-m01074-z0004-s0001-aux.json").read_bytes())
BASE = "https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna/3221"
PASTA = f"{BASE}/dados/ac/01074/0004/0001"
BWEB_AC_2026 = Path(__file__).parents[1] / "fixtures" / "bu" / "bweb_1t_AC_051020261403.zip"
# Exterior, votação em cédula: Puerto Ordaz teve votos; Ciudad Guayana, só abstenção.
BUSA_COM_VOTOS = (FIXTURES / "o03221zz2929700010495-busa.dat").read_bytes()
BUSA_SEM_VOTOS = (FIXTURES / "o03221zz2956400010091-busa.dat").read_bytes()
CANDIDATOS = {13: ("LULA", "PT"), 22: ("FLAVIO BOLSONARO", "PL")}
CONFIG = {
    "cdp": "3221",
    "abr": [
        {
            "cd": "ac",
            "mu": [
                {
                    "cd": "01074",
                    "nm": "CRUZEIRO DO SUL",
                    "zon": [{"cd": "0004", "sec": [{"ns": "0001", "da": "", "ha": ""}]}],
                }
            ],
        }
    ],
}


@responses.activate
def montar_bronze_urna(bronze: Path) -> None:
    """Ingere a seção de fixture (AC, Cruzeiro do Sul, 4/1) no bronze dos arquivos de urna."""
    responses.get(f"{BASE}/config/ac/ac-p003221-cs.json", json=CONFIG)
    responses.get(f"{PASTA}/p003221-ac-m01074-z0004-s0001-aux.json", json=AUX)
    responses.get(f"{PASTA}/{AUX['hashes'][0]['hash']}/o03221ac0107400040001-bu.dat", body=BU)
    ingerir_urna(bronze, 2026, 2, ufs=["AC"], workers=1)


def montar_resultado_votos(dados: Path, candidatos=CANDIDATOS) -> None:
    """O silver `resultado_votos` do Brasil em 2026/2, de onde vêm os nomes."""
    destino = dados / "silver" / "resultado_votos" / "ano=2026" / "turno=2"
    destino.mkdir(parents=True)
    valores = ", ".join(
        f"('brasil', {n}, '{nome}', '{sg}')" for n, (nome, sg) in candidatos.items()
    )
    duckdb.execute(
        f"copy (select * from (values {valores}) t(nivel, nr_votavel, nm_votavel, sg_partido)) "
        f"to '{destino / 'dados.parquet'}' (format parquet)"
    )


def montar_ckan_2t(dados: Path) -> None:
    """Um zip do CKAN registrado como 2026/2 (o conteúdo é o do 1º turno do AC)."""
    caminho = Path("ano=2026") / "turno=2" / "bweb_2t_AC_271020261800.zip"
    destino = dados / "bronze" / caminho
    destino.parent.mkdir(parents=True)
    shutil.copy(BWEB_AC_2026, destino)
    Manifest(dados / "bronze" / "manifest.csv").registrar(
        Entrada(
            url="https://cdn.tse.jus.br/bweb_2t_AC_271020261800.zip",
            ano=2026,
            turno=2,
            uf="AC",
            caminho=caminho.as_posix(),
            sha512=hashlib.sha512(destino.read_bytes()).hexdigest(),
            bytes=destino.stat().st_size,
            downloaded_at="2026-10-27T00:00:00+00:00",
            status=Status.SUCCESS,
        )
    )


def ler(silver: Path, tabela: str) -> list[tuple]:
    return duckdb.sql(
        f"select * from read_parquet('{silver}/{tabela}/ano=2026/turno=2/*.parquet', "
        "hive_partitioning = false) order by all"
    ).fetchall()


def test_linhas_de_presidente_do_bu_no_formato_do_bweb():
    linhas = linhas_presidente(decodificar_bu(BU), "AC", "CRUZEIRO DO SUL", 6258, ["0003"])

    votos = {(linha["CD_TIPO_VOTAVEL"], linha["NR_VOTAVEL"]): linha["QT_VOTOS"] for linha in linhas}
    assert votos == {(1, 13): 68, (1, 22): 127, (2, 95): 3, (3, 96): 4}
    comum = {
        (linha["CD_MUNICIPIO"], linha["NR_ZONA"], linha["NR_SECAO"], linha["CD_CARGO_PERGUNTA"])
        for linha in linhas
    }
    assert comum == {(1074, 4, 1, 1)}
    [totais] = {
        (linha["QT_APTOS"], linha["QT_COMPARECIMENTO"], linha["QT_ABSTENCOES"]) for linha in linhas
    }
    assert totais == (262, 202, 60)
    assert {linha["DS_SECOES_AGREGADAS"] for linha in linhas} == {"3"}


def test_bu_sem_secao_agregada_fica_com_nulo_como_no_bweb():
    linhas = linhas_presidente(decodificar_bu(BU), "AC", "CRUZEIRO DO SUL", 6258)
    assert {linha["DS_SECOES_AGREGADAS"] for linha in linhas} == {"#NULO#"}


def test_csv_extraido_passa_pelo_silver_sem_quarentena(tmp_path):
    bronze = tmp_path / "bronze"
    montar_bronze_urna(bronze)

    staging = tmp_path / "staging"
    assert extrair_presidente_urna(bronze, 2026, 2, "AC", staging / "AC.csv") == 4

    con = duckdb.connect()
    _carregar(con, staging)
    assert con.execute(
        "select uf, cd_municipio, nm_municipio, zona, secao, aptos, comparecimento, abstencao,"
        " branco, nulo, nominais, motivo from secoes"
    ).fetchall() == [("AC", 1074, "CRUZEIRO DO SUL", 4, 1, 262, 202, 60, 3, 4, 195, None)]


def test_nome_e_partido_dos_nominais_vem_dos_candidatos():
    linhas = linhas_presidente(decodificar_bu(BU), "AC", "CRUZEIRO DO SUL", 6258, None, CANDIDATOS)

    nomes = {(linha["NR_VOTAVEL"], linha["NM_VOTAVEL"], linha["SG_PARTIDO"]) for linha in linhas}
    assert nomes == {
        (13, "LULA", "PT"),
        (22, "FLAVIO BOLSONARO", "PL"),
        (95, "Branco", ""),
        (96, "Nulo", ""),
    }


def test_candidato_fora_dos_divulgados_falha():
    with pytest.raises(ValueError, match="candidato 22"):
        linhas_presidente(decodificar_bu(BU), "AC", "X", 6258, None, {13: ("LULA", "PT")})


def test_sem_ckan_o_turno_vem_dos_arquivos_de_urna_com_os_nomes(tmp_path):
    montar_bronze_urna(tmp_path / "bronze" / "urna")
    montar_resultado_votos(tmp_path)

    [construcao] = construir(tmp_path, workers=1)

    assert (construcao.ano, construcao.turno, construcao.fonte) == (2026, 2, "urna")
    assert (construcao.secoes, construcao.rejeitadas) == (1, 0)
    assert ler(tmp_path / "silver", "votos") == [
        ("AC", 1074, 4, 1, 13, "LULA", "PT", 68),
        ("AC", 1074, 4, 1, 22, "FLAVIO BOLSONARO", "PL", 127),
    ]
    assert [linha[-1] for linha in ler(tmp_path / "silver", "secao")] == ["Totalizada"]
    registro = json.loads((tmp_path / "silver" / "_construcao.json").read_text())
    assert registro["2026-2"]["fonte"] == "urna"
    assert construir(tmp_path, workers=1)[0].pulado


def test_ckan_com_o_turno_tem_precedencia_sobre_os_arquivos_de_urna(tmp_path):
    montar_bronze_urna(tmp_path / "bronze" / "urna")
    montar_resultado_votos(tmp_path)
    construir(tmp_path, workers=1)

    montar_ckan_2t(tmp_path)
    [construcao] = construir(tmp_path, workers=1)

    assert (construcao.fonte, construcao.pulado) == ("ckan", False)
    assert construcao.secoes > 1
    # O bweb não tem a situação da seção.
    assert {linha[-1] for linha in ler(tmp_path / "silver", "secao")} == {None}
    registro = json.loads((tmp_path / "silver" / "_construcao.json").read_text())
    assert registro["2026-2"]["fonte"] == "ckan"


def test_arquivos_de_urna_sem_resultados_divulgados_falham(tmp_path):
    montar_bronze_urna(tmp_path / "bronze" / "urna")

    with pytest.raises(RuntimeError, match="resultado_votos"):
        construir(tmp_path, workers=1)


def test_conferencia_entre_fontes_aponta_cada_secao_e_campo_divergente(tmp_path):
    montar_bronze_urna(tmp_path / "bronze" / "urna")
    montar_resultado_votos(tmp_path)
    a, b = tmp_path / "conferencia" / "urna", tmp_path / "conferencia" / "outra"
    construir_fonte(tmp_path, a, 2026, 2, "urna", workers=1)
    construir_fonte(tmp_path, b, 2026, 2, "urna", workers=1)
    assert conferir_secoes(a, b, 2026, 2) == []
    assert not (tmp_path / "silver" / "_construcao.json").exists()

    # Na outra fonte, um voto passa do Lula para o nulo e aparece uma seção a mais.
    for tabela, consulta in {
        "secao": "select * replace (nulo + 1 as nulo, nominais - 1 as nominais) from t "
        "union all select * replace (2 as secao) from t",
        "votos": "select * replace (if(nr_votavel = 13, votos - 1, votos) as votos) from t",
    }.items():
        arquivo = b / tabela / "ano=2026" / "turno=2" / "dados.parquet"
        duckdb.execute(f"create or replace table t as select * from '{arquivo}'")
        duckdb.execute(f"copy ({consulta}) to '{arquivo}' (format parquet)")

    diferencas = conferir_secoes(a, b, 2026, 2)

    assert [d for d in diferencas if d.secao == 1] == [
        Diferenca("AC", 1074, 4, 1, "nominais", 195, 194),
        Diferenca("AC", 1074, 4, 1, "nulo", 4, 5),
        Diferenca("AC", 1074, 4, 1, "votos_13", 68, 67),
    ]
    assert {(d.secao, d.valor_a) for d in diferencas if d.secao == 2} == {(2, None)}


def test_busa_vira_as_mesmas_linhas_do_bu():
    linhas = linhas_presidente(decodificar_bu(BUSA_COM_VOTOS), "ZZ", "PUERTO ORDAZ", 6258)

    votos = {(linha["CD_TIPO_VOTAVEL"], linha["NR_VOTAVEL"]): linha["QT_VOTOS"] for linha in linhas}
    assert votos == {(1, 13): 29, (1, 22): 8, (3, 96): 2}
    assert {(linha["QT_APTOS"], linha["QT_COMPARECIMENTO"]) for linha in linhas} == {(77, 39)}


def test_bu_sem_voto_de_presidente_nao_gera_linha():
    assert linhas_presidente(decodificar_bu(BUSA_SEM_VOTOS), "ZZ", "CIUDAD GUAYANA", 6258) == []


def test_secao_sem_voto_de_presidente_fica_fora_do_silver(tmp_path):
    bronze = tmp_path / "bronze"
    particao = bronze / "ano=2026" / "turno=2"
    municipios = []
    manifest = Manifest(particao / "ZZ" / "manifest.csv", EntradaUrna)
    for (cd, nm, ns), conteudo in {
        ("29297", "PUERTO ORDAZ", "0495"): BUSA_COM_VOTOS,
        ("29564", "CIUDAD GUAYANA", "0091"): BUSA_SEM_VOTOS,
    }.items():
        municipios.append({"cd": cd, "nm": nm, "zon": [{"cd": "0001", "sec": [{"ns": ns}]}]})
        caminho = Path("ano=2026/turno=2/ZZ") / cd / f"o03221zz{cd}0001{ns}-busa.dat"
        (bronze / caminho).parent.mkdir(parents=True)
        (bronze / caminho).write_bytes(conteudo)
        manifest.registrar(
            EntradaUrna(
                url=f"https://exemplo/{cd}",
                ano=2026,
                turno=2,
                uf="ZZ",
                cd_municipio=cd,
                zona="0001",
                secao=ns,
                hash="h",
                hashes=1,
                caminho=caminho.as_posix(),
                sha512="",
                bytes=len(conteudo),
                downloaded_at="2026-10-28T00:00:00+00:00",
                status=Status.SUCCESS,
            )
        )
    (particao / "config").mkdir()
    (particao / "config" / "zz-p003221-cs.json").write_text(
        json.dumps({"cdp": "3221", "abr": [{"cd": "zz", "mu": municipios}]})
    )

    staging = tmp_path / "staging"
    extrair_presidente_urna(bronze, 2026, 2, "ZZ", staging / "ZZ.csv")
    con = duckdb.connect()
    _carregar(con, staging)

    assert con.execute(
        "select cd_municipio, aptos, comparecimento, nominais, nulo, motivo from secoes"
    ).fetchall() == [(29297, 77, 39, 37, 2, None)]
