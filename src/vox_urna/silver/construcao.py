import hashlib
import json
import logging
import multiprocessing
import shutil
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import duckdb

from vox_urna.bronze.manifest import Entrada, Manifest, Status
from vox_urna.bronze.urna import EntradaUrna
from vox_urna.silver.extracao import extrair_presidente
from vox_urna.silver.urna import candidatos_divulgados, extrair_presidente_urna

log = logging.getLogger(__name__)

LIMITE_REJEICAO = 0.001
TABELAS = ("secao", "votos", "rejeitadas")

# De onde vem o BU de um (ano, turno): o CSV do CKAN (bweb) ou os arquivos de urna do site de
# resultados, que saem dias antes. O CKAN tem precedência quando publica o turno.
CKAN, URNA = "ckan", "urna"


class RejeicaoExcessiva(RuntimeError):
    pass


@dataclass(frozen=True)
class Construcao:
    ano: int
    turno: int
    secoes: int
    rejeitadas: int
    fonte: str
    pulado: bool = False


def construir(
    dir_dados: Path,
    *,
    force: bool = False,
    workers: int = 8,
    limite_rejeicao: float = LIMITE_REJEICAO,
) -> list[Construcao]:
    """Constrói o silver de cada (ano, turno) baixado no bronze cujo conteúdo mudou.

    A fonte de cada turno fica no `_construcao.json` (`fonte`) e no `Construcao` devolvido.
    """
    dir_bronze, dir_silver = dir_dados / "bronze", dir_dados / "silver"
    registro_path = dir_silver / "_construcao.json"
    registro = json.loads(registro_path.read_text()) if registro_path.exists() else {}

    fontes = {chave: (URNA, entradas) for chave, entradas in _entradas_urna(dir_bronze).items()}
    fontes |= {chave: (CKAN, entradas) for chave, entradas in _entradas_ckan(dir_bronze).items()}

    resultados = []
    for (ano, turno), (fonte, entradas) in sorted(fontes.items()):
        chave = f"{ano}-{turno}"
        impressao = _impressao(fonte, entradas)
        anterior = registro.get(chave)
        if not force and anterior and anterior["bronze_sha512"] == impressao:
            resultados.append(
                Construcao(
                    ano, turno, anterior["secoes"], anterior["rejeitadas"], fonte, pulado=True
                )
            )
            continue

        resultado = _construir_turno(
            dir_dados, dir_silver, ano, turno, fonte, entradas, workers, limite_rejeicao
        )
        registro[chave] = {
            "bronze_sha512": impressao,
            "fonte": fonte,
            "secoes": resultado.secoes,
            "rejeitadas": resultado.rejeitadas,
            "construido_em": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        registro_path.write_text(json.dumps(registro, indent=2, sort_keys=True))
        resultados.append(resultado)

    for temporario in ("_staging", "_tmp"):
        shutil.rmtree(dir_silver / temporario, ignore_errors=True)
    return resultados


def construir_fonte(
    dir_dados: Path,
    destino: Path,
    ano: int,
    turno: int,
    fonte: str,
    *,
    workers: int = 8,
    limite_rejeicao: float = LIMITE_REJEICAO,
) -> Construcao:
    """Constrói um (ano, turno) de uma fonte escolhida num silver à parte, em `destino`.

    É o que permite ter as duas fontes do mesmo turno lado a lado para conferir uma contra a
    outra (`silver.conferencia`), sem mexer no silver principal nem no `_construcao.json`.
    """
    dir_bronze = dir_dados / "bronze"
    por_fonte = {CKAN: _entradas_ckan, URNA: _entradas_urna}
    entradas = por_fonte[fonte](dir_bronze).get((ano, turno))
    if not entradas:
        raise RuntimeError(f"{ano}/{turno}: nada baixado no bronze da fonte {fonte}")
    try:
        return _construir_turno(
            dir_dados, destino, ano, turno, fonte, entradas, workers, limite_rejeicao
        )
    finally:
        for temporario in ("_staging", "_tmp"):
            shutil.rmtree(destino / temporario, ignore_errors=True)


def _entradas_ckan(dir_bronze: Path) -> dict[tuple[int, int], list[Entrada]]:
    por_turno: dict[tuple[int, int], list[Entrada]] = defaultdict(list)
    for entrada in Manifest(dir_bronze / "manifest.csv").entradas:
        if entrada.status is Status.SUCCESS:
            por_turno[(entrada.ano, entrada.turno)].append(entrada)
    return por_turno


def _entradas_urna(dir_bronze: Path) -> dict[tuple[int, int], list[EntradaUrna]]:
    """As seções baixadas dos arquivos de urna, de todos os manifests por UF."""
    por_turno: dict[tuple[int, int], list[EntradaUrna]] = defaultdict(list)
    for caminho in sorted((dir_bronze / "urna").glob("ano=*/turno=*/*/manifest.csv")):
        for entrada in Manifest(caminho, EntradaUrna).entradas:
            if entrada.status is Status.SUCCESS:
                por_turno[(entrada.ano, entrada.turno)].append(entrada)
    return por_turno


def _impressao(fonte: str, entradas: list[Entrada] | list[EntradaUrna]) -> list[str]:
    """Os sha512 do bronze usados no turno, que dizem se ele precisa ser reconstruído.

    Com os arquivos de urna são ~500 mil BUs; no registro vai só o sha512 da lista ordenada.
    """
    impressao = sorted(e.sha512 for e in entradas)
    if fonte == URNA:
        return [hashlib.sha512("\n".join(impressao).encode()).hexdigest()]
    return impressao


def _extracoes(
    dir_dados: Path,
    staging: Path,
    ano: int,
    turno: int,
    fonte: str,
    entradas: list[Entrada] | list[EntradaUrna],
) -> list[Callable[[], int]]:
    """Uma extração por UF, cada uma gravando no staging um CSV no formato do bweb."""
    if fonte == CKAN:
        return [
            partial(extrair_presidente, dir_dados / "bronze" / e.caminho, staging / f"{e.uf}.csv")
            for e in entradas
        ]
    # O BU só traz o número do candidato; o nome e o partido vêm dos resultados divulgados.
    candidatos = candidatos_divulgados(dir_dados / "silver", ano, turno)
    return [
        partial(
            extrair_presidente_urna,
            dir_dados / "bronze" / "urna",
            ano,
            turno,
            uf,
            staging / f"{uf}.csv",
            candidatos=candidatos,
        )
        for uf in sorted({e.uf for e in entradas})
    ]


def _executar(extracao: Callable[[], int]) -> int:
    return extracao()


def _construir_turno(
    dir_dados: Path,
    dir_silver: Path,
    ano: int,
    turno: int,
    fonte: str,
    entradas: list[Entrada] | list[EntradaUrna],
    workers: int,
    limite_rejeicao: float,
) -> Construcao:
    particao = Path(f"ano={ano}") / f"turno={turno}"
    staging = dir_silver / "_staging" / particao
    saida_tmp = dir_silver / "_tmp" / particao
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(saida_tmp, ignore_errors=True)

    extracoes = _extracoes(dir_dados, staging, ano, turno, fonte, entradas)
    log.info(
        "%s/%s: extraindo Presidente de %d arquivos (fonte %s, %d no bronze)",
        ano, turno, len(extracoes), fonte, len(entradas),
    )  # fmt: skip
    # spawn: o processo pai pode ter threads do DuckDB, e fork com threads pode travar.
    contexto = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=contexto) as executor:
        list(executor.map(_executar, extracoes))

    try:
        con = duckdb.connect()
        con.execute("set enable_progress_bar = false")
        _carregar(con, staging)
        secoes, rejeitadas = con.execute("select count(*), count(motivo) from secoes").fetchone()
        if secoes == 0:
            raise RuntimeError(f"{ano}/{turno}: nenhuma seção de Presidente encontrada")
        for motivo, n in con.execute(
            "select motivo, count(*) from secoes where motivo is not null group by motivo"
        ).fetchall():
            log.warning("%s/%s: %d seções em quarentena por %s", ano, turno, n, motivo)
        if rejeitadas / secoes > limite_rejeicao:
            raise RejeicaoExcessiva(
                f"{ano}/{turno}: {rejeitadas} de {secoes} seções rejeitadas "
                f"({rejeitadas / secoes:.3%}), acima do limite de {limite_rejeicao:.3%}"
            )
        _gravar(con, saida_tmp)
        con.close()

        for tabela in TABELAS:
            destino = dir_silver / tabela / particao
            shutil.rmtree(destino, ignore_errors=True)
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(saida_tmp / tabela, destino)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(saida_tmp, ignore_errors=True)

    log.info("%s/%s: %d seções, %d em quarentena (fonte %s)", ano, turno, secoes, rejeitadas, fonte)
    return Construcao(ano, turno, secoes - rejeitadas, rejeitadas, fonte)


def _carregar(con: duckdb.DuckDBPyConnection, staging: Path) -> None:
    glob = str(staging / "*.csv")
    colunas = con.execute(
        "select * from read_csv($glob, delim = ';', header = true, all_varchar = true) limit 0",
        {"glob": glob},
    ).description
    # Só o CSV tirado dos arquivos de urna traz a situação da seção; no bweb ela fica nula.
    st_secao = "ST_SECAO" if "ST_SECAO" in {c[0] for c in colunas} else "null"
    con.execute(
        f"""
        create table linhas as
        select
            SG_UF as uf,
            cast(CD_MUNICIPIO as integer) as cd_municipio,
            NM_MUNICIPIO as nm_municipio,
            cast(NR_ZONA as integer) as zona,
            cast(NR_SECAO as integer) as secao,
            cast(CD_TIPO_VOTAVEL as integer) as tipo_votavel,
            cast(NR_VOTAVEL as integer) as nr_votavel,
            NM_VOTAVEL as nm_votavel,
            SG_PARTIDO as sg_partido,
            cast(QT_VOTOS as integer) as votos,
            cast(QT_APTOS as integer) as aptos,
            cast(QT_COMPARECIMENTO as integer) as comparecimento,
            cast(QT_ABSTENCOES as integer) as abstencao,
            DS_TIPO_URNA as tipo_urna,
            DS_SECOES_AGREGADAS as secoes_agregadas,
            cast({st_secao} as varchar) as st_secao
        from read_csv($glob, delim = ';', header = true, all_varchar = true)
        where CD_CARGO_PERGUNTA = '1'
        """,
        {"glob": glob},
    )
    # Cada seção recebe no máximo um motivo de quarentena (ADR 0001), checados nesta ordem.
    con.execute(
        """
        create table secoes as
        with agregado as (
            select
                uf, cd_municipio, zona, secao,
                any_value(nm_municipio) as nm_municipio,
                min(aptos) as aptos,
                min(comparecimento) as comparecimento,
                min(abstencao) as abstencao,
                cast(coalesce(sum(votos) filter (where tipo_votavel = 2), 0) as integer) as branco,
                cast(coalesce(sum(votos) filter (where tipo_votavel = 3), 0) as integer) as nulo,
                cast(coalesce(sum(votos) filter (where tipo_votavel = 1), 0) as integer)
                    as nominais,
                cast(sum(votos) as integer) as total_votos,
                any_value(secoes_agregadas) as secoes_agregadas,
                any_value(st_secao) as st_secao,
                bool_or(lower(tipo_urna) <> 'apurada') as urna_anulada,
                bool_or(tipo_votavel not in (1, 2, 3)) as tipo_desconhecido,
                count(*) <> count(distinct nr_votavel) as duplicada,
                count(distinct aptos) > 1
                    or count(distinct comparecimento) > 1
                    or count(distinct abstencao) > 1 as totais_inconsistentes
            from linhas
            group by uf, cd_municipio, zona, secao
        )
        select
            *,
            case
                when urna_anulada then 'urna_anulada'
                when tipo_desconhecido then 'tipo_votavel_desconhecido'
                when duplicada then 'secao_duplicada'
                when totais_inconsistentes then 'totais_inconsistentes'
                when total_votos <> comparecimento then 'votos_diferente_do_comparecimento'
                when aptos <> comparecimento + abstencao
                    then 'aptos_diferente_de_comparecimento_mais_abstencao'
            end as motivo
        from agregado
        """
    )


def _gravar(con: duckdb.DuckDBPyConnection, saida: Path) -> None:
    consultas = {
        "secao": """
            select uf, cd_municipio, nm_municipio, zona, secao,
                   aptos, comparecimento, abstencao, branco, nulo, nominais, secoes_agregadas,
                   st_secao
            from secoes where motivo is null
        """,
        "votos": """
            select l.uf, l.cd_municipio, l.zona, l.secao,
                   l.nr_votavel, l.nm_votavel, l.sg_partido, l.votos
            from linhas l
            join secoes s using (uf, cd_municipio, zona, secao)
            where s.motivo is null and l.tipo_votavel = 1
        """,
        "rejeitadas": """
            select uf, cd_municipio, nm_municipio, zona, secao, motivo
            from secoes where motivo is not null
        """,
    }
    for tabela, consulta in consultas.items():
        destino = saida / tabela
        destino.mkdir(parents=True)
        # Uma tabela vazia (ex: nenhuma rejeitada) ainda precisa de um arquivo com o schema.
        con.execute(
            f"copy ({consulta} order by all) to '{destino / 'dados.parquet'}' (format parquet)"
        )
