"""Conferência, seção a seção, de dois silvers do mesmo turno vindos de fontes diferentes.

O uso previsto é o BU dos arquivos de urna contra o bweb do CKAN: os dois são o mesmo boletim,
então qualquer diferença é de ingestão ou de publicação. Cada silver é construído à parte por
`construcao.construir_fonte`.
"""

from dataclasses import dataclass
from pathlib import Path

import duckdb

# Os totais de `secao` comparados; os votos de cada candidato entram como `votos_{numero}`.
CAMPOS = ("aptos", "comparecimento", "abstencao", "branco", "nulo", "nominais")


@dataclass(frozen=True)
class Diferenca:
    uf: str
    cd_municipio: int
    zona: int
    secao: int
    campo: str
    valor_a: int | None  # None: a seção não está nesse silver (ou foi para a quarentena)
    valor_b: int | None


def conferir_secoes(silver_a: Path, silver_b: Path, ano: int, turno: int) -> list[Diferenca]:
    """Cada (seção, campo) em que os dois silvers divergem, inclusive a seção que só um tem."""
    con = duckdb.connect()
    for lado, silver in (("a", silver_a), ("b", silver_b)):
        particao = Path(f"ano={ano}") / f"turno={turno}"
        con.execute(
            f"""
            create table {lado} as
            unpivot (
                select uf, cd_municipio, zona, secao, {", ".join(CAMPOS)}
                from read_parquet($secao)
            ) on {", ".join(CAMPOS)} into name campo value valor
            union all
            select uf, cd_municipio, zona, secao, 'votos_' || nr_votavel, votos
            from read_parquet($votos)
            -- um candidato sem voto na seção pode vir com 0 numa fonte e faltar na outra
            where votos > 0
            """,
            {
                "secao": str(silver / "secao" / particao / "*.parquet"),
                "votos": str(silver / "votos" / particao / "*.parquet"),
            },
        )
    linhas = con.execute(
        """
        select
            coalesce(a.uf, b.uf), coalesce(a.cd_municipio, b.cd_municipio),
            coalesce(a.zona, b.zona), coalesce(a.secao, b.secao), coalesce(a.campo, b.campo),
            a.valor, b.valor
        from a full join b using (uf, cd_municipio, zona, secao, campo)
        where a.valor is distinct from b.valor
        order by all
        """
    ).fetchall()
    return [Diferenca(*linha) for linha in linhas]
