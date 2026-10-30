"""Mapas coropléticos da projeção, sobre as malhas do IBGE em app/geo/."""

import json
from functools import cache
from pathlib import Path

import plotly.graph_objects as go
import polars as pl

# O streamlit_app recarrega o módulo quando o arquivo muda (veja recarregar()).
VERSAO_ARQUIVO = Path(__file__).stat().st_mtime_ns
DIR_GEO = Path(__file__).resolve().parent / "geo"
COR = {"lula": "#C8102E", "flavio": "#1F4E9C"}
NEUTRO = "#F2F2F2"
CINZA = "#BDBDBD"
# As trajetórias pintadas por cima do 2º turno (dados.TRAJETORIAS_MAPA). Têm a luminosidade
# e a saturação do vermelho e do azul, com o matiz nos vãos entre eles (violeta entre os
# dois, verde-azulado e âmbar do outro lado do círculo), e passam no validador de
# daltonismo junto com eles: ΔE ≥ 12 entre qualquer par.
COR_TRAJETORIA = {"roxo": "#A17ADF", "especial": "#00908A", "vermelho": "#CB7F00"}
# Do amarelo claro ao ocre: quanto mais escuro, mais gente voltou a votar.
AMARELO = [[0, "#FFF6C7"], [0.5, "#F2C200"], [1, "#7A5200"]]
# Horizontal e embaixo, para não roubar largura do mapa no celular.
BARRA = {
    "orientation": "h",
    "thickness": 10,
    "len": 0.6,
    "x": 0.5,
    "xanchor": "center",
    "y": 0,
    "yanchor": "top",
}
# Boa Esperança do Norte (MT), instalado em 2025, ainda não está na malha v3 do IBGE: o
# território dele aparece dentro de Sorriso e de Nova Ubiratã.
SEM_POLIGONO = "5101837"


@cache
def malha(nivel: str) -> dict:
    """A malha do IBGE de um nível ('uf' ou 'municipio'), com propriedades {cd_ibge, uf}."""
    return json.loads((DIR_GEO / f"{nivel}.geojson").read_text())


def figura(
    tabela: pl.DataFrame,
    geojson: dict,
    *,
    chave: str,
    rotulo: str,
    nomes: dict[str, str],
) -> go.Figure:
    """Pinta cada feição pela diferença em pontos percentuais (Lula − Flávio).

    `tabela` precisa de `chave` (a propriedade da feição que a identifica), `rotulo`,
    `pct_lula_validos` e `pct_flavio_validos`. A escala é simétrica em torno de zero:
    vermelho onde Lula fica à frente e azul onde Flávio fica.
    """
    margem = (tabela["pct_lula_validos"] - tabela["pct_flavio_validos"]) * 100
    limite = max(float(margem.abs().max() or 0.0), 1.0)
    dica = [
        f"<b>{r}</b><br>{nomes['lula']}: {lula * 100:.1f}%<br>"
        f"{nomes['flavio']}: {flavio * 100:.1f}%".replace(".", ",")
        for r, lula, flavio in tabela.select(
            rotulo, "pct_lula_validos", "pct_flavio_validos"
        ).iter_rows()
    ]
    fig = go.Figure(
        go.Choropleth(
            geojson=geojson,
            featureidkey=f"properties.{chave}",
            locations=tabela[chave].to_list(),
            z=margem.to_list(),
            zmin=-limite,
            zmax=limite,
            colorscale=[[0, COR["flavio"]], [0.5, NEUTRO], [1, COR["lula"]]],
            marker_line_width=0.3,
            marker_line_color="white",
            text=dica,
            hovertemplate="%{text}<extra></extra>",
            colorbar={"title": {"text": "diferença em pontos", "side": "top"}, **BARRA},
        )
    )
    return _enquadrar(fig)


def _enquadrar(fig: go.Figure) -> go.Figure:
    fig.update_geos(
        fitbounds="locations", visible=False, projection_type="mercator", bgcolor="rgba(0,0,0,0)"
    )
    fig.update_layout(
        margin={"l": 0, "r": 0, "t": 0, "b": 0}, height=560, paper_bgcolor="rgba(0,0,0,0)"
    )
    return fig


def _camada(
    tabela: pl.DataFrame, geojson: dict, z: list, dica: list[str], **estilo
) -> go.Choropleth:
    return go.Choropleth(
        geojson=geojson,
        featureidkey="properties.cd_ibge",
        locations=tabela["cd_ibge"].to_list(),
        z=z,
        marker_line_width=0.3,
        marker_line_color="white",
        text=dica,
        hovertemplate="%{text}<extra></extra>",
        **estilo,
    )


def _uma_cor(cor: str) -> list:
    return [[0, cor], [1, cor]]


def figura_trajetoria(
    tabela: pl.DataFrame, geojson: dict, *, nomes: dict[str, str], trajetorias: dict[str, str]
) -> go.Figure:
    """O 2º turno real em gradação clara e, por cima, cada trajetória na sua cor sólida.

    `tabela` vem de `dados.mapa_2o_turno`.
    """
    fig = figura(tabela, geojson, chave="cd_ibge", rotulo="nome", nomes=nomes)
    fig.update_traces(marker_opacity=0.4)
    for cor, descricao in trajetorias.items():
        parte = tabela.filter(pl.col("cor_mapa") == cor)
        if parte.is_empty():
            continue
        dica = [f"<b>{n}</b><br>{descricao}" for n in parte["nome"]]
        fig.add_trace(
            _camada(
                parte,
                geojson,
                [1] * parte.height,
                dica,
                colorscale=_uma_cor(COR_TRAJETORIA[cor]),
                showscale=False,
            )
        )
    return fig


def figura_virada(tabela: pl.DataFrame, geojson: dict) -> go.Figure:
    """Cada município por quanto a diferença Lula − Flávio andou, a cada 100 eleitores:
    vermelho onde o Lula cresceu mais, azul onde o Flávio cresceu mais.

    `tabela` vem de `dados.mapa_virada`.
    """
    # O 98º percentil segura a escala, para os extremos não apagarem o resto do mapa.
    limite = max(float(tabela["virada_100"].abs().quantile(0.98) or 0.0), 1.0)

    def dica(t: pl.DataFrame) -> list[str]:
        return [
            f"<b>{n}</b><br>Lula {dl:+,.0f} · Flávio {df:+,.0f} votos<br>"
            f"Diferença: {v:+.1f} a cada 100 eleitores".replace(",", "@")
            .replace(".", ",")
            .replace("@", ".")
            for n, dl, df, v in t.select(
                "nome", "delta_lula", "delta_flavio", "virada_100"
            ).iter_rows()
        ]

    fig = go.Figure()
    fig.add_trace(
        _camada(
            tabela,
            geojson,
            tabela["virada_100"].to_list(),
            dica(tabela),
            zmin=-limite,
            zmax=limite,
            colorscale=[[0, COR["flavio"]], [0.5, NEUTRO], [1, COR["lula"]]],
            colorbar={"title": {"text": "votos a cada 100 eleitores", "side": "top"}, **BARRA},
        )
    )
    return _enquadrar(fig)


def figura_nao_voto(tabela: pl.DataFrame, geojson: dict) -> go.Figure:
    """Quanto abstenção, brancos e nulos caíram em cada município, em amarelo cada vez mais
    escuro; em cinza onde subiram. `tabela` vem de `dados.mapa_nao_voto`."""
    limite = max(float(tabela["queda_pp"].quantile(0.98) or 0.0), 1.0)

    def dica(t: pl.DataFrame) -> list[str]:
        return [
            f"<b>{n}</b><br>Abstenção, brancos e nulos: {-q:+.1f} p.p.<br>"
            f"Abstenção {-a:+.1f} · brancos e nulos {-b:+.1f}".replace(".", ",")
            for n, q, a, b in t.select(
                "nome", "queda_pp", "queda_abstencao_pp", "queda_branco_nulo_pp"
            ).iter_rows()
        ]

    caiu = tabela.filter(pl.col("queda_pp") > 0)
    subiu = tabela.filter(pl.col("queda_pp") <= 0)
    fig = go.Figure(
        [
            _camada(
                subiu,
                geojson,
                [0] * subiu.height,
                dica(subiu),
                colorscale=_uma_cor(CINZA),
                showscale=False,
            ),
            _camada(
                caiu,
                geojson,
                caiu["queda_pp"].to_list(),
                dica(caiu),
                zmin=0,
                zmax=limite,
                colorscale=AMARELO,
                colorbar={"title": {"text": "queda em p.p. dos aptos", "side": "top"}, **BARRA},
            ),
        ]
    )
    return _enquadrar(fig)
