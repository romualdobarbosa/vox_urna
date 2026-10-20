import polars as pl
import pytest

import dados

LADOS = pl.DataFrame(
    {
        "ano": [2022, 2022, 2026, 2026],
        "lado": ["lula", "flavio", "lula", "flavio"],
        "nr_votavel": [13, 22, 13, 22],
        "nm_votavel": ["LULA", "JAIR BOLSONARO", "LULA", "FLAVIO BOLSONARO"],
        "sg_partido": ["PT", "PL", "PT", "PL"],
    }
)


def _linha(cenario, nivel, uf, lula, flavio):
    return {
        "cenario": cenario,
        "nivel": nivel,
        "uf": uf,
        "aptos": 100.0,
        "pct_lula_validos": lula,
        "pct_flavio_validos": flavio,
    }


PROJECAO = pl.DataFrame(
    [
        _linha("outros_2018", "brasil", None, 0.49, 0.51),
        _linha("base", "brasil", None, 0.47, 0.53),
        _linha("base", "uf", "ZZ", 0.5, 0.5),
        _linha("base", "uf", "SP", 0.4, 0.6),
        _linha("base", "uf", "AC", 0.3, 0.7),
        _linha("outros_2018", "uf", "AC", 0.35, 0.65),
    ]
)


def test_finalistas_usa_nome_real_com_acento_e_partido():
    assert dados.finalistas(LADOS) == {"lula": "Lula (PT)", "flavio": "Flávio Bolsonaro (PL)"}
    assert dados.finalistas(LADOS, 2022)["flavio"] == "Jair Bolsonaro (PL)"


def test_faixa_brasil_vai_do_menor_ao_maior_cenario():
    faixa = dados.faixa_brasil(PROJECAO)
    assert faixa["lula"] == pytest.approx((0.47, 0.49))
    assert faixa["flavio"] == pytest.approx((0.51, 0.53))


def test_cenarios_poe_o_base_primeiro():
    assert dados.cenarios(PROJECAO) == ["base", "outros_2018"]


def test_tabela_uf_filtra_o_cenario_e_poe_o_exterior_por_ultimo():
    uf = dados.tabela_uf(PROJECAO, "base")
    assert uf["uf"].to_list() == ["AC", "SP", "ZZ"]
    assert uf["nome"].to_list() == ["Acre", "São Paulo", "Exterior"]
    assert uf["margem_lula"].to_list() == pytest.approx([-0.4, -0.2, 0.0])


def test_todo_cenario_da_gold_tem_suposicoes_escritas():
    gold = dados.carregar()
    assert set(dados.cenarios(gold.projecao)) <= set(dados.CENARIOS)


def test_formatacao_brasileira():
    assert dados.pct(0.47518) == "47,5%"
    assert dados.pct(0.995248, 2) == "99,52%"
    assert dados.inteiro(57119831.46) == "57.119.831"
