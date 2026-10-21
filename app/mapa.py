"""Mapas coropléticos da projeção, sobre as malhas do IBGE em app/geo/."""

import json
from functools import cache
from pathlib import Path

import plotly.graph_objects as go
import polars as pl

DIR_GEO = Path(__file__).resolve().parent / "geo"
COR = {"lula": "#C8102E", "flavio": "#1F4E9C"}
NEUTRO = "#F2F2F2"
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
            # Horizontal e embaixo, para não roubar largura do mapa no celular.
            colorbar={
                "title": {"text": "p.p.", "side": "right"},
                "orientation": "h",
                "thickness": 10,
                "len": 0.6,
                "x": 0.5,
                "xanchor": "center",
                "y": 0,
                "yanchor": "top",
            },
        )
    )
    fig.update_geos(
        fitbounds="locations", visible=False, projection_type="mercator", bgcolor="rgba(0,0,0,0)"
    )
    fig.update_layout(
        margin={"l": 0, "r": 0, "t": 0, "b": 0}, height=560, paper_bgcolor="rgba(0,0,0,0)"
    )
    return fig
