from pathlib import Path

import duckdb
import numpy as np
import pytest
from test_transferencia import BETA

from vox_urna.gold import construcao
from vox_urna.gold.construcao import GoldInconsistente, construir
from vox_urna.gold.lados import LADOS, Finalista
from vox_urna.gold.transferencia import J, K, rmse_pp

# nr_votavel do lado Lula, do lado Flávio e dos candidatos que caem em "outros", como no BU.
# Em 2022 são dois, os da referência externa.
CANDIDATOS = {
    2018: {13: ("FERNANDO HADDAD", "PT"), 17: ("JAIR BOLSONARO", "PSL"), 12: ("CIRO GOMES", "PDT")},
    2022: {
        13: ("LULA", "PT"),
        22: ("JAIR BOLSONARO", "PL"),
        15: ("SIMONE TEBET", "MDB"),
        12: ("CIRO GOMES", "PDT"),
    },
    2026: {13: ("LULA", "PT"), 22: ("FLAVIO BOLSONARO", "PL"), 70: ("AUGUSTO CURY", "AVANTE")},
}
# (UF, município, zona, seções). Na referência, a zona 1 tem β próprio e a zona 2, com
# poucas seções, usa o da UF. Em 2026 o município 1570 passa para a zona 3 (passo
# "municipio") e surge o município 1007 (passo "uf").
SECOES_REF = [("AC", 1120, 1, 30), ("AC", 1120, 2, 3), ("AC", 1570, 2, 4)]
SECOES_2026 = [("AC", 1120, 1, 20), ("AC", 1570, 3, 5), ("AC", 1007, 1, 2)]
# Geografia de 2022 para o backtest, que liga 2022 a 2018 pelos mesmos três passos.
SECOES_2022_BACKTEST = [("AC", 1120, 1, 30), ("AC", 1570, 3, 12), ("AC", 1007, 1, 3)]
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
    """Seção do silver a partir das contagens (lula, flavio, outros, branco, nulo).

    Com dois candidatos em "outros", o segundo fica com um terço desses votos.
    """
    nrs = list(CANDIDATOS[ano])
    outros = int(contagens[2])
    por_nr = [contagens[0], contagens[1], outros][: len(nrs)]
    if len(nrs) == 4:
        por_nr = [contagens[0], contagens[1], outros - outros // 3, outros // 3]
    votos = {nr: int(v) for nr, v in zip(nrs, por_nr, strict=True) if v > 0}
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


def gravar_silver(
    dados: Path, ano: int, turno: int, secoes: list[dict], arquivo: str = "dados.parquet"
) -> None:
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
        destino.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect()
        con.register("t", colunas)
        con.execute(f"copy t to '{destino / arquivo}' (format parquet)")
        con.close()


def montar_silver(dados: Path, refs=(2022,), seed=0, geografia=None) -> None:
    """Cada referência com o 2º turno gerado por BETA a partir do 1º, e o 1º turno de 2026.

    `geografia` troca as seções de uma referência ({ano: [(uf, município, zona, seções)]}).
    """
    rng = np.random.default_rng(seed)
    for ano in refs:
        t1, t2 = [], []
        secoes_ref = (geografia or {}).get(ano, SECOES_REF)
        for numero, (uf, municipio, zona) in enumerate(
            (uf, m, z) for uf, m, z, n in secoes_ref for _ in range(n)
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


def votos(dados: Path, ano: int, turno: int, filtro: str) -> np.ndarray:
    categorias = ler(dados, "categorias_secao", f"ano = {ano} and turno = {turno} and {filtro}")
    return np.array([sum(c[k] * c["aptos"] for c in categorias) for k in K])


def votos_1t_2026(dados: Path, filtro: str) -> np.ndarray:
    return votos(dados, 2026, 1, filtro)


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


@pytest.fixture
def gold_backtest(tmp_path):
    montar_silver(tmp_path, refs=(2018, 2022), geografia={2022: SECOES_2022_BACKTEST})
    construir(tmp_path)
    return tmp_path


def test_sem_2018_nao_ha_backtest_nem_estabilidade(gold):
    assert not (gold / "gold" / "backtest_2022.parquet").exists()
    assert not (gold / "gold" / "estabilidade_transferencia.parquet").exists()
    assert not ler(gold, "taxa_match", "ligacao like 'ligacao_2022%'")


def test_backtest_liga_2022_a_2018_pelos_tres_passos(gold_backtest):
    passos = {
        (linha["cd_municipio"], linha["zona"]): linha["passo"]
        for linha in ler(gold_backtest, "ligacao_2022")
    }
    assert passos == {(1120, 1): "municipio_zona", (1570, 3): "municipio", (1007, 1): "uf"}
    assert {linha["ano_ref"] for linha in ler(gold_backtest, "ligacao_2022")} == {2018}

    taxas = ler(gold_backtest, "taxa_match", "ligacao = 'ligacao_2022_ref_2018'")
    assert {t["passo"]: t["secoes"] for t in taxas} == {
        "municipio_zona": 30,
        "municipio": 12,
        "uf": 3,
    }
    assert sum(t["fracao_aptos"] for t in taxas) == pytest.approx(1)


def test_backtest_aplica_o_beta_de_2018_ao_1o_turno_de_2022(gold_backtest):
    filtro = "cd_municipio = 1570"
    [linha] = ler(gold_backtest, "backtest_2022", f"nivel = 'municipio' and {filtro}")
    beta_2018 = beta(ler(gold_backtest, "ligacao_2022", filtro))
    # O município 1570 só estava na zona 2 em 2018, então herda o β dela.
    zona_2 = ler(gold_backtest, "transferencia_zona_2018", "nivel = 'zona' and zona = 2")
    np.testing.assert_allclose(beta_2018, beta(zona_2))

    t1 = votos(gold_backtest, 2022, 1, filtro)
    projetado = t1 @ beta_2018
    real = votos(gold_backtest, 2022, 2, filtro)[[K.index(j) for j in J]]
    pct = {
        "projetado": projetado[0] / (projetado[0] + projetado[1]),
        "real": real[0] / (real[0] + real[1]),
    }
    assert linha["pct_lula_validos_projetado"] == pytest.approx(pct["projetado"])
    assert linha["pct_lula_validos_real"] == pytest.approx(pct["real"])
    assert linha["erro_pp"] == pytest.approx(100 * (pct["projetado"] - pct["real"]))
    for i, j in enumerate(J):
        esperado = projetado[i] / linha["aptos"] - real[i] / linha["aptos_real"]
        assert linha[f"erro_aptos_{j}"] == pytest.approx(esperado, abs=1e-12)

    # Os dois turnos saem do mesmo BETA, então o método quase acerta.
    [brasil] = ler(gold_backtest, "backtest_2022", "nivel = 'brasil'")
    assert abs(brasil["erro_pp"]) < 0.5
    assert brasil["vencedor_projetado"] == brasil["vencedor_real"]


def test_backtest_conta_os_acertos_do_lado_vencedor(gold_backtest):
    for nivel in ("uf", "municipio"):
        linhas = ler(gold_backtest, "backtest_2022", f"nivel = '{nivel}'")
        [acerto] = ler(gold_backtest, "backtest_2022_acerto", f"nivel = '{nivel}'")
        certas = [linha for linha in linhas if linha["acertou"]]
        for linha in linhas:
            assert linha["acertou"] == (linha["vencedor_projetado"] == linha["vencedor_real"])
        assert (acerto["unidades"], acerto["acertos"]) == (len(linhas), len(certas))
        assert acerto["pct_acerto"] == pytest.approx(len(certas) / len(linhas))
        assert acerto["pct_acerto_aptos"] == pytest.approx(
            sum(linha["aptos"] for linha in certas) / sum(linha["aptos"] for linha in linhas)
        )


def test_rmse_da_zona_usa_o_beta_aplicado_as_suas_secoes_e_nao_existe_no_brasil(gold):
    painel = ler(gold, "painel_secao_2022")
    transferencia = ler(gold, "transferencia_zona_2022")

    def esperado(secoes, linhas):
        x = np.array([[s[f"{k}_1t"] for k in K] for s in secoes])
        y = np.array([[s[f"{j}_2t"] for j in J] for s in secoes])
        return rmse_pp(x, y, np.array([s["aptos_1t"] for s in secoes], dtype=float), beta(linhas))

    for nivel, zona in (("zona", 1), ("zona", 2), ("uf", None)):
        linhas = [t for t in transferencia if t["nivel"] == nivel and t["zona"] == zona]
        secoes = [s for s in painel if zona is None or s["zona"] == zona]
        assert [t["rmse_pp"] for t in linhas] == pytest.approx([esperado(secoes, linhas)] * 6)
    # O 2º turno sintético é o 1º vezes BETA arredondado: o ajuste fica abaixo de 0,1 pp.
    assert max(t["rmse_pp"] for t in transferencia if t["nivel"] != "brasil") < 0.1
    assert {t["rmse_pp"] for t in transferencia if t["nivel"] == "brasil"} == {None}


def test_estabilidade_e_a_tvd_de_cada_linha_entre_2018_e_2022(gold_backtest):
    estabilidade = ler(gold_backtest, "estabilidade_transferencia")
    assert {(e["nivel"], e["uf"]) for e in estabilidade} == {("brasil", None), ("uf", "AC")}
    assert len(estabilidade) == 2 * len(K)

    for nivel in ("brasil", "uf"):
        betas = [
            beta(ler(gold_backtest, f"transferencia_zona_{ano}", f"nivel = '{nivel}'"))
            for ano in (2018, 2022)
        ]
        tvd = {e["k"]: e["tvd"] for e in estabilidade if e["nivel"] == nivel}
        np.testing.assert_allclose(
            [tvd[k] for k in K], 0.5 * np.abs(betas[0] - betas[1]).sum(axis=1)
        )
        # Os dois anos saem do mesmo BETA, então as linhas pesadas quase não mudam.
        assert max(tvd[k] for k in ("lula", "flavio", "abstencao")) < 0.05


def test_referencia_externa_poe_cada_pesquisa_ao_lado_do_beta_brasil_de_outros(gold):
    linhas = ler(gold, "referencia_externa")
    pesquisas = [linha for linha in linhas if linha["fonte"] == "pesquisa"]
    [metodo] = [linha for linha in linhas if linha["fonte"] == "vox_urna"]
    assert {p["registro_tse"] for p in pesquisas} == {"BR-02012/2022", "BR-08253/2022"}

    # Pesos pelos votos de cada um no 1º turno de 2022: a Tebet tem 2/3 de "outros".
    [(ciro, tebet)] = duckdb.sql(
        "select sum(votos) filter (where nr_votavel = 12),"
        " sum(votos) filter (where nr_votavel = 15)"
        f" from '{gold}/silver/votos/ano=2022/turno=1/*.parquet'"
    ).fetchall()
    peso_ciro = ciro / (ciro + tebet)
    [datafolha] = [p for p in pesquisas if p["instituto"] == "Datafolha"]
    assert datafolha["peso_ciro"] == pytest.approx(peso_ciro)
    assert datafolha["peso_ciro"] + datafolha["peso_tebet"] == pytest.approx(1)
    assert datafolha["divisao_lula_ciro"] == pytest.approx(31 / (31 + 42))
    assert datafolha["divisao_lula_tebet"] == pytest.approx(31 / (31 + 29))
    lula = peso_ciro * 31 + (1 - peso_ciro) * 31
    bolsonaro = peso_ciro * 42 + (1 - peso_ciro) * 29
    assert datafolha["divisao_lula"] == pytest.approx(lula / (lula + bolsonaro))

    [outros] = ler(gold, "transferencia_zona_2022", "nivel = 'brasil' and k = 'outros'")
    assert metodo["divisao_lula"] == pytest.approx(
        outros["lula"] / (outros["lula"] + outros["flavio"])
    )
    assert metodo["peso_ciro"] is None


def test_pesquisa_sem_um_dos_candidatos_aborta(tmp_path, monkeypatch):
    original = Path(construcao.REFERENCIA_EXTERNA).read_text(encoding="utf-8").splitlines()
    csv = tmp_path / "referencia.csv"
    # Some a linha do Ciro na PoderData.
    linhas = [linha for linha in original if not ("PoderData" in linha and ",ciro," in linha)]
    csv.write_text("\n".join(linhas))
    monkeypatch.setattr(construcao, "REFERENCIA_EXTERNA", csv)
    montar_silver(tmp_path)

    with pytest.raises(GoldInconsistente, match="incompletas"):
        construir(tmp_path)


# Municípios a mais na zona 1 do AC (códigos reais, pelo código IBGE): município → (lula,
# flavio) no 2º turno de 2022 e no 1º turno de 2026. None é o turno em que ele não existe.
VIRADAS = {
    1058: ((60, 40), (30, 50)),  # Lula → Flávio, −22,5 p.p.
    1015: ((30, 70), (45, 15)),  # Flávio → Lula, +45 p.p.
    1074: ((50, 50), (20, 60)),  # empate em 2022: não é virada
    1112: ((70, 30), (40, 40)),  # empate em 2026: não é virada
    1139: ((55, 45), (45, 55)),  # Lula → Flávio, −10 p.p.
    1104: ((40, 60), None),  # só em 2022
}


# Uma cidade do exterior que virou com folga, para conferir que ela fica fora do Brasil, e
# outra que só votou em 2022 (os dois turnos, para o exterior ter β na ligação de 2026).
VIRADA_EXTERIOR = (99001, (10, 90), (45, 5))  # Flávio → Lula, +80 p.p.
SO_2022_EXTERIOR = ("ZZ", 29874, 1, 12)


@pytest.fixture
def gold_viradas(tmp_path):
    montar_silver(tmp_path, geografia={2022: [*SECOES_REF, SO_2022_EXTERIOR]})
    exterior, *pares = VIRADA_EXTERIOR
    for ano, turno, i in ((2022, 2, 0), (2026, 1, 1)):
        secoes = [
            secao(ano, "AC", m, 1, 9000 + n, 200, [*par[i], 0, 0, 0])
            for n, (m, par) in enumerate(VIRADAS.items())
            if par[i] is not None
        ]
        secoes.append(secao(ano, "ZZ", exterior, 1, 9100, 200, [*pares[i], 0, 0, 0]))
        gravar_silver(tmp_path, ano, turno, secoes, arquivo="viradas.parquet")
    construir(tmp_path)
    return tmp_path


def test_virada_e_a_troca_do_lado_na_frente_e_o_empate_nao_conta(gold_viradas):
    linhas = {
        m["cd_municipio"]: m
        for m in ler(gold_viradas, "viradas_municipio", f"cd_municipio in {tuple(VIRADAS)}")
    }

    assert {m: linha["virada"] for m, linha in linhas.items()} == {
        1058: "lula_para_flavio",
        1015: "flavio_para_lula",
        1074: None,
        1112: None,
        1139: "lula_para_flavio",
    }
    assert linhas[1058]["d22"] == pytest.approx(0.6)
    assert linhas[1058]["d26"] == pytest.approx(30 / 80)
    assert linhas[1058]["variacao_pp"] == pytest.approx(-22.5)
    assert linhas[1015]["variacao_pp"] == pytest.approx(45)
    assert linhas[1058]["aptos_2026"] == 200
    # A tabela por município mantém o exterior.
    [exterior] = ler(gold_viradas, "viradas_municipio", "uf = 'ZZ'")
    assert (exterior["virada"], exterior["variacao_pp"]) == ("flavio_para_lula", pytest.approx(80))


def test_resumo_conta_as_viradas_por_sentido_e_os_municipios_sem_par(gold_viradas):
    municipios = ler(gold_viradas, "viradas_municipio", "uf = 'AC'")
    resumo = {r["sentido"]: r for r in ler(gold_viradas, "viradas_municipio_resumo", "uf = 'AC'")}

    # 1120 e 1570 da base; 1007 e 1104 sem par (só em 2026 e só em 2022).
    assert len(municipios) == 7
    for sentido, r in resumo.items():
        viradas = [m for m in municipios if m["virada"] == sentido]
        variacoes = [m["variacao_pp"] for m in viradas]
        assert (r["municipios_pareados"], r["viradas"]) == (7, len(viradas))
        assert (r["municipios_so_2022"], r["municipios_so_2026"]) == (1, 1)
        assert r["pct_municipios"] == pytest.approx(len(viradas) / 7)
        assert r["pct_aptos_2026"] == pytest.approx(
            sum(m["aptos_2026"] for m in viradas) / sum(m["aptos_2026"] for m in municipios)
        )
        assert r["media_variacao_pp"] == pytest.approx(np.mean(variacoes))
        assert r["maior_variacao_pp"] == pytest.approx(max(variacoes, key=abs))
        assert r["menor_variacao_pp"] == pytest.approx(min(variacoes, key=abs))
    assert resumo["flavio_para_lula"]["maior_nm_municipio"] == "MUNICIPIO 1015"


def test_brasil_do_resumo_deixa_o_exterior_de_fora(gold_viradas):
    resumo = ler(gold_viradas, "viradas_municipio_resumo")
    brasil = {r["sentido"]: r for r in resumo if r["nivel"] == "brasil"}
    ac = {r["sentido"]: r for r in resumo if r["uf"] == "AC"}
    [exterior] = [r for r in resumo if r["uf"] == "ZZ" and r["sentido"] == "flavio_para_lula"]

    # Sem o exterior, o Brasil é o AC inteiro, com a cidade de +80 p.p. fora dos extremos.
    for sentido, r in brasil.items():
        assert r["uf"] is None
        assert {c: r[c] for c in r if c not in ("nivel", "uf")} == {
            c: ac[sentido][c] for c in r if c not in ("nivel", "uf")
        }
    assert brasil["flavio_para_lula"]["maior_nm_municipio"] == "MUNICIPIO 1015"
    assert (exterior["municipios_pareados"], exterior["viradas"]) == (1, 1)
    assert (exterior["municipios_so_2022"], exterior["municipios_so_2026"]) == (1, 0)
    assert exterior["maior_variacao_pp"] == pytest.approx(80)
