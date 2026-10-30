"""Gera as imagens estáticas dos mapas por município, para o README e os posts.

Com a mesma figura do dashboard (`mapa.figura`), saem a projeção feita antes do 2º turno,
no cenário base e com a faixa entre os cenários no título, o resultado oficial do 2º turno,
só no território brasileiro, e as duas lado a lado pro post. Rode com
`make figuras`.
"""

from pathlib import Path

import plotly.graph_objects as go
import polars as pl
from plotly.subplots import make_subplots

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


def lado_a_lado(
    mapas: list[tuple[pl.DataFrame, str]], nomes: dict[str, str], titulo: str, saida: Path
) -> None:
    """Os mapas lado a lado, cada um com o seu subtítulo e na mesma escala de cor."""
    geo = mapa.malha("municipio")
    camadas = [
        mapa.figura(t, geo, chave="cd_ibge", rotulo="nome", nomes=nomes).data[0] for t, _ in mapas
    ]
    limite = max(max(abs(v) for v in c.z) for c in camadas)
    fig = make_subplots(
        rows=1, cols=len(mapas), specs=[[{"type": "choropleth"}] * len(mapas)],
        horizontal_spacing=0.01,
    )  # fmt: skip
    for i, camada in enumerate(camadas, start=1):
        camada.update(zmin=-limite, zmax=limite, marker_line_width=0, showscale=i == 1)
        fig.add_trace(camada, row=1, col=i)
    fig.update_traces(
        colorbar={
            "orientation": "h",
            "x": 0.5,
            "xanchor": "center",
            "y": 0.11,
            "yanchor": "top",
            "len": 0.5,
            "thickness": 14,
            "tickfont": {"size": 15},
            "title": {
                "text": "diferença em pontos (Lula − Flávio)",
                "side": "top",
                "font": {"size": 15},
            },
        },
        selector={"showscale": True},
    )
    fig.update_geos(
        visible=False, projection_type="mercator", bgcolor="white", fitbounds="locations"
    )
    for i, (_, subtitulo) in enumerate(mapas):
        fig.add_annotation(
            text=subtitulo,
            x=(i + 0.5) / len(mapas),
            y=0.84,
            xanchor="center",
            yanchor="bottom",
            xref="paper",
            yref="paper",
            showarrow=False,
            align="center",
            font={"size": 24},
        )
    fig.update_layout(
        width=1200,
        height=960,
        paper_bgcolor="white",
        font={"family": "Inter, Arial", "color": "#1a1a1a"},
        margin={"l": 20, "r": 20, "t": 130, "b": 30},
        title={"x": 0.03, "y": 0.965, "xanchor": "left", "font": {"size": 34}, "text": titulo},
    )
    for i in range(1, len(mapas) + 1):
        dominio = fig.layout[f"geo{i if i > 1 else ''}"].domain
        dominio.y = [0.14, 0.83]
    saida.parent.mkdir(parents=True, exist_ok=True)
    fig.write_image(saida, scale=2)
    print(saida)


def main() -> None:
    g = dados.carregar()
    nomes = dados.finalistas(g.lados)
    faixa = dados.faixa_brasil(g.projecao)
    lula, flavio = faixa["lula"], faixa["flavio"]
    projecao = dados.tabela_municipio(g.projecao, g.municipio_ibge, "base", None)
    resultado = dados.mapa_2o_turno(g.votos_municipio)
    desenhar(
        projecao,
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
        resultado,
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

    lula_2t = dados.pct(br["lula_2t"] / validos)
    flavio_2t = dados.pct(br["flavio_2t"] / validos)
    lado_a_lado(
        [
            (
                projecao,
                "<b>A projeção, antes do 2º turno</b><br>"
                + span(
                    f"Flávio {dados.pct(flavio[0])} a {dados.pct(flavio[1])}",
                    mapa.COR["flavio"],
                    21,
                )
                + span("  ·  ", "#777", 21)
                + span(f"Lula {dados.pct(lula[0])} a {dados.pct(lula[1])}", mapa.COR["lula"], 21),
            ),
            (
                resultado,
                "<b>O resultado oficial</b><br>"
                + span(f"Lula {lula_2t}", mapa.COR["lula"], 21)
                + span("  ·  ", "#777", 21)
                + span(f"Flávio {flavio_2t}", mapa.COR["flavio"], 21),
            ),
        ],
        nomes,
        "2º turno de 2026, por município<br>"
        + span(
            "Projeção metodológica, não pesquisa. Dados oficiais do TSE. voxurna.streamlit.app",
            "#666",
            17,
        ),
        DIR_IMG / "post_projecao_resultado.png",
    )


if __name__ == "__main__":
    main()
