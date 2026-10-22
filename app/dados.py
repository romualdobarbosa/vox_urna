"""Leitura das tabelas gold e os recortes que o dashboard mostra.

Fica separado do app Streamlit para ser testável sem subir a interface.
"""

from dataclasses import dataclass
from pathlib import Path

import polars as pl

# O streamlit_app recarrega o módulo quando o arquivo muda (veja recarregar()).
VERSAO_ARQUIVO = Path(__file__).stat().st_mtime_ns
DIR_GOLD = Path(__file__).resolve().parent.parent / "data" / "gold"
ANO_ALVO = 2026
LADOS = ("lula", "flavio")
UF_EXTERIOR = "ZZ"
UF_BRASIL = "BR"  # a chave das linhas do Brasil na matriz de transferência

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
    # O β de cada UF e do Brasil (com uf = UF_BRASIL), de todas as eleições de referência.
    transferencia_uf: pl.DataFrame
    # O RMSE do ajuste de cada zona de 2022, a eleição do cenário base.
    rmse_zona: pl.DataFrame
    backtest: pl.DataFrame
    backtest_acerto: pl.DataFrame
    estabilidade: pl.DataFrame
    referencia_externa: pl.DataFrame
    viradas: pl.DataFrame
    viradas_resumo: pl.DataFrame


def carregar(dir_gold: Path = DIR_GOLD) -> Gold:
    transferencia = pl.concat(
        pl.read_parquet(caminho)
        .filter(pl.col("nivel").is_in(["uf", "brasil"]))
        .with_columns(
            pl.when(pl.col("nivel") == "brasil")
            .then(pl.lit(UF_BRASIL))
            .otherwise(pl.col("uf"))
            .alias("uf"),
            ano=pl.lit(int(caminho.stem.rsplit("_", 1)[1])),
        )
        for caminho in sorted(dir_gold.glob("transferencia_zona_*.parquet"))
    )
    return Gold(
        projecao=pl.read_parquet(dir_gold / "projecao_2026.parquet"),
        lados=pl.read_parquet(dir_gold / "lados.parquet"),
        taxa_match=pl.read_parquet(dir_gold / "taxa_match.parquet"),
        municipio_ibge=pl.read_parquet(dir_gold / "municipio_ibge.parquet"),
        transferencia_uf=transferencia,
        rmse_zona=pl.read_parquet(dir_gold / "transferencia_zona_2022.parquet")
        .filter(pl.col("nivel") == "zona")
        .select("uf", "zona", "rmse_pp")
        .unique(["uf", "zona"]),
        backtest=pl.read_parquet(dir_gold / "backtest_2022.parquet"),
        backtest_acerto=pl.read_parquet(dir_gold / "backtest_2022_acerto.parquet"),
        estabilidade=pl.read_parquet(dir_gold / "estabilidade_transferencia.parquet"),
        referencia_externa=pl.read_parquet(dir_gold / "referencia_externa.parquet"),
        viradas=pl.read_parquet(dir_gold / "viradas_municipio.parquet"),
        viradas_resumo=pl.read_parquet(dir_gold / "viradas_municipio_resumo.parquet"),
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


def tabela_exterior(projecao: pl.DataFrame, cenario: str) -> pl.DataFrame:
    """Uma linha por cidade do exterior (a do consulado), da maior para a menor em aptos."""
    return (
        projecao.filter(
            (pl.col("nivel") == "municipio")
            & (pl.col("cenario") == cenario)
            & (pl.col("uf") == UF_EXTERIOR)
        )
        .select(
            pl.col("nm_municipio")
            .map_elements(nome_municipio, return_dtype=pl.String)
            .alias("nome"),
            "aptos",
            "pct_lula_validos",
            "pct_flavio_validos",
            (pl.col("pct_lula_validos") - pl.col("pct_flavio_validos")).alias("margem_lula"),
        )
        .sort("aptos", "nome", descending=[True, False])
    )


def tabela_zona(
    projecao: pl.DataFrame,
    cenario: str,
    uf: str | None = None,
    rmse_zona: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Uma linha por zona (o exterior é uma zona só), da mais favorável a Lula à mais
    favorável a Flávio.

    Com `rmse_zona`, ganha a coluna `rmse_pp` do ajuste da zona de mesmo número em 2022,
    nula na zona de 2026 que não existia em 2022.
    """
    zonas = projecao.filter((pl.col("nivel") == "zona") & (pl.col("cenario") == cenario))
    if uf is not None:
        zonas = zonas.filter(pl.col("uf") == uf)
    if rmse_zona is not None:
        zonas = zonas.join(rmse_zona, on=["uf", "zona"], how="left", validate="1:1")
    extras = ["rmse_pp"] if rmse_zona is not None else []
    return zonas.select(
        "uf",
        pl.when(pl.col("uf") == UF_EXTERIOR)
        .then(pl.lit("Exterior"))
        .otherwise(pl.format("Zona {} ({})", "zona", "uf"))
        .alias("nome"),
        "aptos",
        "pct_lula_validos",
        "pct_flavio_validos",
        (pl.col("pct_lula_validos") - pl.col("pct_flavio_validos")).alias("margem_lula"),
        *extras,
    ).sort("margem_lula", "nome", descending=[True, False])


CATEGORIAS = {"outros": "Outros candidatos", "branco": "Branco", "nulo": "Nulo"}
CATEGORIAS_FIM = {"abstencao": "Abstenção"}


def matriz_transferencia(
    transferencia_uf: pl.DataFrame, lados: pl.DataFrame, ano: int, uf: str
) -> pl.DataFrame:
    """Para onde foi cada categoria do 1º turno numa UF, em fração, com os nomes reais.

    Uma linha por categoria do 1º turno (`origem`) e uma coluna por categoria do 2º turno;
    cada linha soma 1.
    """
    nomes = {**finalistas(lados, ano), **CATEGORIAS, **CATEGORIAS_FIM}
    destinos = [*LADOS, "branco", "nulo", "abstencao"]
    ordem = [*LADOS, *CATEGORIAS, *CATEGORIAS_FIM]
    return (
        transferencia_uf.filter((pl.col("ano") == ano) & (pl.col("uf") == uf))
        .sort(pl.col("k").replace_strict({k: i for i, k in enumerate(ordem)}))
        .select(
            pl.col("k").replace_strict(nomes).alias("origem"),
            *(pl.col(j).alias(nomes[j]) for j in destinos),
        )
    )


def divisao_outros(transferencia_uf: pl.DataFrame, ano: int, uf: str = UF_BRASIL) -> float:
    """Dos eleitores de outros candidatos que escolheram um lado no 2º turno, a fração que
    foi para o lado Lula."""
    outros = transferencia_uf.filter(
        (pl.col("ano") == ano) & (pl.col("uf") == uf) & (pl.col("k") == "outros")
    ).row(0, named=True)
    return outros["lula"] / (outros["lula"] + outros["flavio"])


def brancos_nulos_abstencao(linha: dict) -> tuple[float, float]:
    """Brancos e nulos em fração do comparecimento, e a abstenção em fração dos aptos."""
    comparecimento = sum(linha[c] for c in (*LADOS, "branco", "nulo"))
    return (linha["branco"] + linha["nulo"]) / comparecimento, linha["abstencao"] / linha["aptos"]


@dataclass(frozen=True)
class ResumoBacktest:
    erro_pp: float  # com sinal: positivo quando o método deu mais ao lado Lula que o real
    projetado: float
    real: float
    ufs: int  # as UFs do Brasil, sem o exterior
    ufs_erradas: list[str]
    acertou_exterior: bool
    pct_municipios: float


def resumo_backtest(backtest: pl.DataFrame, acerto: pl.DataFrame) -> ResumoBacktest:
    """O erro do backtest no Brasil e o acerto do vencedor, para a linha sob a manchete."""
    brasil = backtest.filter(pl.col("nivel") == "brasil").row(0, named=True)
    ufs = backtest.filter((pl.col("nivel") == "uf") & (pl.col("uf") != UF_EXTERIOR))
    exterior = backtest.filter((pl.col("nivel") == "uf") & (pl.col("uf") == UF_EXTERIOR))
    municipios = acerto.filter(pl.col("nivel") == "municipio").row(0, named=True)
    return ResumoBacktest(
        erro_pp=brasil["erro_pp"],
        projetado=brasil["pct_lula_validos_projetado"],
        real=brasil["pct_lula_validos_real"],
        ufs=ufs.height,
        ufs_erradas=[NOMES_UF[uf] for uf in ufs.filter(~pl.col("acertou"))["uf"].sort()],
        acertou_exterior=bool(exterior["acertou"].all()),
        pct_municipios=municipios["pct_acerto"],
    )


def tabela_backtest_uf(backtest: pl.DataFrame) -> pl.DataFrame:
    """Uma linha por UF (o exterior por último): o lado Lula projetado e o real em 2022."""
    return (
        backtest.filter(pl.col("nivel") == "uf")
        .sort(pl.col("uf") == UF_EXTERIOR, "uf")
        .select(
            pl.col("uf").replace_strict(NOMES_UF, default=pl.col("uf")).alias("nome"),
            pl.col("pct_lula_validos_projetado").alias("projetado"),
            pl.col("pct_lula_validos_real").alias("real"),
            "erro_pp",
            "acertou",
        )
    )


ROTULOS_LINHA = {
    "lula": "Lado Lula",
    "flavio": "Lado Flávio",
    **CATEGORIAS,
    **CATEGORIAS_FIM,
}


def tabela_estabilidade(estabilidade: pl.DataFrame) -> pl.DataFrame:
    """A TVD de cada categoria do 1º turno entre 2018 e 2022 no Brasil, da maior à menor."""
    return (
        estabilidade.filter(pl.col("nivel") == "brasil")
        .select(pl.col("k").replace_strict(ROTULOS_LINHA).alias("categoria"), "tvd")
        .sort("tvd", descending=True)
    )


def tabela_referencia(referencia: pl.DataFrame) -> pl.DataFrame:
    """As pesquisas primeiro e o método por último, cada uma com o rótulo que a identifica."""
    return referencia.sort(pl.col("fonte") != "pesquisa", "instituto").with_columns(
        pl.when(pl.col("fonte") == "pesquisa")
        .then(pl.col("instituto"))
        .otherwise(pl.lit("Este método"))
        .alias("rotulo")
    )


SENTIDOS = {"lula_para_flavio": "Lula → Flávio", "flavio_para_lula": "Flávio → Lula"}
RESSALVA_VIRADAS = (
    "Compara o 2º turno de 2022 com o 1º turno de 2026, só entre os dois lados. Em 2026, quem "
    "votou em outros candidatos ainda não escolheu um lado; isto descreve o 1º turno, não é "
    "uma projeção."
)


def resumo_viradas(resumo: pl.DataFrame, uf: str | None = None) -> dict[str, dict]:
    """A linha do resumo de cada sentido, no Brasil (sem o exterior) ou numa UF."""
    recorte = pl.col("nivel") == "brasil" if uf is None else pl.col("uf") == uf
    return {r["sentido"]: r for r in resumo.filter(recorte).iter_rows(named=True)}


def tabela_viradas(viradas: pl.DataFrame, uf: str | None = None) -> pl.DataFrame:
    """Só os municípios que viraram, no Brasil (sem o exterior, como no resumo) ou numa UF,
    da maior variação em módulo à menor. No exterior, o nome é o da cidade, sem a UF."""
    recorte = pl.col("uf") != UF_EXTERIOR if uf is None else pl.col("uf") == uf
    nome = pl.col("nm_municipio").map_elements(nome_municipio, return_dtype=pl.String)
    return (
        viradas.filter(recorte & pl.col("virada").is_not_null())
        .select(
            pl.when(pl.col("uf") == UF_EXTERIOR)
            .then(nome)
            .otherwise(nome + " (" + pl.col("uf") + ")")
            .alias("nome"),
            pl.col("virada").replace_strict(SENTIDOS).alias("sentido"),
            "aptos_2026",
            "d22",
            "d26",
            "variacao_pp",
        )
        .sort(pl.col("variacao_pp").abs(), "nome", descending=[True, False])
    )


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
