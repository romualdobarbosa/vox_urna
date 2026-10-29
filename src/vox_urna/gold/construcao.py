import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np

from vox_urna.gold.lados import LADOS
from vox_urna.gold.transferencia import J, K, estimar_beta, rmse_pp

log = logging.getLogger(__name__)

MIN_SECOES_ZONA = 10
ANO_ALVO = 2026
ANO_BASE = 2022
ANO_SENSIBILIDADE = 2018
UF_EXTERIOR = "ZZ"
# Faixas de tamanho do município pelos aptos do 2º turno de ANO_ALVO: (limite superior
# inclusivo, nome). A última não tem limite.
FAIXAS_APTOS = [
    (10_000, "ate_10mil"),
    (50_000, "10mil_a_50mil"),
    (200_000, "50mil_a_200mil"),
    (1_000_000, "200mil_a_1mi"),
    (None, "acima_de_1mi"),
]

# Correspondência entre os códigos de município do TSE e do IBGE, copiada de
# https://github.com/betafcc/Municipios-Brasileiros-TSE (municipios_brasileiros_tse.csv,
# commit fe2694220eb7280a3df01e985641f34078989aa8, de 2018). Cobre os 5570 municípios até 2024.
CORRESPONDENCIA_IBGE = Path(__file__).parent / "municipios_tse_ibge.csv"
# Municípios instalados depois da fonte acima: (uf, código TSE, código IBGE). O código IBGE
# vem de https://servicodados.ibge.gov.br/api/v1/localidades/estados/MT/municipios.
MUNICIPIOS_NOVOS = [("MT", 73709, 5101837)]  # Boa Esperança do Norte, instalado em 2025
# Os municípios das regiões metropolitanas (ou RIDEs) que contêm uma capital, tirados de
# https://geoftp.ibge.gov.br/organizacao_do_territorio/estrutura_territorial/
# municipios_por_regioes_metropolitanas/Situacao_2020a2029/Composicao_RM_2025.xlsx. Quando a
# RM separa o núcleo metropolitano da área de expansão (Florianópolis), entra só o núcleo.
# Campo Grande e Rio Branco não têm RM e ficam só como capital.
RECORTES_METROPOLITANOS = Path(__file__).parent / "recortes_metropolitanos.csv"
REGIOES = {
    "N": ("AC", "AP", "AM", "PA", "RO", "RR", "TO"),
    "NE": ("AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"),
    "CO": ("DF", "GO", "MT", "MS"),
    "SE": ("ES", "MG", "RJ", "SP"),
    "S": ("PR", "RS", "SC"),
}
# Pesquisas de 2022 com cruzamento 1º × 2º turno, curadas dos relatórios originais
# (vox-urna.md, "Avaliação do método"), e o nome no BU de cada candidato de "outros" delas.
REFERENCIA_EXTERNA = Path(__file__).parent / "referencia_externa.csv"
CANDIDATOS_REFERENCIA = {"ciro": "CIRO GOMES", "tebet": "SIMONE TEBET"}
# Os dois primeiros dígitos do código IBGE são o código da UF.
CODIGO_UF_IBGE = {
    "RO": 11, "AC": 12, "AM": 13, "RR": 14, "PA": 15, "AP": 16, "TO": 17,
    "MA": 21, "PI": 22, "CE": 23, "RN": 24, "PB": 25, "PE": 26, "AL": 27, "SE": 28, "BA": 29,
    "MG": 31, "ES": 32, "RJ": 33, "SP": 35, "PR": 41, "SC": 42, "RS": 43,
    "MS": 50, "MT": 51, "GO": 52, "DF": 53,
}  # fmt: skip


class GoldInconsistente(RuntimeError):
    pass


@dataclass(frozen=True)
class Tabela:
    nome: str
    linhas: int


def construir(dir_dados: Path, *, min_secoes: int = MIN_SECOES_ZONA) -> list[Tabela]:
    """Reconstrói todo o gold a partir do silver: categorias, painéis, β, ligação e projeção."""
    dir_silver, dir_gold = dir_dados / "silver", dir_dados / "gold"
    saida_tmp = dir_gold / "_tmp"
    shutil.rmtree(saida_tmp, ignore_errors=True)
    saida_tmp.mkdir(parents=True)

    con = duckdb.connect()
    con.execute("set enable_progress_bar = false")
    try:
        for tabela in ("secao", "votos"):
            con.execute(
                f"create view {tabela} as select * from read_parquet("
                f"'{dir_silver / tabela}/*/*/*.parquet', hive_partitioning = true)"
            )
        turnos = set(con.execute("select distinct ano, turno from secao").fetchall())
        _carregar_lados(con, {ano for ano, _ in turnos})
        _categorias(con)
        # O painel de ANO_ALVO só existe depois do 2º turno: ele dá o β real, para o confronto.
        anos_painel = sorted(ano for ano, turno in turnos if turno == 1 and (ano, 2) in turnos)
        if (ANO_ALVO, 1) not in turnos or ANO_BASE not in anos_painel:
            raise GoldInconsistente(
                f"a projeção precisa do 1º turno de {ANO_ALVO} e dos dois turnos de {ANO_BASE}"
            )
        _paineis(con, anos_painel)
        _transferencias(con, min_secoes)
        _referencia_externa(con)
        refs = [ano for ano in (ANO_BASE, ANO_SENSIBILIDADE) if ano in anos_painel]
        _ligacao(con, ANO_ALVO, refs)
        _projecao(con, sensibilidade_2018=ANO_SENSIBILIDADE in refs)
        if ANO_SENSIBILIDADE in anos_painel:
            _ligacao(con, ANO_BASE, [ANO_SENSIBILIDADE])
            _backtest(con)
            _estabilidade(con)
        _municipio_ibge(con)
        _viradas(con)
        _primeiro_turno(con)
        if ANO_ALVO in anos_painel:
            _confronto(con)
            _comparacao_transferencia(con)
            _votos_municipio(con)
            _fluxo(con)
            _resumo_2026(con)

        tabelas = _gravar(con, saida_tmp)
    finally:
        con.close()

    for tabela in tabelas:
        nome = f"{tabela.nome}.parquet"
        (saida_tmp / nome).replace(dir_gold / nome)
    shutil.rmtree(saida_tmp, ignore_errors=True)
    return tabelas


def _carregar_lados(con: duckdb.DuckDBPyConnection, anos: set[int]) -> None:
    """Carrega a config de lados e confere cada finalista contra o nome e o partido do BU."""
    sem_config = anos - LADOS.keys()
    if sem_config:
        raise GoldInconsistente(f"sem config de lados para {sorted(sem_config)}")
    linhas = [(ano, lado, f) for ano in sorted(anos) for lado, f in LADOS[ano].items()]
    con.register(
        "_lados",
        {
            "ano": np.array([ano for ano, _, _ in linhas]),
            "lado": np.array([lado for _, lado, _ in linhas]),
            "nr_votavel": np.array([f.nr_votavel for _, _, f in linhas]),
            "nm_votavel": np.array([f.nm_votavel for _, _, f in linhas]),
            "sg_partido": np.array([f.sg_partido for _, _, f in linhas]),
        },
    )
    con.execute("create table lados as select * from _lados")
    divergentes = con.execute(
        """
        select l.ano, l.lado, l.nm_votavel, l.sg_partido,
               list(distinct v.nm_votavel || ' (' || v.sg_partido || ')') as no_bu
        from lados l
        left join votos v on v.ano = l.ano and v.nr_votavel = l.nr_votavel
        group by all
        having count(v.votos) = 0
            or bool_or(v.nm_votavel <> l.nm_votavel or v.sg_partido <> l.sg_partido)
        """
    ).fetchall()
    if divergentes:
        raise GoldInconsistente(f"config de lados diverge do BU: {divergentes}")


def _categorias(con: duckdb.DuckDBPyConnection) -> None:
    """Cada seção nas categorias do modelo, em votos (a fração dos aptos sai em `_gravar`)."""
    con.execute(
        """
        create table categorias as
        with por_lado as (
            select v.ano, v.turno, v.uf, v.cd_municipio, v.zona, v.secao,
                   sum(v.votos) filter (where l.lado = 'lula') as lula,
                   sum(v.votos) filter (where l.lado = 'flavio') as flavio
            from votos v
            join lados l on l.ano = v.ano and l.nr_votavel = v.nr_votavel
            group by all
        )
        select s.ano, s.turno, s.uf, s.cd_municipio, s.nm_municipio, s.zona, s.secao, s.aptos,
               coalesce(p.lula, 0) as lula,
               coalesce(p.flavio, 0) as flavio,
               s.nominais - coalesce(p.lula, 0) - coalesce(p.flavio, 0) as outros,
               s.branco, s.nulo, s.abstencao
        from secao s
        left join por_lado p using (ano, turno, uf, cd_municipio, zona, secao)
        where s.aptos > 0
        """
    )
    # No 2º turno só há os dois finalistas: "outros" diferente de zero é config de lado errada.
    [(com_outros,)] = con.execute(
        "select count(*) from categorias where turno = 2 and outros <> 0"
    ).fetchall()
    if com_outros:
        raise GoldInconsistente(f"{com_outros} seções de 2º turno com votos fora dos lados")


def _paineis(con: duckdb.DuckDBPyConnection, anos: list[int]) -> None:
    """Junta o 1º ao 2º turno por seção e registra a taxa de match ponderada pelos aptos."""
    fracoes_1t = ", ".join(f"t1.{k} / t1.aptos as {k}_1t" for k in K)
    fracoes_2t = ", ".join(f"t2.{j} / t2.aptos as {j}_2t" for j in J)
    chave = "uf, cd_municipio, zona, secao"
    con.execute(
        f"""
        create table painel as
        select t1.ano, {chave}, t1.nm_municipio,
               t1.aptos as aptos_1t, {fracoes_1t}, t2.aptos as aptos_2t, {fracoes_2t}
        from (select * from categorias where turno = 1) t1
        join (select * from categorias where turno = 2) t2 using (ano, {chave})
        where t1.ano in (select unnest($anos))
        """,
        {"anos": anos},
    )
    # "casou" e "so_1t" são frações dos aptos do 1º turno; "so_2t", dos aptos do 2º.
    con.execute(
        f"""
        create table taxa_match as
        with juncao as (
            select coalesce(t1.ano, t2.ano) as ano, t1.aptos as aptos_1t, t2.aptos as aptos_2t
            from (select * from categorias where turno = 1) t1
            full join (select * from categorias where turno = 2) t2 using (ano, {chave})
            where coalesce(t1.ano, t2.ano) in (select unnest($anos))
        ),
        passos as (
            select ano,
                   case when aptos_2t is null then 'so_1t'
                        when aptos_1t is null then 'so_2t'
                        else 'casou' end as passo,
                   coalesce(aptos_1t, aptos_2t) as aptos,
                   sum(aptos_1t) over (partition by ano) as total_1t,
                   sum(aptos_2t) over (partition by ano) as total_2t
            from juncao
        )
        select 'painel_secao_' || ano as ligacao, passo,
               count(*) as secoes,
               sum(aptos) as aptos,
               sum(aptos) / any_value(if(passo = 'so_2t', total_2t, total_1t)) as fracao_aptos
        from passos
        group by ano, passo
        """,
        {"anos": anos},
    )


def _transferencias(con: duckdb.DuckDBPyConnection, min_secoes: int) -> None:
    """β por zona e por UF; a zona com menos de `min_secoes` seções herda o da UF (ADR 0002)."""
    colunas = ["ano", "uf", "zona", "aptos_1t"]
    colunas += [f"{k}_1t" for k in K] + [f"{j}_2t" for j in J]
    dados = con.execute(
        f"select {', '.join(colunas)} from painel order by ano, uf, zona, cd_municipio, secao"
    ).fetchnumpy()
    x = np.column_stack([dados[f"{k}_1t"] for k in K]).astype(float)
    y = np.column_stack([dados[f"{j}_2t"] for j in J]).astype(float)
    peso = dados["aptos_1t"].astype(float)

    linhas: list[tuple] = []

    def registrar(ano, nivel, uf, zona, idx, fonte, beta):
        aptos, rmse = peso[idx].sum(), rmse_pp(x[idx], y[idx], peso[idx], beta)
        for i, k in enumerate(K):
            linhas.append((ano, nivel, uf, zona, len(idx), aptos, fonte, k, *beta[i], rmse))

    grupo_uf = np.char.add(dados["ano"].astype(str), np.asarray(dados["uf"], dtype=str))
    for g in np.unique(grupo_uf):
        no_uf = np.flatnonzero(grupo_uf == g)
        ano, uf = int(dados["ano"][no_uf[0]]), str(dados["uf"][no_uf[0]])
        beta_uf = estimar_beta(x[no_uf], y[no_uf], peso[no_uf])
        registrar(ano, "uf", uf, None, no_uf, "uf", beta_uf)
        zonas = dados["zona"][no_uf]
        for zona in np.unique(zonas):
            idx = no_uf[zonas == zona]
            if len(idx) >= min_secoes:
                beta, fonte = estimar_beta(x[idx], y[idx], peso[idx]), "zona"
            else:
                beta, fonte = beta_uf, "uf"
            registrar(ano, "zona", uf, int(zona), idx, fonte, beta)
        log.info("%s %s: β de %d zonas", ano, uf, len(np.unique(zonas)))

    nomes = ["ano", "nivel", "uf", "zona", "secoes", "aptos", "fonte", "k", *J, "rmse_pp"]
    con.register(
        "_beta",
        {nome: np.array([linha[i] for linha in linhas]) for i, nome in enumerate(nomes)},
    )
    con.execute(
        """
        create table beta as
        select cast(ano as integer) as ano, cast(nivel as varchar) as nivel,
               cast(uf as varchar) as uf, cast(zona as integer) as zona,
               cast(secoes as integer) as secoes, cast(aptos as bigint) as aptos,
               cast(fonte as varchar) as fonte, cast(k as varchar) as k,
               lula, flavio, branco, nulo, abstencao, rmse_pp
        from _beta
        """
    )
    # β Brasil: para cada linha k, a média dos β das zonas (o próprio ou o herdado da UF)
    # ponderada pelos eleitores da categoria k em cada zona. Só para leitura: a projeção
    # usa os β de zona e de UF. Não há RMSE no Brasil, porque esse β não é ajustado a seções.
    eleitores = ", ".join(f"sum(aptos_1t * {k}_1t) as {k}" for k in K)
    media = ", ".join(f"sum(n.eleitores * b.{j}) / sum(n.eleitores) as {j}" for j in J)
    con.execute(
        f"""
        insert into beta
        with contagem as (select ano, uf, zona, {eleitores} from painel group by all),
        n as (unpivot contagem on {", ".join(K)} into name k value eleitores)
        select b.ano, 'brasil', null, null, sum(b.secoes), sum(b.aptos), 'media_zonas', b.k,
               {media}, null
        from beta b
        join n using (ano, uf, zona, k)
        where b.nivel = 'zona'
        group by b.ano, b.k
        """
    )


def _referencia_externa(con: duckdb.DuckDBPyConnection) -> None:
    """Cada pesquisa ao lado do β Brasil de "outros" em 2022, sem média entre elas.

    A métrica é Lula ÷ (Lula + Bolsonaro) entre os eleitores de "outros". Numa pesquisa, os
    percentuais de cada candidato são ponderados pelos votos dele no 1º turno de 2022 nos
    BUs antes da divisão, que vale tanto na base "total" quanto na de votos válidos.
    """
    con.execute(
        """
        create table _referencia as
        select * from read_csv($csv, header = true, columns = {
            'instituto': 'varchar', 'registro_tse': 'varchar', 'campo_inicio': 'date',
            'campo_fim': 'date', 'modo': 'varchar', 'n_total': 'integer',
            'candidato_1t': 'varchar', 'base': 'varchar', 'pct_lula': 'double',
            'pct_bolsonaro': 'double', 'pct_branco_nulo': 'double', 'pct_indeciso': 'double',
            'url': 'varchar', 'obs': 'varchar'
        })
        """,
        {"csv": str(REFERENCIA_EXTERNA)},
    )
    con.register(
        "_candidatos_referencia",
        {
            "candidato_1t": np.array(list(CANDIDATOS_REFERENCIA)),
            "nm_votavel": np.array(list(CANDIDATOS_REFERENCIA.values())),
        },
    )
    con.execute(
        f"""
        create table _peso_referencia as
        select c.candidato_1t, sum(v.votos) as votos,
               sum(v.votos) / sum(sum(v.votos)) over () as peso
        from _candidatos_referencia c
        left join votos v on v.nm_votavel = c.nm_votavel and v.ano = {ANO_BASE} and v.turno = 1
        group by c.candidato_1t
        """
    )
    sem_voto = con.execute(
        "select candidato_1t from _peso_referencia where votos is null order by all"
    ).fetchall()
    if sem_voto:
        raise GoldInconsistente(f"candidatos da referência externa sem voto no BU: {sem_voto}")
    candidatos = sorted(CANDIDATOS_REFERENCIA)
    incompletas = con.execute(
        """
        select registro_tse from _referencia
        group by registro_tse
        having list_sort(list(candidato_1t)) <> $candidatos
            or count(distinct base) <> 1
            or bool_or(base not in ('total', 'votos_validos'))
            or bool_or(pct_lula is null or pct_bolsonaro is null)
        order by all
        """,
        {"candidatos": candidatos},
    ).fetchall()
    if incompletas:
        raise GoldInconsistente(f"pesquisas da referência externa incompletas: {incompletas}")

    por_candidato = ", ".join(
        f"any_value(r.pct_lula / (r.pct_lula + r.pct_bolsonaro)) "
        f"filter (where r.candidato_1t = '{c}') as divisao_lula_{c}"
        for c in candidatos
    )
    pesos = ", ".join(
        f"any_value(p.peso) filter (where p.candidato_1t = '{c}') as peso_{c}" for c in candidatos
    )
    nulos = ", ".join(f"null as divisao_lula_{c}" for c in candidatos)
    nulos += ", " + ", ".join(f"null as peso_{c}" for c in candidatos)
    con.execute(
        f"""
        create table referencia_externa as
        select 'pesquisa' as fonte, r.instituto, r.registro_tse,
               any_value(r.campo_inicio) as campo_inicio, any_value(r.campo_fim) as campo_fim,
               any_value(r.modo) as modo, any_value(r.n_total) as n_total,
               any_value(r.base) as base, {por_candidato}, {pesos},
               sum(p.peso * r.pct_lula) / sum(p.peso * (r.pct_lula + r.pct_bolsonaro))
                   as divisao_lula,
               any_value(r.url) as url
        from _referencia r
        join _peso_referencia p using (candidato_1t)
        group by r.instituto, r.registro_tse
        union all
        select 'vox_urna', null, null, null, null, null, null, 'beta_brasil_outros_{ANO_BASE}',
               {nulos}, lula / (lula + flavio), null
        from beta
        where ano = {ANO_BASE} and nivel = 'brasil' and k = 'outros'
        """
    )


def _ligacao(con: duckdb.DuckDBPyConnection, ano_alvo: int, refs: list[int]) -> None:
    """Liga cada (UF, município, zona) de `ano_alvo` ao β de cada referência (ADR 0002).

    Passos: (1) a zona da referência com o mesmo par (município, zona); (2) as zonas que
    continham o município, ponderadas pelos aptos; (3) a UF.
    """
    media = ", ".join(f"sum(r.aptos * b.{j}) / sum(r.aptos) as {j}" for j in J)
    direto = ", ".join(f"b.{j}" for j in J)
    con.execute(
        f"""
        create table ligacao_{ano_alvo} as
        with unidade as (
            select uf, cd_municipio, zona, count(*) as secoes, sum(aptos) as aptos
            from categorias where ano = {ano_alvo} and turno = 1
            group by all
        ),
        ref as (
            select ano as ano_ref, uf, cd_municipio, zona, sum(aptos_1t) as aptos
            from painel group by all
        ),
        anos_ref as (select unnest($refs) as ano_ref),
        par as (
            select a.ano_ref, u.*, 'municipio_zona' as passo, b.k, {direto}
            from unidade u
            cross join anos_ref a
            join ref r on r.ano_ref = a.ano_ref
                and (r.uf, r.cd_municipio, r.zona) = (u.uf, u.cd_municipio, u.zona)
            join beta b on b.ano = a.ano_ref and b.nivel = 'zona'
                and (b.uf, b.zona) = (u.uf, u.zona)
        ),
        municipio as (
            select a.ano_ref, u.uf, u.cd_municipio, u.zona, u.secoes, u.aptos,
                   'municipio' as passo, b.k, {media}
            from unidade u
            cross join anos_ref a
            join ref r on r.ano_ref = a.ano_ref
                and (r.uf, r.cd_municipio) = (u.uf, u.cd_municipio)
            join beta b on b.ano = a.ano_ref and b.nivel = 'zona'
                and (b.uf, b.zona) = (r.uf, r.zona)
            where not exists (
                select 1 from par p
                where (p.ano_ref, p.uf, p.cd_municipio, p.zona)
                    = (a.ano_ref, u.uf, u.cd_municipio, u.zona)
            )
            group by a.ano_ref, u.uf, u.cd_municipio, u.zona, u.secoes, u.aptos, b.k
        ),
        uf as (
            select a.ano_ref, u.*, 'uf' as passo, b.k, {direto}
            from unidade u
            cross join anos_ref a
            join beta b on b.ano = a.ano_ref and b.nivel = 'uf' and b.uf = u.uf
            where not exists (
                select 1 from ref r
                where (r.ano_ref, r.uf, r.cd_municipio) = (a.ano_ref, u.uf, u.cd_municipio)
            )
        )
        select * from par
        union all select * from municipio
        union all select * from uf
        """,
        {"refs": refs},
    )
    [(faltando,)] = con.execute(
        f"""
        select count(*)
        from (
            select distinct uf, cd_municipio, zona
            from categorias where ano = {ano_alvo} and turno = 1
        ) u
        cross join (select unnest($refs) as ano_ref) a
        left join (
            select ano_ref, uf, cd_municipio, zona, count(*) as n
            from ligacao_{ano_alvo} group by all
        ) l using (ano_ref, uf, cd_municipio, zona)
        where coalesce(l.n, 0) <> {len(K)}
        """,
        {"refs": refs},
    ).fetchall()
    if faltando:
        raise GoldInconsistente(f"{faltando} (município, zona) de {ano_alvo} sem β ligado")
    con.execute(
        f"""
        insert into taxa_match
        select 'ligacao_{ano_alvo}_ref_' || ano_ref, passo,
               sum(secoes), sum(aptos), sum(aptos) / sum(sum(aptos)) over (partition by ano_ref)
        from (
            select distinct ano_ref, uf, cd_municipio, zona, secoes, aptos, passo
            from ligacao_{ano_alvo}
        )
        group by ano_ref, passo
        """
    )


def _projecao(con: duckdb.DuckDBPyConnection, *, sensibilidade_2018: bool) -> None:
    """Aplica cada cenário ao 1º turno de 2026 e agrega por zona, município, UF e Brasil.

    Base: o β de 2022 inteiro. Sensibilidade: a linha "outros" vem do β de 2018 ou, sem
    2018, é dividida igualmente entre os lados.
    """
    if sensibilidade_2018:
        outros = f"""
            select 'outros_{ANO_SENSIBILIDADE}' as cenario, uf, cd_municipio, zona, k,
                   {", ".join(J)}
            from ligacao_{ANO_ALVO} where ano_ref = {ANO_SENSIBILIDADE} and k = 'outros'
        """
    else:
        outros = f"""
            select 'outros_divididos' as cenario, uf, cd_municipio, zona, k,
                   0.5 as lula, 0.5 as flavio, 0.0 as branco, 0.0 as nulo, 0.0 as abstencao
            from ligacao_{ANO_ALVO} where ano_ref = {ANO_BASE} and k = 'outros'
        """
    sensibilidade = f"outros_{ANO_SENSIBILIDADE}" if sensibilidade_2018 else "outros_divididos"
    soma = ", ".join(f"sum(n.votos * b.{j}) as {j}" for j in J)
    total = ", ".join(f"sum({j}) as {j}" for j in J)
    con.execute(
        f"""
        create table projecao as
        with cenario as (
            select 'base' as cenario, uf, cd_municipio, zona, k, {", ".join(J)}
            from ligacao_{ANO_ALVO} where ano_ref = {ANO_BASE}
            union all
            select '{sensibilidade}', uf, cd_municipio, zona, k, {", ".join(J)}
            from ligacao_{ANO_ALVO} where ano_ref = {ANO_BASE} and k <> 'outros'
            union all
            {outros}
        ),
        unidade as (
            select uf, cd_municipio, any_value(nm_municipio) as nm_municipio, zona,
                   sum(aptos) as aptos, {", ".join(f"sum({k}) as {k}" for k in K)}
            from categorias where ano = {ANO_ALVO} and turno = 1
            group by uf, cd_municipio, zona
        ),
        n as (
            unpivot unidade on {", ".join(K)} into name k value votos
        ),
        projetada as (
            select b.cenario, n.uf, n.cd_municipio, n.nm_municipio, n.zona, n.aptos, {soma}
            from n
            join cenario b using (uf, cd_municipio, zona, k)
            group by all
        )
        select cenario,
               case when grouping(uf) = 1 then 'brasil'
                    when grouping(cd_municipio) = 0 then 'municipio'
                    when grouping(zona) = 0 then 'zona'
                    else 'uf' end as nivel,
               uf, cd_municipio,
               if(grouping(cd_municipio) = 0, any_value(nm_municipio), null) as nm_municipio,
               zona, sum(aptos) as aptos, {total},
               sum(lula) / (sum(lula) + sum(flavio)) as pct_lula_validos,
               sum(flavio) / (sum(lula) + sum(flavio)) as pct_flavio_validos
        from projetada
        group by grouping sets (
            (cenario), (cenario, uf), (cenario, uf, cd_municipio), (cenario, uf, zona)
        )
        """
    )


def _backtest(con: duckdb.DuckDBPyConnection) -> None:
    """Aplica o β de 2018 inteiro ao 1º turno de 2022 e compara com o 2º turno real.

    Só o cenário base, nos níveis Brasil, UF e município, e só com os municípios que
    votaram nos dois turnos (em 2022, Caracas só votou no 2º). Nada daqui corrige a
    projeção de 2026 (ADR 0004).
    """
    alvo, ref = ANO_BASE, ANO_SENSIBILIDADE
    soma = ", ".join(f"sum(n.votos * b.{j}) as {j}" for j in J)
    total = ", ".join(f"sum({j}) as {j}" for j in J)
    con.execute(
        f"""
        create table _backtest_municipio as
        with unidade as (
            select uf, cd_municipio, zona, sum(aptos) as aptos,
                   {", ".join(f"sum({k}) as {k}" for k in K)}
            from categorias where ano = {alvo} and turno = 1
            group by uf, cd_municipio, zona
        ),
        n as (unpivot unidade on {", ".join(K)} into name k value votos),
        projetada as (
            select n.uf, n.cd_municipio, n.zona, n.aptos, {soma}
            from n
            join ligacao_{alvo} b using (uf, cd_municipio, zona, k)
            where b.ano_ref = {ref}
            group by all
        ),
        projetada_municipio as (
            select uf, cd_municipio, sum(aptos) as aptos, {total}
            from projetada group by all
        ),
        real as (
            select uf, cd_municipio, any_value(nm_municipio) as nm_municipio,
                   sum(aptos) as aptos, {total}
            from categorias where ano = {alvo} and turno = 2
            group by all
        )
        select p.uf, p.cd_municipio, r.nm_municipio, p.aptos, r.aptos as aptos_real,
               {", ".join(f"p.{j} as {j}_projetado, r.{j} as {j}_real" for j in J)}
        from projetada_municipio p
        join real r using (uf, cd_municipio)
        """
    )

    _comparar_com_real(con, "_backtest_municipio", "backtest")


def _comparar_com_real(
    con: duckdb.DuckDBPyConnection, origem: str, destino: str, grupo: tuple[str, ...] = ()
) -> None:
    """Agrega `origem` (projetado × real por município) nos níveis Brasil, UF e município.

    Por linha: a % dos válidos do lado Lula projetada e a real, o erro em pp, o vencedor de
    cada um e se bateu, e o erro por categoria em fração dos aptos. Em `{destino}_acerto`,
    a % de UFs e de municípios em que o vencedor bateu, sem ponderar e ponderada pelos aptos
    do 1º turno. `grupo` são colunas a mais de `origem` que separam as linhas (o cenário).
    """

    def vencedor(lado: str) -> str:
        return (
            f"case when sum(lula_{lado}) > sum(flavio_{lado}) then 'lula' "
            f"when sum(lula_{lado}) < sum(flavio_{lado}) then 'flavio' else 'empate' end"
        )

    def pct_lula(lado: str) -> str:
        return f"sum(lula_{lado}) / (sum(lula_{lado}) + sum(flavio_{lado}))"

    # O erro por categoria é a diferença das frações dos aptos de cada turno.
    erros = ", ".join(
        f"sum({j}_projetado) / sum(aptos) - sum({j}_real) / sum(aptos_real) as erro_aptos_{j}"
        for j in J
    )
    prefixo = "".join(f"{c}, " for c in grupo)
    conjuntos = ", ".join(
        f"({', '.join([*grupo, *nivel])})" for nivel in ([], ["uf"], ["uf", "cd_municipio"])
    )
    con.execute(
        f"""
        create table {destino} as
        select {prefixo}case when grouping(uf) = 1 then 'brasil'
                    when grouping(cd_municipio) = 1 then 'uf'
                    else 'municipio' end as nivel,
               uf, cd_municipio,
               if(grouping(cd_municipio) = 0, any_value(nm_municipio), null) as nm_municipio,
               sum(aptos) as aptos, sum(aptos_real) as aptos_real,
               {pct_lula("projetado")} as pct_lula_validos_projetado,
               {pct_lula("real")} as pct_lula_validos_real,
               100 * ({pct_lula("projetado")} - {pct_lula("real")}) as erro_pp,
               {vencedor("projetado")} as vencedor_projetado,
               {vencedor("real")} as vencedor_real,
               {vencedor("projetado")} = {vencedor("real")} as acertou,
               {erros}
        from {origem}
        group by grouping sets ({conjuntos})
        """
    )
    con.execute(
        f"""
        create table {destino}_acerto as
        select {prefixo}nivel, count(*) as unidades, count(*) filter (where acertou) as acertos,
               avg(acertou::int) as pct_acerto,
               sum(aptos) filter (where acertou) / sum(aptos) as pct_acerto_aptos
        from {destino}
        where nivel <> 'brasil'
        group by {prefixo}nivel
        """
    )


def _estabilidade(con: duckdb.DuckDBPyConnection) -> None:
    """TVD de cada linha k do β entre 2018 e 2022, no Brasil e por UF: 0,5 · Σ_j |p − q|.

    A linha "outros" junta candidatos diferentes em cada ano, então a sua TVD compara
    eleitorados de composição diferente.
    """
    distancia = " + ".join(f"abs(a.{j} - b.{j})" for j in J)
    con.execute(
        f"""
        create table estabilidade as
        select a.nivel, a.uf, a.k, 0.5 * ({distancia}) as tvd
        from beta a
        join beta b on (b.nivel, b.uf, b.k) is not distinct from (a.nivel, a.uf, a.k)
        where a.ano = {ANO_SENSIBILIDADE} and b.ano = {ANO_BASE}
            and a.nivel in ('brasil', 'uf')
        """
    )


def _municipio_ibge(con: duckdb.DuckDBPyConnection) -> None:
    """Código IBGE de cada município brasileiro de 2026, para o mapa; o exterior fica fora."""
    con.execute(
        """
        create table _correspondencia as
        select uf, codigo_tse as cd_municipio_tse, codigo_ibge as cd_municipio_ibge,
               capital = 1 as capital
        from read_csv($csv, header = true, columns = {
            'codigo_tse': 'integer', 'uf': 'varchar', 'nome_municipio': 'varchar',
            'capital': 'integer', 'codigo_ibge': 'integer'
        })
        """,
        {"csv": str(CORRESPONDENCIA_IBGE)},
    )
    con.executemany("insert into _correspondencia values (?, ?, ?, false)", MUNICIPIOS_NOVOS)
    con.execute(
        """
        create table _recorte_metropolitano as
        select codigo_ibge as cd_municipio_ibge, recorte_metropolitano
        from read_csv($csv, header = true, columns = {
            'codigo_ibge': 'integer', 'recorte_metropolitano': 'varchar'
        })
        """,
        {"csv": str(RECORTES_METROPOLITANOS)},
    )
    con.register(
        "_regiao",
        {
            "uf": np.array([uf for ufs in REGIOES.values() for uf in ufs]),
            "regiao": np.array([r for r, ufs in REGIOES.items() for _ in ufs]),
        },
    )
    con.execute(
        f"""
        create table municipio_ibge as
        select m.uf, m.cd_municipio as cd_municipio_tse, c.cd_municipio_ibge, m.nm_municipio,
               r.regiao,
               case when c.capital then 'capital'
                    when rm.cd_municipio_ibge is not null then 'entorno_capital'
                    else 'interior' end as recorte,
               rm.recorte_metropolitano
        from (
            select uf, cd_municipio, any_value(nm_municipio) as nm_municipio
            from categorias where ano = {ANO_ALVO} and turno = 1 and uf <> 'ZZ'
            group by all
        ) m
        left join _correspondencia c on c.uf = m.uf and c.cd_municipio_tse = m.cd_municipio
        left join _recorte_metropolitano rm using (cd_municipio_ibge)
        left join _regiao r on r.uf = m.uf
        """
    )
    fora = con.execute(
        "select cd_municipio_ibge from _recorte_metropolitano "
        "except select cd_municipio_ibge from _correspondencia order by all"
    ).fetchall()
    if fora:
        raise GoldInconsistente(f"recorte metropolitano com código IBGE desconhecido: {fora}")
    sem_par = con.execute(
        "select uf, cd_municipio_tse, nm_municipio from municipio_ibge "
        "where cd_municipio_ibge is null order by all"
    ).fetchall()
    if sem_par:
        raise GoldInconsistente(f"municípios de {ANO_ALVO} sem código IBGE: {sem_par}")
    con.register(
        "_uf_ibge",
        {
            "uf": np.array(list(CODIGO_UF_IBGE)),
            "codigo": np.array(list(CODIGO_UF_IBGE.values())),
        },
    )
    errados = con.execute(
        """
        select m.uf, m.cd_municipio_tse, m.cd_municipio_ibge
        from municipio_ibge m left join _uf_ibge u using (uf)
        where u.codigo is null or m.cd_municipio_ibge // 100000 <> u.codigo
        order by all
        """
    ).fetchall()
    if errados:
        raise GoldInconsistente(f"código IBGE fora da UF do município: {errados}")


def _viradas(con: duckdb.DuckDBPyConnection) -> None:
    """Os municípios em que o lado na frente mudou do 2º turno de 2022 ao 1º turno de 2026.

    A métrica é d = lula ÷ (lula + flavio), com os votos do município somados; o empate
    exato não é virada. Só entram os municípios (no exterior, as cidades) que votaram nos
    dois turnos; os demais são contados no resumo. O Brasil do resumo deixa o exterior de
    fora: as cidades de consulado, com poucos votos, dominariam os extremos.
    """
    con.execute(
        f"""
        create table _divisao_municipio as
        select ano, uf, cd_municipio, any_value(nm_municipio) as nm_municipio,
               sum(aptos)::bigint as aptos, sum(lula) / (sum(lula) + sum(flavio)) as d
        from categorias
        where (ano, turno) in (({ANO_BASE}, 2), ({ANO_ALVO}, 1))
        group by all
        """
    )
    con.execute(
        f"""
        create table viradas as
        select uf, cd_municipio, b.nm_municipio, b.aptos as aptos_{ANO_ALVO},
               a.d as d22, b.d as d26, 100 * (b.d - a.d) as variacao_pp,
               case when a.d > 0.5 and b.d < 0.5 then 'lula_para_flavio'
                    when a.d < 0.5 and b.d > 0.5 then 'flavio_para_lula' end as virada
        from (select * from _divisao_municipio where ano = {ANO_BASE}) a
        join (select * from _divisao_municipio where ano = {ANO_ALVO}) b
            using (uf, cd_municipio)
        """
    )
    # Uma linha por nível e sentido, mesmo sem viradas; o exterior só tem a linha de UF. A
    # maior e a menor variação são em módulo, com o sinal mantido; a média é a dos
    # municípios, sem ponderar.
    con.execute(
        f"""
        create table viradas_resumo as
        with sem_par as (
            select uf,
                   count(*) filter (where ano = {ANO_BASE}) as so_{ANO_BASE},
                   count(*) filter (where ano = {ANO_ALVO}) as so_{ANO_ALVO}
            from _divisao_municipio m
            where not exists (
                select 1 from viradas v where (v.uf, v.cd_municipio) = (m.uf, m.cd_municipio)
            )
            group by uf
        ),
        por_nivel as (
            select 'uf' as nivel, uf as uf_nivel, * from viradas
            union all
            select 'brasil', null, * from viradas where uf <> '{UF_EXTERIOR}'
        ),
        unidade as (
            select v.*, s.sentido, v.virada is not distinct from s.sentido as virou
            from por_nivel v
            cross join (values ('lula_para_flavio'), ('flavio_para_lula')) s(sentido)
        ),
        resumo as (
            select nivel, uf_nivel as uf, sentido,
                   count(*) as municipios_pareados,
                   count(*) filter (where virou) as viradas,
                   count(*) filter (where virou) / count(*) as pct_municipios,
                   coalesce(sum(aptos_{ANO_ALVO}) filter (where virou), 0)
                       / sum(aptos_{ANO_ALVO}) as pct_aptos_{ANO_ALVO},
                   avg(variacao_pp) filter (where virou) as media_variacao_pp,
                   arg_max(variacao_pp, abs(variacao_pp)) filter (where virou)
                       as maior_variacao_pp,
                   arg_max(nm_municipio, abs(variacao_pp)) filter (where virou)
                       as maior_nm_municipio,
                   arg_max(uf, abs(variacao_pp)) filter (where virou) as maior_uf,
                   arg_min(variacao_pp, abs(variacao_pp)) filter (where virou)
                       as menor_variacao_pp,
                   arg_min(nm_municipio, abs(variacao_pp)) filter (where virou)
                       as menor_nm_municipio,
                   arg_min(uf, abs(variacao_pp)) filter (where virou) as menor_uf
            from unidade
            group by nivel, uf_nivel, sentido
        ),
        sem_par_nivel as (
            select 'uf' as nivel, * from sem_par
            union all
            select 'brasil', null, sum(so_{ANO_BASE})::bigint, sum(so_{ANO_ALVO})::bigint
            from sem_par where uf <> '{UF_EXTERIOR}'
        )
        select r.nivel, r.uf, r.sentido, r.municipios_pareados,
               coalesce(s.so_{ANO_BASE}, 0) as municipios_so_{ANO_BASE},
               coalesce(s.so_{ANO_ALVO}, 0) as municipios_so_{ANO_ALVO},
               r.* exclude (nivel, uf, sentido, municipios_pareados)
        from resumo r
        left join sem_par_nivel s
            on s.nivel = r.nivel and s.uf is not distinct from r.uf
        """
    )


def _primeiro_turno(con: duckdb.DuckDBPyConnection) -> None:
    """Os votos do 1º turno de ANO_ALVO por UF e no Brasil, que entra com o exterior, como no
    resultado oficial."""
    con.execute(
        f"""
        create table primeiro_turno as
        select case when grouping(uf) = 1 then 'brasil' else 'uf' end as nivel, uf,
               sum(aptos)::bigint as aptos,
               {", ".join(f"sum({k})::bigint as {k}" for k in K)}
        from categorias
        where ano = {ANO_ALVO} and turno = 1
        group by grouping sets ((uf), ())
        """
    )


def _confronto(con: duckdb.DuckDBPyConnection) -> None:
    """A projeção de cada cenário contra o 2º turno real de ANO_ALVO, só no território
    nacional, nos níveis Brasil, UF e município, como no backtest."""
    total = ", ".join(f"sum({j}) as {j}" for j in J)
    con.execute(
        f"""
        create table _confronto_municipio as
        with real as (
            select uf, cd_municipio, any_value(nm_municipio) as nm_municipio,
                   sum(aptos) as aptos, {total}
            from categorias where ano = {ANO_ALVO} and turno = 2 and uf <> '{UF_EXTERIOR}'
            group by all
        )
        select p.cenario, p.uf, p.cd_municipio, r.nm_municipio, p.aptos, r.aptos as aptos_real,
               {", ".join(f"p.{j} as {j}_projetado, r.{j} as {j}_real" for j in J)}
        from projecao p
        join real r using (uf, cd_municipio)
        where p.nivel = 'municipio'
        """
    )
    _comparar_com_real(con, "_confronto_municipio", "confronto", ("cenario",))


def _comparacao_transferencia(con: duckdb.DuckDBPyConnection) -> None:
    """O β de ANO_ALVO ao lado do de ANO_BASE, por (k, j), no Brasil e por UF, sem o exterior.

    O β Brasil é recalculado sem a zona do exterior, com a mesma média das zonas ponderada
    pelos eleitores de cada categoria; o `tvd` (0,5 · Σ_j |β_alvo − β_base|) se repete nas
    linhas j de cada k.
    """
    eleitores = ", ".join(f"sum(aptos_1t * {k}_1t) as {k}" for k in K)
    media = ", ".join(f"sum(n.eleitores * b.{j}) / sum(n.eleitores) as {j}" for j in J)
    anos = f"({ANO_BASE}, {ANO_ALVO})"
    con.execute(
        f"""
        create table comparacao_transferencia as
        with contagem as (
            select ano, uf, zona, {eleitores}
            from painel where ano in {anos} and uf <> '{UF_EXTERIOR}'
            group by all
        ),
        n as (unpivot contagem on {", ".join(K)} into name k value eleitores),
        largo as (
            select b.ano, 'brasil' as nivel, null::varchar as uf, b.k, {media}
            from beta b
            join n using (ano, uf, zona, k)
            where b.nivel = 'zona'
            group by b.ano, b.k
            union all
            select ano, nivel, uf, k, {", ".join(J)}
            from beta where ano in {anos} and nivel = 'uf' and uf <> '{UF_EXTERIOR}'
        ),
        longo as (unpivot largo on {", ".join(J)} into name j value valor)
        select a.nivel, a.uf, a.k, a.j,
               a.valor as beta_{ANO_BASE}, b.valor as beta_{ANO_ALVO},
               b.valor - a.valor as diferenca,
               0.5 * sum(abs(b.valor - a.valor)) over (partition by a.nivel, a.uf, a.k) as tvd
        from longo a
        join longo b on (b.nivel, b.uf, b.k, b.j) is not distinct from (a.nivel, a.uf, a.k, a.j)
        where a.ano = {ANO_BASE} and b.ano = {ANO_ALVO}
        """
    )


def _votos_municipio(con: duckdb.DuckDBPyConnection) -> None:
    """Os votos absolutos de cada município brasileiro no 1º e no 2º turno de ANO_ALVO, a
    variação entre eles e a trajetória do lado na frente desde o 2º turno de ANO_BASE.

    O lado na frente conta só os dois lados, como em `viradas_municipio`. `liberado` é o que
    saiu de outros, brancos, nulos e abstenção do 1º para o 2º turno: Δlula + Δflavio =
    liberado + Δaptos. Os limites por seção não usam modelo: se o Flávio cai numa seção,
    pelo menos essa queda saiu dele; e de quem votou no Flávio no 1º turno, pelo menos
    max(0, flavio_1t + lula_2t − aptos) votou no Lula no 2º.

    `virada` é quanto a diferença Lula − Flávio andou do 1º para o 2º turno (Δlula − Δflavio).
    As colunas `var_*` são a variação, em fração dos aptos, da abstenção, de brancos + nulos
    e das três juntas (o não-voto).
    """
    soma = ", ".join(f"sum({k}) as {k}" for k in K)
    turnos = f"(({ANO_BASE}, 2), ({ANO_ALVO}, 1), ({ANO_ALVO}, 2))"

    def lado(t: str) -> str:
        return (
            f"case when {t}.lula > {t}.flavio then 'lula' "
            f"when {t}.lula < {t}.flavio then 'flavio' when {t}.lula = {t}.flavio then 'empate' end"
        )

    def faixa() -> str:
        casos = " ".join(
            f"when t2.aptos <= {limite} then '{nome}'" for limite, nome in FAIXAS_APTOS if limite
        )
        return f"case {casos} else '{FAIXAS_APTOS[-1][1]}' end"

    turno_1 = ", ".join(f"t1.{k} as {k}_1t" for k in K)
    turno_2 = ", ".join(f"t2.{k} as {k}_2t" for k in K)
    delta = ", ".join(f"t2.{c} - t1.{c} as delta_{c}" for c in ("aptos", *K))
    limites = ", ".join(
        f"sum(greatest(t1.{c} - t2.{c}, 0)) as saida_min_{c}, "
        f"sum(greatest(t2.{c} - t1.{c}, 0)) as entrada_min_{c}"
        for c in ("lula", "flavio")
    )
    con.execute(
        f"""
        create table votos_municipio as
        with turno as (
            select ano, turno, uf, cd_municipio, any_value(nm_municipio) as nm_municipio,
                   sum(aptos) as aptos, {soma}
            from categorias
            where (ano, turno) in {turnos} and uf <> '{UF_EXTERIOR}'
            group by all
        ),
        secoes as (
            select uf, cd_municipio, {limites},
                   sum(greatest(t1.flavio + t2.lula - greatest(t1.aptos, t2.aptos), 0))
                       as flavio_para_lula_min,
                   sum(least(t1.flavio, t2.lula)) as flavio_para_lula_max
            from categorias t1
            join categorias t2 using (ano, uf, cd_municipio, zona, secao)
            where t1.ano = {ANO_ALVO} and t1.turno = 1 and t2.turno = 2
                and uf <> '{UF_EXTERIOR}'
            group by all
        ),
        unido as (
            select uf, cd_municipio, t2.nm_municipio,
                   t1.aptos as aptos_1t, t2.aptos as aptos_2t, {turno_1}, {turno_2}, {delta},
                   t1.outros + (t1.branco - t2.branco) + (t1.nulo - t2.nulo)
                       + (t1.abstencao - t2.abstencao) as liberado,
                   t0.lula / (t0.lula + t0.flavio) as d_{ANO_BASE},
                   t1.lula / (t1.lula + t1.flavio) as d_1t,
                   t2.lula / (t2.lula + t2.flavio) as d_2t,
                   {lado("t0")} as vencedor_{ANO_BASE},
                   {lado("t1")} as lider_1t,
                   {lado("t2")} as vencedor_2t,
                   {faixa()} as faixa_aptos
            from (select * from turno where ano = {ANO_ALVO} and turno = 1) t1
            join (select * from turno where ano = {ANO_ALVO} and turno = 2) t2
                using (uf, cd_municipio)
            left join (select * from turno where ano = {ANO_BASE}) t0 using (uf, cd_municipio)
        )
        select u.uf, u.cd_municipio, i.cd_municipio_ibge, i.regiao, i.recorte,
               i.recorte_metropolitano, u.* exclude (uf, cd_municipio),
               u.delta_lula - u.delta_flavio as virada,
               u.abstencao_2t / u.aptos_2t - u.abstencao_1t / u.aptos_1t as var_abstencao,
               (u.branco_2t + u.nulo_2t) / u.aptos_2t - (u.branco_1t + u.nulo_1t) / u.aptos_1t
                   as var_branco_nulo,
               (u.abstencao_2t + u.branco_2t + u.nulo_2t) / u.aptos_2t
                   - (u.abstencao_1t + u.branco_1t + u.nulo_1t) / u.aptos_1t as var_nao_voto,
               coalesce(u.vencedor_{ANO_BASE}, 'sem_{ANO_BASE}') || '/' || u.lider_1t || '/'
                   || u.vencedor_2t as trajetoria,
               case when (u.lider_1t, u.vencedor_2t) = ('flavio', 'lula') then 'virou_para_lula'
                    when (u.lider_1t, u.vencedor_2t) = ('lula', 'flavio')
                        then 'virou_para_flavio'
                    when (u.lider_1t, u.vencedor_2t) = ('lula', 'lula') then 'lula_manteve'
                    when (u.lider_1t, u.vencedor_2t) = ('flavio', 'flavio')
                        then 'flavio_manteve'
                    else 'empate' end as situacao,
               -- As categorias de cor do mapa do 2º turno, por cima da gradação; quem não
               -- está em nenhuma fica só na gradação (nulo).
               case when (u.vencedor_{ANO_BASE}, u.lider_1t, u.vencedor_2t)
                        = ('lula', 'flavio', 'flavio') then 'roxo'
                    when (u.vencedor_{ANO_BASE}, u.lider_1t, u.vencedor_2t)
                        = ('lula', 'flavio', 'lula') then 'especial'
                    when (u.vencedor_{ANO_BASE}, u.lider_1t, u.vencedor_2t)
                        = ('flavio', 'flavio', 'lula') then 'vermelho' end as cor_mapa,
               u.delta_lula > u.liberado as ganho_lula_maior_que_liberado,
               s.* exclude (uf, cd_municipio)
        from unido u
        left join municipio_ibge i on i.uf = u.uf and i.cd_municipio_tse = u.cd_municipio
        left join secoes s on (s.uf, s.cd_municipio) = (u.uf, u.cd_municipio)
        """
    )
    [(faltando,)] = con.execute(
        "select count(*) from votos_municipio where cd_municipio_ibge is null"
    ).fetchall()
    if faltando:
        raise GoldInconsistente(f"{faltando} municípios do 2º turno de {ANO_ALVO} sem IBGE")
    cenarios = [c for (c,) in con.execute("select distinct cenario from projecao").fetchall()]
    for cenario in sorted(cenarios):
        con.execute(
            f"""
            alter table votos_municipio add column lula_projetado_{cenario} double;
            alter table votos_municipio add column flavio_projetado_{cenario} double;
            update votos_municipio v
            set lula_projetado_{cenario} = c.lula_projetado,
                flavio_projetado_{cenario} = c.flavio_projetado
            from _confronto_municipio c
            where c.cenario = '{cenario}'
                and (c.uf, c.cd_municipio) = (v.uf, v.cd_municipio)
            """
        )


def _fluxo(con: duckdb.DuckDBPyConnection) -> None:
    """Os votos de cada categoria do 1º turno de ANO_ALVO que o β de ANO_ALVO manda para cada
    categoria do 2º, por município brasileiro. É uma estimativa da inferência ecológica, não
    voto individual. A linha `residuo` é o 2º turno real menos a soma das estimadas, então
    as 7 linhas de um município somam o 2º turno real.

    Cada seção usa o β da sua zona (o próprio ou o da UF, ADR 0002); no mesmo ano, a zona
    da seção é a do β, sem ligação.
    """
    fluxo = ", ".join(f"sum(n.votos * b.{j}) as {j}" for j in J)
    con.execute(
        f"""
        create table fluxo_municipio as
        with unidade as (
            select uf, cd_municipio, zona, {", ".join(f"sum({k}) as {k}" for k in K)}
            from categorias
            where ano = {ANO_ALVO} and turno = 1 and uf <> '{UF_EXTERIOR}'
            group by all
        ),
        n as (unpivot unidade on {", ".join(K)} into name k value votos)
        select n.uf, n.cd_municipio, n.k, {fluxo}
        from n
        join beta b on b.ano = {ANO_ALVO} and b.nivel = 'zona'
            and (b.uf, b.zona, b.k) = (n.uf, n.zona, n.k)
        group by all
        """
    )
    [(sem_beta,)] = con.execute(
        f"""
        select count(*) from (
            select uf, cd_municipio from votos_municipio
            except select uf, cd_municipio from fluxo_municipio
            group by all having count(*) = {len(K)}
        )
        """
    ).fetchall()
    if sem_beta:
        raise GoldInconsistente(f"{sem_beta} municípios de {ANO_ALVO} sem β de {ANO_ALVO}")
    residuo = ", ".join(f"any_value(v.{j}_2t) - sum(f.{j}) as {j}" for j in J)
    con.execute(
        f"""
        insert into fluxo_municipio
        select f.uf, f.cd_municipio, 'residuo', {residuo}
        from fluxo_municipio f
        join votos_municipio v using (uf, cd_municipio)
        group by all
        """
    )


def _resumo_2026(con: duckdb.DuckDBPyConnection) -> None:
    """Somas de `votos_municipio` e de `fluxo_municipio` por grupo de municípios.

    Cada `dimensao` cobre os municípios brasileiros uma vez: `brasil`, `uf`, `situacao`,
    `cor_mapa` (`gradacao` para os que ficam só na gradação), `trajetoria`, `faixa_aptos`,
    `regiao`, `recorte` (capital, entorno da capital ou interior) e `recorte_faixa` (o
    interior partido pela faixa de aptos).
    As colunas `pct_*` são a parte do grupo no total da dimensão.
    """
    dimensoes = {
        "brasil": "'brasil'",
        "uf": "uf",
        "situacao": "situacao",
        "cor_mapa": "coalesce(cor_mapa, 'gradacao')",
        "trajetoria": "trajetoria",
        "faixa_aptos": "faixa_aptos",
        "regiao": "regiao",
        "recorte": "recorte",
        "recorte_faixa": "case when recorte = 'interior' then 'interior/' || faixa_aptos "
        "else recorte end",
    }
    con.execute(
        "create table _grupo_municipio as "
        + " union all ".join(
            f"select uf, cd_municipio, '{d}' as dimensao, {g} as grupo from votos_municipio"
            for d, g in dimensoes.items()
        )
    )
    colunas = [
        c
        for (c,) in con.execute(
            "select column_name from duckdb_columns() where table_name = 'votos_municipio' "
            "and data_type in ('BIGINT', 'HUGEINT', 'DOUBLE', 'INTEGER') "
            "and not starts_with(column_name, 'd_') and not starts_with(column_name, 'var_') "
            "and column_name not in ('cd_municipio', 'cd_municipio_ibge') order by column_index"
        ).fetchall()
    ]
    cenarios = [c.removeprefix("lula_projetado_") for c in colunas if "lula_projetado_" in c]
    margens = ", ".join(
        f"sum(v.lula_projetado_{c} - v.flavio_projetado_{c}) as margem_projetada_{c}, "
        f"count(*) filter (where (v.lula_projetado_{c} > v.flavio_projetado_{c})"
        f" = (v.lula_2t > v.flavio_2t)) as acertos_{c}"
        for c in cenarios
    )
    pct = ", ".join(
        f"{c} / sum({c}) over (partition by dimensao) as pct_{c}"
        for c in ("aptos_2t", "delta_lula", "delta_flavio", "margem_real", "virada")
    )
    erros = ", ".join(
        f"margem_projetada_{c} - margem_real as erro_margem_{c}, "
        f"(margem_projetada_{c} - margem_real) "
        f"/ sum(margem_projetada_{c} - margem_real) over (partition by dimensao) "
        f"as pct_erro_margem_{c}"
        for c in cenarios
    )
    con.execute(
        f"""
        create table resumo_2026 as
        with soma as (
            select g.dimensao, g.grupo, count(*) as municipios,
                   {", ".join(f"sum(v.{c}) as {c}" for c in colunas)},
                   count(*) filter (where v.ganho_lula_maior_que_liberado)
                       as municipios_ganho_lula_maior_que_liberado,
                   sum(v.lula_2t - v.flavio_2t) as margem_real,
                   sum(v.lula_1t - v.flavio_1t) as margem_1t,
                   {margens}
            from _grupo_municipio g
            join votos_municipio v using (uf, cd_municipio)
            group by g.dimensao, g.grupo
        )
        select *, lula_2t / (lula_2t + flavio_2t) as pct_lula_validos_2t, {pct}, {erros}
        from soma
        """
    )
    con.execute(
        f"""
        create table fluxo_resumo as
        select g.dimensao, g.grupo, f.k, {", ".join(f"sum(f.{j}) as {j}" for j in J)}
        from _grupo_municipio g
        join fluxo_municipio f using (uf, cd_municipio)
        group by all
        """
    )


def _gravar(con: duckdb.DuckDBPyConnection, saida: Path) -> list[Tabela]:
    fracoes = ", ".join(f"{k} / aptos as {k}" for k in K)
    consultas = {
        "lados": "select * from lados",
        "categorias_secao": f"""
            select ano, turno, uf, cd_municipio, nm_municipio, zona, secao, aptos, {fracoes}
            from categorias
        """,
        "ligacao_2026": f"select * from ligacao_{ANO_ALVO}",
        "projecao_2026": "select * from projecao",
        "taxa_match": "select * from taxa_match",
        "municipio_ibge": "select * from municipio_ibge",
        "referencia_externa": "select * from referencia_externa",
        "viradas_municipio": "select * from viradas",
        "viradas_municipio_resumo": "select * from viradas_resumo",
        f"primeiro_turno_{ANO_ALVO}": "select * from primeiro_turno",
    }
    if con.execute("select 1 from duckdb_tables() where table_name = 'backtest'").fetchall():
        consultas |= {
            f"ligacao_{ANO_BASE}": f"select * from ligacao_{ANO_BASE}",
            f"backtest_{ANO_BASE}": "select * from backtest",
            f"backtest_{ANO_BASE}_acerto": "select * from backtest_acerto",
            "estabilidade_transferencia": "select * from estabilidade",
        }
    if con.execute("select 1 from duckdb_tables() where table_name = 'confronto'").fetchall():
        consultas |= {
            f"confronto_{ANO_ALVO}": "select * from confronto",
            f"confronto_{ANO_ALVO}_acerto": "select * from confronto_acerto",
            f"comparacao_transferencia_{ANO_ALVO}": "select * from comparacao_transferencia",
            f"votos_municipio_{ANO_ALVO}": "select * from votos_municipio",
            f"fluxo_municipio_{ANO_ALVO}": "select * from fluxo_municipio",
            f"resumo_{ANO_ALVO}": "select * from resumo_2026",
            f"fluxo_resumo_{ANO_ALVO}": "select * from fluxo_resumo",
        }
    for (ano,) in con.execute("select distinct ano from painel").fetchall():
        consultas[f"painel_secao_{ano}"] = f"select * exclude (ano) from painel where ano = {ano}"
        consultas[f"transferencia_zona_{ano}"] = (
            f"select * exclude (ano) from beta where ano = {ano}"
        )

    tabelas = []
    for nome, consulta in consultas.items():
        con.execute(f"copy ({consulta} order by all) to '{saida / nome}.parquet' (format parquet)")
        [(linhas,)] = con.execute(f"select count(*) from ({consulta})").fetchall()
        tabelas.append(Tabela(nome, linhas))
    return tabelas
