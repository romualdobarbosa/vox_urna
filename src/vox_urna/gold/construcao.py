import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np

from vox_urna.gold.lados import LADOS
from vox_urna.gold.transferencia import J, K, estimar_beta

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
        refs = [ano for ano in (ANO_BASE, ANO_SENSIBILIDADE) if ano in anos_painel]
        _ligacao(con, refs)
        _projecao(con, sensibilidade_2018=ANO_SENSIBILIDADE in refs)
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

    def registrar(ano, nivel, uf, zona, secoes, aptos, fonte, beta):
        for i, k in enumerate(K):
            linhas.append((ano, nivel, uf, zona, secoes, aptos, fonte, k, *beta[i]))

    grupo_uf = np.char.add(dados["ano"].astype(str), np.asarray(dados["uf"], dtype=str))
    for g in np.unique(grupo_uf):
        no_uf = np.flatnonzero(grupo_uf == g)
        ano, uf = int(dados["ano"][no_uf[0]]), str(dados["uf"][no_uf[0]])
        beta_uf = estimar_beta(x[no_uf], y[no_uf], peso[no_uf])
        registrar(ano, "uf", uf, None, len(no_uf), peso[no_uf].sum(), "uf", beta_uf)
        zonas = dados["zona"][no_uf]
        for zona in np.unique(zonas):
            idx = no_uf[zonas == zona]
            if len(idx) >= min_secoes:
                beta, fonte = estimar_beta(x[idx], y[idx], peso[idx]), "zona"
            else:
                beta, fonte = beta_uf, "uf"
            registrar(ano, "zona", uf, int(zona), len(idx), peso[idx].sum(), fonte, beta)
        log.info("%s %s: β de %d zonas", ano, uf, len(np.unique(zonas)))

    nomes = ["ano", "nivel", "uf", "zona", "secoes", "aptos", "fonte", "k", *J]
    con.register(
        "_beta",
        {nome: np.array([linha[i] for linha in linhas]) for i, nome in enumerate(nomes)},
    )
    con.execute(
        """
        create table beta as
        select cast(ano as integer) as ano, nivel, uf, cast(zona as integer) as zona,
               cast(secoes as integer) as secoes, cast(aptos as bigint) as aptos, fonte, k,
               lula, flavio, branco, nulo, abstencao
        from _beta
        """
    )


def _ligacao(con: duckdb.DuckDBPyConnection, refs: list[int]) -> None:
    """Liga cada (UF, município, zona) de 2026 ao β de cada eleição de referência (ADR 0002).

    Passos: (1) a zona da referência com o mesmo par (município, zona); (2) as zonas que
    continham o município, ponderadas pelos aptos; (3) a UF.
    """
    media = ", ".join(f"sum(r.aptos * b.{j}) / sum(r.aptos) as {j}" for j in J)
    direto = ", ".join(f"b.{j}" for j in J)
    con.execute(
        f"""
        create table ligacao as
        with unidade as (
            select uf, cd_municipio, zona, count(*) as secoes, sum(aptos) as aptos
            from categorias where ano = {ANO_ALVO} and turno = 1
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
            from categorias where ano = {ANO_ALVO} and turno = 1
        ) u
        cross join (select unnest($refs) as ano_ref) a
        left join (
            select ano_ref, uf, cd_municipio, zona, count(*) as n from ligacao group by all
        ) l using (ano_ref, uf, cd_municipio, zona)
        where coalesce(l.n, 0) <> {len(K)}
        """,
        {"refs": refs},
    ).fetchall()
    if faltando:
        raise GoldInconsistente(f"{faltando} (município, zona) de {ANO_ALVO} sem β ligado")
    con.execute(
        f"""
        insert into taxa_match
        select 'ligacao_{ANO_ALVO}_ref_' || ano_ref, passo,
               sum(secoes), sum(aptos), sum(aptos) / sum(sum(aptos)) over (partition by ano_ref)
        from (select distinct ano_ref, uf, cd_municipio, zona, secoes, aptos, passo from ligacao)
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
            from ligacao where ano_ref = {ANO_SENSIBILIDADE} and k = 'outros'
        """
    else:
        outros = f"""
            select 'outros_divididos' as cenario, uf, cd_municipio, zona, k,
                   0.5 as lula, 0.5 as flavio, 0.0 as branco, 0.0 as nulo, 0.0 as abstencao
            from ligacao where ano_ref = {ANO_BASE} and k = 'outros'
        """
    sensibilidade = f"outros_{ANO_SENSIBILIDADE}" if sensibilidade_2018 else "outros_divididos"
    soma = ", ".join(f"sum(n.votos * b.{j}) as {j}" for j in J)
    total = ", ".join(f"sum({j}) as {j}" for j in J)
    con.execute(
        f"""
        create table projecao as
        with cenario as (
            select 'base' as cenario, uf, cd_municipio, zona, k, {", ".join(J)}
            from ligacao where ano_ref = {ANO_BASE}
            union all
            select '{sensibilidade}', uf, cd_municipio, zona, k, {", ".join(J)}
            from ligacao where ano_ref = {ANO_BASE} and k <> 'outros'
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
        "ligacao_2026": "select * from ligacao",
        "projecao_2026": "select * from projecao",
        "taxa_match": "select * from taxa_match",
        "municipio_ibge": "select * from municipio_ibge",
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
