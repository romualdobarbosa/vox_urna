"""Gera as imagens estáticas dos mapas por município, para o README e os posts.

São duas, com a mesma figura do dashboard (`mapa.figura`). A projeção feita antes do 2º
turno, no cenário base e com a faixa entre os cenários no título, e o resultado oficial do
2º turno, só no território brasileiro. Rode com `make figuras`.
"""

from pathlib import Path

import plotly.graph_objects as go
import polars as pl

import dados
import mapa

DIR_IMG = Path(__file__).resolve().parent.parent / "docs" / "img"


def span(texto: str, cor: str, tamanho: int = 26) -> str:
    return f"<span style='font-size:{tamanho}px;color:{cor}'>{texto}</span>"


def desenhar(tabela: pl.DataFrame, nomes: dict[str, str], titulo: str, saida: Path) -> None:
    fig: go.Figure = mapa.figura(
        tabela, mapa.malha("municipio"), chave="cd_ibge", rotulo="nome", nomes=nomes
    )
    fig.update_traces(
        marker_line_width=0,
        colorbar={
            "orientation": "h",
            "x": 0.5,
            "xanchor": "center",
            "y": 0.06,
            "yanchor": "top",
            "len": 0.55,
            "thickness": 14,
            "tickfont": {"size": 15},
            "title": {
                "text": "diferença em p.p. (Lula − Flávio)",
                "side": "top",
                "font": {"size": 15},
            },
        },
    )
    fig.update_geos(bgcolor="white", fitbounds="locations", domain={"x": [0, 1], "y": [0.1, 1]})
    fig.update_layout(
        width=1200,
        height=1250,
        paper_bgcolor="white",
        font={"family": "Inter, Arial", "color": "#1a1a1a"},
        margin={"l": 20, "r": 20, "t": 190, "b": 20},
        title={
            "x": 0.03,
            "y": 0.955,
            "yanchor": "top",
            "xanchor": "left",
            "font": {"size": 34},
            "text": titulo,
        },
    )
    saida.parent.mkdir(parents=True, exist_ok=True)
    fig.write_image(saida)
    print(saida)


def main() -> None:
    g = dados.carregar()
    nomes = dados.finalistas(g.lados)
    faixa = dados.faixa_brasil(g.projecao)
    lula, flavio = faixa["lula"], faixa["flavio"]
    desenhar(
        dados.tabela_municipio(g.projecao, g.municipio_ibge, "base", None),
        nomes,
        "Projeção do 2º turno de 2026, por município<br>"
        + span(
            f"Flávio Bolsonaro {dados.pct(flavio[0])} a {dados.pct(flavio[1])}",
            mapa.COR["flavio"],
        )
        + span("  ·  ", "#777")
        + span(f"Lula {dados.pct(lula[0])} a {dados.pct(lula[1])}", mapa.COR["lula"])
        + "<br>"
        + span(
            "Projeção metodológica, não pesquisa. Dados oficiais do TSE. "
            "Mapa no cenário base. voxurna.streamlit.app",
            "#666",
            17,
        ),
        DIR_IMG / "mapa_municipios.png",
    )

    br = dados.linha_resumo(g.resumo, "brasil", "brasil")
    validos = br["lula_2t"] + br["flavio_2t"]
    desenhar(
        dados.mapa_2o_turno(g.votos_municipio),
        nomes,
        "Resultado do 2º turno de 2026, por município<br>"
        + span(f"Lula {dados.pct(br['lula_2t'] / validos)}", mapa.COR["lula"])
        + span("  ·  ", "#777")
        + span(f"Flávio Bolsonaro {dados.pct(br['flavio_2t'] / validos)}", mapa.COR["flavio"])
        + "<br>"
        + span(
            "Votos válidos no território brasileiro. Dados oficiais do TSE. voxurna.streamlit.app",
            "#666",
            17,
        ),
        DIR_IMG / "mapa_resultado_2026.png",
    )


if __name__ == "__main__":
    main()
