from pathlib import Path

import duckdb
import numpy as np
import pytest
from test_transferencia import BETA

from vox_urna.gold.construcao import GoldInconsistente, construir
from vox_urna.gold.lados import LADOS, Finalista
from vox_urna.gold.transferencia import J, K

# nr_votavel do lado Lula, do lado Flávio e de um candidato que cai em "outros", como no BU.
CANDIDATOS = {
    2018: {13: ("FERNANDO HADDAD", "PT"), 17: ("JAIR BOLSONARO", "PSL"), 12: ("CIRO GOMES", "PDT")},
    2022: {13: ("LULA", "PT"), 22: ("JAIR BOLSONARO", "PL"), 15: ("SIMONE TEBET", "MDB")},
    2026: {13: ("LULA", "PT"), 22: ("FLAVIO BOLSONARO", "PL"), 70: ("AUGUSTO CURY", "AVANTE")},
}
# (UF, município, zona, seções). Na referência, a zona 1 tem β próprio e a zona 2, com
# poucas seções, usa o da UF. Em 2026 o município 1570 passa para a zona 3 (passo
# "municipio") e surge o município 1007 (passo "uf").
SECOES_REF = [("AC", 1120, 1, 30), ("AC", 1120, 2, 3), ("AC", 1570, 2, 4)]
SECOES_2026 = [("AC", 1120, 1, 20), ("AC", 1570, 3, 5), ("AC", 1007, 1, 2)]
COLUNAS_SECAO = (
    "uf",
    "cd_municipio",
    "nm_municipio",
    "zona",
    "secao",
    "aptos",
    "comparecimento",
    "abstencao",
    "branco",
    "nulo",
    "nominais",
)
COLUNAS_VOTOS = ("uf", "cd_municipio", "zona", "secao", "nr_votavel", "nm_votavel", "sg_partido")


def secao(ano, uf, municipio, zona, numero, aptos, contagens) -> dict:
    """Seção do silver a partir das contagens (lula, flavio, outros, branco, nulo)."""
    nrs = list(CANDIDATOS[ano])
    votos = {nr: int(v) for nr, v in zip(nrs, contagens[:3], strict=True) if v > 0}
    branco, nulo = int(contagens[3]), int(contagens[4])
    comparecimento = sum(votos.values()) + branco + nulo
    return {
        "uf": uf,
        "cd_municipio": municipio,
        "nm_municipio": f"MUNICIPIO {municipio}",
        "zona": zona,
        "secao": numero,
        "aptos": aptos,
        "comparecimento": comparecimento,
        "abstencao": aptos - comparecimento,
        "branco": branco,
        "nulo": nulo,
        "nominais": sum(votos.values()),
        "votos": votos,
    }


def gravar_silver(dados: Path, ano: int, turno: int, secoes: list[dict]) -> None:
    votos = [
        (s["uf"], s["cd_municipio"], s["zona"], s["secao"], nr, *CANDIDATOS[ano][nr], v)
        for s in secoes
        for nr, v in s["votos"].items()
    ]
    tabelas = {
        "secao": {c: np.array([s[c] for s in secoes]) for c in COLUNAS_SECAO},
        "votos": {
            c: np.array([linha[i] for linha in votos])
            for i, c in enumerate((*COLUNAS_VOTOS, "votos"))
        },
    }
    for tabela, colunas in tabelas.items():
        destino = dados / "silver" / tabela / f"ano={ano}" / f"turno={turno}"
        destino.mkdir(parents=True)
        con = duckdb.connect()
        con.register("t", colunas)
        con.execute(f"copy t to '{destino / 'dados.parquet'}' (format parquet)")
        con.close()


def montar_silver(dados: Path, refs=(2022,), seed=0) -> None:
    """Cada referência com o 2º turno gerado por BETA a partir do 1º, e o 1º turno de 2026."""
    rng = np.random.default_rng(seed)
    for ano in refs:
        t1, t2 = [], []
        for numero, (uf, municipio, zona) in enumerate(
            (uf, m, z) for uf, m, z, n in SECOES_REF for _ in range(n)
        ):
            aptos = 20_000
            x = np.floor(rng.dirichlet([8, 7, 2, 0.5, 0.8, 4]) * aptos)
            x[-1] = aptos - x[:-1].sum()
            y = np.floor(x @ BETA)
            t1.append(secao(ano, uf, municipio, zona, numero, aptos, x[:5]))
            t2.append(secao(ano, uf, municipio, zona, numero, aptos, [y[0], y[1], 0, y[2], y[3]]))
        gravar_silver(dados, ano, 1, t1)
        gravar_silver(dados, ano, 2, t2)

    t2026 = [
        secao(2026, uf, m, z, numero, 300, rng.multinomial(300, [0.3, 0.35, 0.1, 0.02, 0.03, 0.2]))
        for numero, (uf, m, z) in enumerate(
            (uf, m, z) for uf, m, z, n in SECOES_2026 for _ in range(n)
        )
    ]
    gravar_silver(dados, 2026, 1, t2026)


def ler(dados: Path, tabela: str, filtro: str = "true") -> list[dict]:
    rel = duckdb.sql(f"select * from '{dados}/gold/{tabela}.parquet' where {filtro} order by all")
    return [dict(zip(rel.columns, linha, strict=True)) for linha in rel.fetchall()]


def beta(linhas: list[dict]) -> np.ndarray:
    por_k = {linha["k"]: [linha[j] for j in J] for linha in linhas}
    return np.array([por_k[k] for k in K])


def votos_1t_2026(dados: Path, filtro: str) -> np.ndarray:
    categorias = ler(dados, "categorias_secao", f"ano = 2026 and {filtro}")
    return np.array([sum(c[k] * c["aptos"] for c in categorias) for k in K])


@pytest.fixture
def gold(tmp_path):
    montar_silver(tmp_path)
    construir(tmp_path)
    return tmp_path


def test_zona_com_secoes_suficientes_recupera_o_beta_e_a_pequena_usa_o_da_uf(gold):
    zona_1 = ler(gold, "transferencia_zona_2022", "nivel = 'zona' and zona = 1")
    zona_2 = ler(gold, "transferencia_zona_2022", "nivel = 'zona' and zona = 2")
    uf = ler(gold, "transferencia_zona_2022", "nivel = 'uf'")

    assert {linha["fonte"] for linha in zona_1} == {"zona"}
    for categoria in ("lula", "flavio", "abstencao"):
        i = K.index(categoria)
        np.testing.assert_allclose(beta(zona_1)[i], BETA[i], atol=0.02)
    assert {linha["fonte"] for linha in zona_2} == {"uf"}
    np.testing.assert_array_equal(beta(zona_2), beta(uf))


def test_painel_casa_todas_as_secoes(gold):
    [taxa] = ler(gold, "taxa_match", "ligacao = 'painel_secao_2022'")

    assert (taxa["passo"], taxa["secoes"], taxa["fracao_aptos"]) == ("casou", 37, 1)


def test_ligacao_usa_os_tres_passos_e_reporta_a_taxa_de_match(gold):
    passos = {
        (linha["cd_municipio"], linha["zona"]): linha["passo"]
        for linha in ler(gold, "ligacao_2026")
    }
    assert passos == {(1120, 1): "municipio_zona", (1570, 3): "municipio", (1007, 1): "uf"}

    taxas = ler(gold, "taxa_match", "ligacao = 'ligacao_2026_ref_2022'")
    assert {t["passo"]: t["secoes"] for t in taxas} == {
        "municipio_zona": 20,
        "municipio": 5,
        "uf": 2,
    }
    assert sum(t["fracao_aptos"] for t in taxas) == pytest.approx(1)

    # O município 1570 só estava na zona 2 em 2022, então herda o β dela.
    zona_2 = ler(gold, "transferencia_zona_2022", "nivel = 'zona' and zona = 2")
    np.testing.assert_allclose(beta(ler(gold, "ligacao_2026", "cd_municipio = 1570")), beta(zona_2))


def test_projecao_conserva_os_aptos_e_aplica_o_beta_ligado(gold):
    [brasil] = ler(gold, "projecao_2026", "cenario = 'base' and nivel = 'brasil'")
    assert sum(brasil[j] for j in J) == pytest.approx(brasil["aptos"])
    assert brasil["pct_lula_validos"] + brasil["pct_flavio_validos"] == pytest.approx(1)

    [cidade] = ler(
        gold, "projecao_2026", "cenario = 'base' and nivel = 'municipio' and cd_municipio = 1007"
    )
    esperado = votos_1t_2026(gold, "cd_municipio = 1007") @ beta(
        ler(gold, "ligacao_2026", "cd_municipio = 1007")
    )
    np.testing.assert_allclose([cidade[j] for j in J], esperado)


def test_sem_2018_a_sensibilidade_divide_outros_entre_os_lados(gold):
    cenarios = {
        p["cenario"]: p
        for p in ler(gold, "projecao_2026", "nivel = 'municipio' and cd_municipio = 1007")
    }
    assert set(cenarios) == {"base", "outros_divididos"}

    outros = votos_1t_2026(gold, "cd_municipio = 1007")[K.index("outros")]
    linha_base = beta(ler(gold, "ligacao_2026", "cd_municipio = 1007"))[K.index("outros")]
    diferenca = [cenarios["outros_divididos"][j] - cenarios["base"][j] for j in J]
    np.testing.assert_allclose(diferenca, outros * (np.array([0.5, 0.5, 0, 0, 0]) - linha_base))


def test_com_2018_a_sensibilidade_usa_a_linha_outros_de_2018(tmp_path):
    montar_silver(tmp_path, refs=(2018, 2022))
    construir(tmp_path)

    cenarios = {
        p["cenario"]: p
        for p in ler(tmp_path, "projecao_2026", "nivel = 'municipio' and cd_municipio = 1120")
    }
    assert set(cenarios) == {"base", "outros_2018"}

    outros = votos_1t_2026(tmp_path, "cd_municipio = 1120")[K.index("outros")]
    i = K.index("outros")
    linha = {
        ano: beta(ler(tmp_path, "ligacao_2026", f"ano_ref = {ano} and cd_municipio = 1120"))[i]
        for ano in (2018, 2022)
    }
    diferenca = [cenarios["outros_2018"][j] - cenarios["base"][j] for j in J]
    np.testing.assert_allclose(diferenca, outros * (linha[2018] - linha[2022]))


def test_finalista_diferente_do_bu_aborta_sem_gravar(tmp_path, monkeypatch):
    montar_silver(tmp_path)
    monkeypatch.setitem(LADOS[2026], "flavio", Finalista(22, "EDUARDO BOLSONARO", "PL"))

    with pytest.raises(GoldInconsistente, match="diverge do BU"):
        construir(tmp_path)
    assert not (tmp_path / "gold" / "projecao_2026.parquet").exists()


def test_voto_fora_dos_lados_no_2o_turno_aborta(tmp_path, monkeypatch):
    # Com o lado Flávio de 2022 apontando para a Tebet, os votos do Jair viram "outros".
    monkeypatch.setitem(LADOS[2022], "flavio", Finalista(15, "SIMONE TEBET", "MDB"))
    montar_silver(tmp_path)

    with pytest.raises(GoldInconsistente, match="fora dos lados"):
        construir(tmp_path)


def test_municipios_de_2026_ganham_o_codigo_ibge(gold):
    assert {
        (m["cd_municipio_tse"], m["cd_municipio_ibge"]) for m in ler(gold, "municipio_ibge")
    } == {(1120, 1200013), (1570, 1200054), (1007, 1200138)}


def test_municipio_sem_codigo_ibge_aborta(tmp_path, monkeypatch):
    monkeypatch.setattr("test_gold.SECOES_2026", [*SECOES_2026, ("AC", 99999, 1, 1)])
    montar_silver(tmp_path)

    with pytest.raises(GoldInconsistente, match="sem código IBGE"):
        construir(tmp_path)


def test_beta_brasil_e_a_media_das_zonas_ponderada_pelos_eleitores_de_cada_categoria(tmp_path):
    montar_silver(tmp_path)
    construir(tmp_path)

    brasil = ler(tmp_path, "transferencia_zona_2022", "nivel = 'brasil'")
    zonas = ler(tmp_path, "transferencia_zona_2022", "nivel = 'zona'")
    painel = ler(tmp_path, "painel_secao_2022")
    assert {(b["uf"], b["zona"], b["fonte"]) for b in brasil} == {(None, None, "media_zonas")}
    assert {b["secoes"] for b in brasil} == {len(painel)}
    np.testing.assert_allclose(beta(brasil).sum(axis=1), 1)

    for i, k in enumerate(K):
        peso = {
            z["zona"]: sum(p["aptos_1t"] * p[f"{k}_1t"] for p in painel if p["zona"] == z["zona"])
            for z in zonas
            if z["k"] == k
        }
        por_zona = {z["zona"]: [z[j] for j in J] for z in zonas if z["k"] == k}
        esperado = sum(peso[z] * np.array(por_zona[z]) for z in peso) / sum(peso.values())
        np.testing.assert_allclose(beta(brasil)[i], esperado)
        linhas = np.array(list(por_zona.values()))
        assert (linhas.min(axis=0) - 1e-12 <= beta(brasil)[i]).all()
        assert (beta(brasil)[i] <= linhas.max(axis=0) + 1e-12).all()
