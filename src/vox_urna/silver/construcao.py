import json
import logging
import multiprocessing
import shutil
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from vox_urna.bronze.manifest import Entrada, Manifest, Status
from vox_urna.silver.extracao import extrair_presidente

log = logging.getLogger(__name__)

LIMITE_REJEICAO = 0.001
TABELAS = ("secao", "votos", "rejeitadas")


class RejeicaoExcessiva(RuntimeError):
    pass


@dataclass(frozen=True)
class Construcao:
    ano: int
    turno: int
    secoes: int
    rejeitadas: int
    pulado: bool = False


def construir(
    dir_dados: Path,
    *,
    force: bool = False,
    workers: int = 8,
    limite_rejeicao: float = LIMITE_REJEICAO,
) -> list[Construcao]:
    """Constrói o silver de cada (ano, turno) baixado no bronze cujo conteúdo mudou."""
    dir_bronze, dir_silver = dir_dados / "bronze", dir_dados / "silver"
    registro_path = dir_silver / "_construcao.json"
    registro = json.loads(registro_path.read_text()) if registro_path.exists() else {}

    por_turno: dict[tuple[int, int], list[Entrada]] = defaultdict(list)
    for entrada in Manifest(dir_bronze / "manifest.csv").entradas:
        if entrada.status is Status.SUCCESS:
            por_turno[(entrada.ano, entrada.turno)].append(entrada)

    resultados = []
    for (ano, turno), entradas in sorted(por_turno.items()):
        chave = f"{ano}-{turno}"
        impressao = sorted(e.sha512 for e in entradas)
        anterior = registro.get(chave)
        if not force and anterior and anterior["bronze_sha512"] == impressao:
            resultados.append(
                Construcao(ano, turno, anterior["secoes"], anterior["rejeitadas"], pulado=True)
            )
            continue

        resultado = _construir_turno(
            dir_bronze, dir_silver, ano, turno, entradas, workers, limite_rejeicao
        )
        registro[chave] = {
            "bronze_sha512": impressao,
            "secoes": resultado.secoes,
            "rejeitadas": resultado.rejeitadas,
            "construido_em": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        registro_path.write_text(json.dumps(registro, indent=2, sort_keys=True))
        resultados.append(resultado)

    for temporario in ("_staging", "_tmp"):
        shutil.rmtree(dir_silver / temporario, ignore_errors=True)
    return resultados


def _construir_turno(
    dir_bronze: Path,
    dir_silver: Path,
    ano: int,
    turno: int,
    entradas: list[Entrada],
    workers: int,
    limite_rejeicao: float,
) -> Construcao:
    particao = Path(f"ano={ano}") / f"turno={turno}"
    staging = dir_silver / "_staging" / particao
    saida_tmp = dir_silver / "_tmp" / particao
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(saida_tmp, ignore_errors=True)

    log.info("%s/%s: extraindo Presidente de %d zips", ano, turno, len(entradas))
    # spawn: o processo pai pode ter threads do DuckDB, e fork com threads pode travar.
    contexto = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=contexto) as executor:
        list(
            executor.map(
                extrair_presidente,
                [dir_bronze / e.caminho for e in entradas],
                [staging / f"{e.uf}.csv" for e in entradas],
            )
        )

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

    log.info("%s/%s: %d seções, %d em quarentena", ano, turno, secoes, rejeitadas)
    return Construcao(ano, turno, secoes - rejeitadas, rejeitadas)


def _carregar(con: duckdb.DuckDBPyConnection, staging: Path) -> None:
    con.execute(
        """
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
            DS_SECOES_AGREGADAS as secoes_agregadas
        from read_csv($glob, delim = ';', header = true, all_varchar = true)
        where CD_CARGO_PERGUNTA = '1'
        """,
        {"glob": str(staging / "*.csv")},
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
                   aptos, comparecimento, abstencao, branco, nulo, nominais, secoes_agregadas
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
