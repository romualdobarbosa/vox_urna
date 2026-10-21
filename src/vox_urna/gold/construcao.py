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

# Correspondência entre os códigos de município do TSE e do IBGE, copiada de
# https://github.com/betafcc/Municipios-Brasileiros-TSE (municipios_brasileiros_tse.csv,
# commit fe2694220eb7280a3df01e985641f34078989aa8, de 2018). Cobre os 5570 municípios até 2024.
CORRESPONDENCIA_IBGE = Path(__file__).parent / "municipios_tse_ibge.csv"
# Municípios instalados depois da fonte acima: (uf, código TSE, código IBGE). O código IBGE
# vem de https://servicodados.ibge.gov.br/api/v1/localidades/estados/MT/municipios.
MUNICIPIOS_NOVOS = [("MT", 73709, 5101837)]  # Boa Esperança do Norte, instalado em 2025
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
        anos_painel = sorted(
            ano for ano, turno in turnos if turno == 1 and (ano, 2) in turnos and ano != ANO_ALVO
        )
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
    con.execute(
        f"""
        create table backtest as
        select case when grouping(uf) = 1 then 'brasil'
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
        from _backtest_municipio
        group by grouping sets ((), (uf), (uf, cd_municipio))
        """
    )
    # A % de unidades em que o lado vencedor bateu, sem ponderar e ponderada pelos aptos
    # do 1º turno. O exterior (ZZ) conta como uma UF a mais, como na projeção.
    con.execute(
        """
        create table backtest_acerto as
        select nivel, count(*) as unidades, count(*) filter (where acertou) as acertos,
               avg(acertou::int) as pct_acerto,
               sum(aptos) filter (where acertou) / sum(aptos) as pct_acerto_aptos
        from backtest
        where nivel <> 'brasil'
        group by nivel
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
        select uf, codigo_tse as cd_municipio_tse, codigo_ibge as cd_municipio_ibge
        from read_csv($csv, header = true, columns = {
            'codigo_tse': 'integer', 'uf': 'varchar', 'nome_municipio': 'varchar',
            'capital': 'integer', 'codigo_ibge': 'integer'
        })
        """,
        {"csv": str(CORRESPONDENCIA_IBGE)},
    )
    con.executemany("insert into _correspondencia values (?, ?, ?)", MUNICIPIOS_NOVOS)
    con.execute(
        f"""
        create table municipio_ibge as
        select m.uf, m.cd_municipio as cd_municipio_tse, c.cd_municipio_ibge, m.nm_municipio
        from (
            select uf, cd_municipio, any_value(nm_municipio) as nm_municipio
            from categorias where ano = {ANO_ALVO} and turno = 1 and uf <> 'ZZ'
            group by all
        ) m
        left join _correspondencia c on c.uf = m.uf and c.cd_municipio_tse = m.cd_municipio
        """
    )
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
    }
    if con.execute("select 1 from duckdb_tables() where table_name = 'backtest'").fetchall():
        consultas |= {
            f"ligacao_{ANO_BASE}": f"select * from ligacao_{ANO_BASE}",
            f"backtest_{ANO_BASE}": "select * from backtest",
            f"backtest_{ANO_BASE}_acerto": "select * from backtest_acerto",
            "estabilidade_transferencia": "select * from estabilidade",
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
