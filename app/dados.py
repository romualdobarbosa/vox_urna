"""Leitura das tabelas gold e os recortes que o dashboard mostra.

Fica separado do app Streamlit para ser testável sem subir a interface.
"""

from dataclasses import dataclass
from pathlib import Path

import polars as pl

DIR_GOLD = Path(__file__).resolve().parent.parent / "data" / "gold"
ANO_ALVO = 2026
LADOS = ("lula", "flavio")
UF_EXTERIOR = "ZZ"

AVISO = (
    "Não é pesquisa eleitoral. Não houve coleta de opinião com eleitores: é uma projeção "
    "com metodologia própria, a partir dos dados oficiais das urnas, abertos a qualquer cidadão."
)

# O BU traz o nome em maiúsculas e sem acento.
NOMES_EXIBICAO = {"FLAVIO BOLSONARO": "Flávio Bolsonaro", "JAIR BOLSONARO": "Jair Bolsonaro"}


@dataclass(frozen=True)
class Cenario:
    titulo: str
    suposicoes: str


CENARIOS = {
    "base": Cenario(
        titulo="Base: transferência de 2022",
        suposicoes=(
            "Em cada zona, os eleitores de cada categoria do 1º turno de 2026 (Lula, Flávio "
            "Bolsonaro, outros candidatos, branco, nulo e abstenção) se distribuem no 2º turno "
            "como a categoria equivalente se distribuiu em 2022. Quem votou em Lula no 1º turno "
            "se comporta como quem votou em Lula em 2022; quem votou em Flávio se comporta como "
            "quem votou em Jair Bolsonaro em 2022; e quem votou em outros candidatos se comporta "
            "como os eleitores de Ciro, Tebet e dos demais em 2022."
        ),
    ),
    "outros_2018": Cenario(
        titulo="Sensibilidade: outros como em 2018",
        suposicoes=(
            "Igual ao cenário base, exceto pelos eleitores de outros candidatos no 1º turno: "
            "eles se distribuem no 2º turno como os eleitores de outros candidatos se "
            "distribuíram em 2018, entre Haddad e Jair Bolsonaro. Os demais grupos seguem a "
            "transferência de 2022."
        ),
    ),
    "outros_divididos": Cenario(
        titulo="Sensibilidade: outros divididos ao meio",
        suposicoes=(
            "Igual ao cenário base, exceto pelos eleitores de outros candidatos no 1º turno: "
            "metade vai para Lula e metade para Flávio Bolsonaro."
        ),
    ),
}

NOMES_UF = {
    "AC": "Acre", "AL": "Alagoas", "AM": "Amazonas", "AP": "Amapá", "BA": "Bahia",
    "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás",
    "MA": "Maranhão", "MG": "Minas Gerais", "MS": "Mato Grosso do Sul", "MT": "Mato Grosso",
    "PA": "Pará", "PB": "Paraíba", "PE": "Pernambuco", "PI": "Piauí", "PR": "Paraná",
    "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RO": "Rondônia", "RR": "Roraima",
    "RS": "Rio Grande do Sul", "SC": "Santa Catarina", "SE": "Sergipe", "SP": "São Paulo",
    "TO": "Tocantins", UF_EXTERIOR: "Exterior",
}  # fmt: skip


@dataclass(frozen=True)
class Gold:
    projecao: pl.DataFrame
    lados: pl.DataFrame
    taxa_match: pl.DataFrame
    municipio_ibge: pl.DataFrame


def carregar(dir_gold: Path = DIR_GOLD) -> Gold:
    return Gold(
        projecao=pl.read_parquet(dir_gold / "projecao_2026.parquet"),
        lados=pl.read_parquet(dir_gold / "lados.parquet"),
        taxa_match=pl.read_parquet(dir_gold / "taxa_match.parquet"),
        municipio_ibge=pl.read_parquet(dir_gold / "municipio_ibge.parquet"),
    )


def nome_exibicao(nm_votavel: str) -> str:
    return NOMES_EXIBICAO.get(nm_votavel, nm_votavel.title())


def finalistas(lados: pl.DataFrame, ano: int = ANO_ALVO) -> dict[str, str]:
    """O rótulo real de cada lado naquela eleição, como "Lula (PT)"."""
    linhas = lados.filter(pl.col("ano") == ano).iter_rows(named=True)
    return {r["lado"]: f"{nome_exibicao(r['nm_votavel'])} ({r['sg_partido']})" for r in linhas}


def cenarios(projecao: pl.DataFrame) -> list[str]:
    """Os cenários presentes na gold, com o base primeiro."""
    presentes = set(projecao["cenario"].unique())
    return sorted(presentes, key=lambda c: (c != "base", c))


def faixa_brasil(projecao: pl.DataFrame) -> dict[str, tuple[float, float]]:
    """A menor e a maior fração dos votos válidos de cada lado entre os cenários."""
    brasil = projecao.filter(pl.col("nivel") == "brasil")
    return {
        lado: (brasil[f"pct_{lado}_validos"].min(), brasil[f"pct_{lado}_validos"].max())
        for lado in LADOS
    }


def tabela_uf(projecao: pl.DataFrame, cenario: str) -> pl.DataFrame:
    """Uma linha por UF (o exterior por último), com a fração dos válidos de cada lado."""
    return (
        projecao.filter((pl.col("nivel") == "uf") & (pl.col("cenario") == cenario))
        .select(
            "uf",
            pl.col("uf").replace_strict(NOMES_UF, default=pl.col("uf")).alias("nome"),
            "aptos",
            "pct_lula_validos",
            "pct_flavio_validos",
            (pl.col("pct_lula_validos") - pl.col("pct_flavio_validos")).alias("margem_lula"),
        )
        .sort(pl.col("uf") == UF_EXTERIOR, "uf")
    )


def tabela_municipio(
    projecao: pl.DataFrame, municipio_ibge: pl.DataFrame, cenario: str, uf: str | None = None
) -> pl.DataFrame:
    """Uma linha por município do Brasil (de uma UF, se dada), com o código IBGE da malha.

    O exterior fica de fora: ele não tem município IBGE e não vai para o mapa.
    """
    municipios = projecao.filter(
        (pl.col("nivel") == "municipio")
        & (pl.col("cenario") == cenario)
        & (pl.col("uf") != UF_EXTERIOR)
    )
    if uf is not None:
        municipios = municipios.filter(pl.col("uf") == uf)
    return (
        municipios.join(
            municipio_ibge.select("uf", "cd_municipio_tse", "cd_municipio_ibge"),
            left_on=["uf", "cd_municipio"],
            right_on=["uf", "cd_municipio_tse"],
            how="left",
            validate="1:1",
        )
        .select(
            "uf",
            pl.col("cd_municipio_ibge").cast(pl.String).alias("cd_ibge"),
            (
                pl.col("nm_municipio").map_elements(nome_municipio, return_dtype=pl.String)
                + " ("
                + pl.col("uf")
                + ")"
            ).alias("nome"),
            "aptos",
            "pct_lula_validos",
            "pct_flavio_validos",
            (pl.col("pct_lula_validos") - pl.col("pct_flavio_validos")).alias("margem_lula"),
        )
        .sort("uf", "nome")
    )


MINUSCULAS = {"de", "da", "do", "das", "dos", "e"}


def nome_municipio(nm: str) -> str:
    """O nome do BU, em maiúsculas, na grafia usual: "OLHO D'ÁGUA DO BORGES" → "Olho d'Água
    do Borges"."""
    palavras = nm.title().split()
    for i, p in enumerate(palavras[1:], start=1):
        if p.lower() in MINUSCULAS:
            palavras[i] = p.lower()
        elif p.startswith("D'"):
            palavras[i] = "d" + p[1:]
    return " ".join(palavras)


def taxa_match_ligacao(taxa_match: pl.DataFrame, ano_ref: int) -> dict[str, float]:
    """A fração dos aptos de 2026 ligada em cada passo à eleição de referência."""
    linhas = taxa_match.filter(pl.col("ligacao") == f"ligacao_{ANO_ALVO}_ref_{ano_ref}")
    return dict(zip(linhas["passo"], linhas["fracao_aptos"], strict=True))


def pct(fracao: float, casas: int = 1) -> str:
    """Formata uma fração como porcentagem no padrão brasileiro: 0.4752 → "47,5%"."""
    return f"{fracao * 100:.{casas}f}".replace(".", ",") + "%"


def inteiro(n: float) -> str:
    """Formata um número com separador de milhar brasileiro: 1234567 → "1.234.567"."""
    return f"{round(n):,}".replace(",", ".")
