"""Gera a imagem estática do mapa por município, para o README e o post de lançamento.

Usa a mesma figura do dashboard (`mapa.figura`), no cenário base, com a faixa entre os
cenários no título. Rode com `make figuras`.
"""

from pathlib import Path

import dados
import mapa

SAIDA = Path(__file__).resolve().parent.parent / "docs" / "img" / "mapa_municipios.png"


def span(texto: str, cor: str, tamanho: int = 26) -> str:
    return f"<span style='font-size:{tamanho}px;color:{cor}'>{texto}</span>"


def main() -> None:
    g = dados.carregar()
    faixa = dados.faixa_brasil(g.projecao)
    lula, flavio = faixa["lula"], faixa["flavio"]
    tabela = dados.tabela_municipio(g.projecao, g.municipio_ibge, "base", None)
    fig = mapa.figura(
        tabela,
        mapa.malha("municipio"),
        chave="cd_ibge",
        rotulo="nome",
        nomes=dados.finalistas(g.lados),
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
            "text": "Projeção do 2º turno de 2026, por município<br>"
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
        },
    )
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    fig.write_image(SAIDA)
    print(SAIDA)


if __name__ == "__main__":
    main()
