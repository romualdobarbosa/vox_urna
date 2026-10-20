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


def test_malha_uf_cobre_todas_as_ufs_da_projecao_menos_o_exterior():
    import mapa

    gold = dados.carregar()
    ufs = set(dados.tabela_uf(gold.projecao, "base")["uf"]) - {dados.UF_EXTERIOR}
    assert ufs == {f["properties"]["uf"] for f in mapa.malha("uf")["features"]}


def test_nome_municipio_em_grafia_usual():
    assert dados.nome_municipio("BOA ESPERANÇA DO NORTE") == "Boa Esperança do Norte"
    assert dados.nome_municipio("OLHO D'ÁGUA DO BORGES") == "Olho d'Água do Borges"
    assert dados.nome_municipio("PINGO-D'ÁGUA") == "Pingo-D'Água"


def test_so_boa_esperanca_do_norte_fica_sem_poligono_na_malha_municipal():
    import mapa

    gold = dados.carregar()
    municipios = dados.tabela_municipio(gold.projecao, gold.municipio_ibge, "base")
    assert municipios["cd_ibge"].null_count() == 0
    malha = {f["properties"]["cd_ibge"] for f in mapa.malha("municipio")["features"]}
    sem_poligono = municipios.filter(~pl.col("cd_ibge").is_in(malha))
    assert sem_poligono["cd_ibge"].to_list() == [mapa.SEM_POLIGONO]
    assert set(municipios["cd_ibge"]) >= malha


def test_exterior_lista_as_cidades_pelo_eleitorado():
    gold = dados.carregar()
    exterior = dados.tabela_exterior(gold.projecao, "base")
    assert exterior.height > 100
    assert exterior["aptos"].is_sorted(descending=True)
    assert "Abu Dhabi" in exterior["nome"].to_list()


def test_zonas_ordenadas_da_mais_favoravel_a_lula_e_exterior_como_uma_zona():
    gold = dados.carregar()
    zonas = dados.tabela_zona(gold.projecao, "base")
    assert zonas["margem_lula"].is_sorted(descending=True)
    assert zonas.filter(pl.col("uf") == dados.UF_EXTERIOR)["nome"].to_list() == ["Exterior"]
    assert dados.tabela_zona(gold.projecao, "base", "AC")["nome"][0].endswith("(AC)")


def test_matriz_de_transferencia_usa_os_nomes_da_eleicao_e_cada_linha_soma_1():
    gold = dados.carregar()
    m2018 = dados.matriz_transferencia(gold.transferencia_uf, gold.lados, 2018, "SP")
    assert m2018["origem"].to_list() == [
        "Fernando Haddad (PT)",
        "Jair Bolsonaro (PSL)",
        "Outros candidatos",
        "Branco",
        "Nulo",
        "Abstenção",
    ]
    assert m2018.columns[1:3] == ["Fernando Haddad (PT)", "Jair Bolsonaro (PSL)"]
    somas = m2018.select(pl.sum_horizontal(pl.exclude("origem")))
    assert somas.to_series().to_list() == pytest.approx([1.0] * 6)
