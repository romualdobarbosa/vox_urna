import hashlib
import json
from pathlib import Path

import duckdb
import pytest

from vox_urna.bronze.manifest import Manifest, Status
from vox_urna.bronze.resultados import Arquivo, Eleicao, EntradaResultado
from vox_urna.silver.resultados import (
    ResultadoIncompleto,
    ResultadoInconsistente,
    construir_resultados,
)

T2 = Eleicao(2026, 2, 6258)

LISTA = {
    "dg": "15/10/2026",
    "hg": "09:38:00",
    "abr": [
        {"cd": "ac", "mu": [{"cd": "01120", "cdi": "1200013", "nm": "ACRELÂNDIA"},
                            {"cd": "01007", "cdi": "1200138", "nm": "BUJARI"}]},
        {"cd": "zz", "mu": [{"cd": "29262", "cdi": "", "nm": "ABU DHABI"}]},
    ],
}  # fmt: skip

# Por município: abstenção, branco, nulo, nulo técnico, Lula, Flávio e os eleitores de seções
# não instaladas, que contam no eleitorado mas não nos aptos.
MUNICIPIOS = {
    ("AC", "01120"): (2495, 57, 200, 58, 1647, 5751, 3),
    ("AC", "01007"): (3239, 80, 150, 0, 2100, 5584, 0),
    ("ZZ", "29262"): (900, 5, 10, 0, 300, 250, 0),
}


def contagem(abstencao, branco, nulo, nulo_tecnico, lula, flavio, nao_instalados) -> dict:
    return {
        "nao_instalados": nao_instalados,
        "abstencao": abstencao,
        "branco": branco,
        "nulo": nulo,
        "nulo_tecnico": nulo_tecnico,
        "lula": lula,
        "flavio": flavio,
    }


def somar(*contagens: dict) -> dict:
    return {campo: sum(c[campo] for c in contagens) for campo in contagens[0]}


def resultado_json(tpabr: str, cdabr: str, c: dict) -> dict:
    validos = c["lula"] + c["flavio"]
    comparecimento = validos + c["branco"] + c["nulo"] + c["nulo_tecnico"]
    aptos = comparecimento + c["abstencao"]

    return {
        "ele": "6258", "t": "2", "tpabr": tpabr, "cdabr": cdabr, "tf": "s",
        "dg": "25/10/2026", "hg": "23:27:02",
        "e": {"te": str(aptos + c["nao_instalados"]), "esa": str(aptos), "c": str(comparecimento),
              "a": str(c["abstencao"])},
        "v": {"vv": str(validos), "vb": str(c["branco"]), "vn": str(c["nulo"]),
              "vnt": str(c["nulo_tecnico"]), "van": "0", "vansj": "0"},
        "carg": [{"cd": "1", "agr": [
            {"par": [{"sg": "PL", "cand": [{"n": "22", "nmu": "FLAVIO BOLSONARO",
                                            "vap": str(c["flavio"])}]}]},
            {"par": [{"sg": "PT", "cand": [{"n": "13", "nmu": "LULA",
                                            "vap": str(c["lula"])}]}]},
        ]}],
    }  # fmt: skip


def arquivos_do_turno() -> dict[Arquivo, dict]:
    """Os JSONs de um turno coerente: municípios somam as UFs, e as UFs somam o Brasil."""
    contagens = {chave: contagem(*valores) for chave, valores in MUNICIPIOS.items()}
    por_uf = {uf: somar(*(c for (u, _), c in contagens.items() if u == uf)) for uf in ("AC", "ZZ")}
    arquivos = {Arquivo(T2, "config"): LISTA}
    arquivos[Arquivo(T2, "brasil")] = resultado_json("br", "br", somar(*por_uf.values()))
    for uf, c in por_uf.items():
        arquivos[Arquivo(T2, "uf", uf)] = resultado_json("uf", uf.lower(), c)
    for (uf, cd), c in contagens.items():
        arquivos[Arquivo(T2, "municipio", uf, cd)] = resultado_json("mu", cd, c)
    return arquivos


def montar_bronze(dados: Path, arquivos: dict[Arquivo, dict]) -> None:
    dir_bronze = dados / "bronze" / "resultados"
    manifest = Manifest(dir_bronze / "manifest.csv", EntradaResultado)
    for arquivo, corpo in arquivos.items():
        conteudo = json.dumps(corpo).encode()
        destino = dir_bronze / arquivo.caminho
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(conteudo)
        manifest.registrar(
            EntradaResultado(
                url=arquivo.url,
                ano=2026,
                turno=2,
                abrangencia=arquivo.abrangencia,
                uf=arquivo.uf,
                cd_municipio=arquivo.cd_municipio,
                caminho=arquivo.caminho.as_posix(),
                sha512=hashlib.sha512(conteudo).hexdigest(),
                bytes=len(conteudo),
                gerado_em="2026-10-25T23:27:02-03:00",
                downloaded_at="2026-10-26T00:00:00+00:00",
                status=Status.SUCCESS,
            )
        )


def ler(dados: Path, tabela: str, where: str = "true") -> list[dict]:
    rel = duckdb.sql(
        # gerado_em (timestamptz) pediria o pytz para virar Python; os testes não precisam dele.
        "select columns(c -> c <> 'gerado_em') "
        f"from read_parquet('{dados}/silver/{tabela}/*/*/*.parquet', "
        f"hive_partitioning = true) where {where} order by all"
    )
    return [dict(zip(rel.columns, linha, strict=True)) for linha in rel.fetchall()]


def test_constroi_uma_linha_por_nivel_com_nome_e_codigo_ibge(tmp_path):
    montar_bronze(tmp_path, arquivos_do_turno())

    [construcao] = construir_resultados(tmp_path)

    assert (construcao.ano, construcao.turno, construcao.municipios) == (2026, 2, 3)
    linhas = ler(tmp_path, "resultado")
    assert [(r["nivel"], r["uf"], r["cd_municipio"]) for r in linhas] == [
        ("brasil", None, None),
        ("municipio", "AC", 1007),
        ("municipio", "AC", 1120),
        ("municipio", "ZZ", 29262),
        ("uf", "AC", None),
        ("uf", "ZZ", None),
    ]
    [acrelandia] = ler(tmp_path, "resultado", "cd_municipio = 1120")
    assert acrelandia["nm_municipio"] == "ACRELÂNDIA"
    assert acrelandia["cd_ibge"] == 1200013
    assert acrelandia["nulo_tecnico"] == 58
    assert acrelandia["eleitorado"] == acrelandia["aptos"] + 3
    [abu_dhabi] = ler(tmp_path, "resultado", "cd_municipio = 29262")
    assert abu_dhabi["cd_ibge"] is None


def test_votos_por_candidato_com_nome_de_urna_e_partido(tmp_path):
    montar_bronze(tmp_path, arquivos_do_turno())
    construir_resultados(tmp_path)

    votos = ler(tmp_path, "resultado_votos", "nivel = 'brasil'")
    assert [(v["nr_votavel"], v["nm_votavel"], v["sg_partido"], v["votos"]) for v in votos] == [
        (13, "LULA", "PT", 1647 + 2100 + 300),
        (22, "FLAVIO BOLSONARO", "PL", 5751 + 5584 + 250),
    ]


def test_rerun_sem_mudanca_no_bronze_e_pulado(tmp_path):
    montar_bronze(tmp_path, arquivos_do_turno())
    construir_resultados(tmp_path)

    [construcao] = construir_resultados(tmp_path)
    assert construcao.pulado


def test_sem_bronze_de_resultados_nao_faz_nada(tmp_path):
    assert construir_resultados(tmp_path) == []


def test_municipio_faltando_derruba_o_build(tmp_path):
    arquivos = arquivos_do_turno()
    del arquivos[Arquivo(T2, "municipio", "AC", "01007")]
    montar_bronze(tmp_path, arquivos)

    with pytest.raises(ResultadoIncompleto, match="faltam 1"):
        construir_resultados(tmp_path)
    assert not (tmp_path / "silver" / "resultado").exists()


def test_municipios_que_nao_somam_a_uf_derrubam_o_build(tmp_path):
    arquivos = arquivos_do_turno()
    bujari = arquivos[Arquivo(T2, "municipio", "AC", "01007")]
    bujari["carg"][0]["agr"][1]["par"][0]["cand"][0]["vap"] = "2101"
    bujari["v"]["vv"] = str(int(bujari["v"]["vv"]) + 1)
    bujari["e"]["c"] = str(int(bujari["e"]["c"]) + 1)
    bujari["e"]["a"] = str(int(bujari["e"]["a"]) - 1)
    montar_bronze(tmp_path, arquivos)

    with pytest.raises(ResultadoInconsistente, match=r"AC: soma dos filhos <> \['abstencao'"):
        construir_resultados(tmp_path)


def test_conta_que_nao_fecha_dentro_do_arquivo_derruba_o_build(tmp_path):
    arquivos = arquivos_do_turno()
    arquivos[Arquivo(T2, "uf", "ZZ")]["v"]["vb"] = "6"
    montar_bronze(tmp_path, arquivos)

    with pytest.raises(ResultadoInconsistente, match="uf/ZZ: comparecimento"):
        construir_resultados(tmp_path)
