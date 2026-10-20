"""Baixa as malhas do IBGE que o mapa do dashboard usa e grava em app/geo/.

Fonte: API de malhas do IBGE, v3, GeoJSON, qualidade mínima.
- UF: https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR?formato=application/vnd.geo%2Bjson&qualidade=minima&intrarregiao=UF
- Município: mesma URL com intrarregiao=municipio
- Siglas das UFs: https://servicodados.ibge.gov.br/api/v1/localidades/estados
Baixadas em 20/10/2026. As malhas são insumo estático e ficam versionadas; rode de novo
(uv run python app/geo/baixar_malhas.py) só se o IBGE mudar a divisão territorial.

As propriedades de cada feição viram {cd_ibge, uf}: o código IBGE (2 dígitos na UF, 7 no
município) e a sigla da UF. Os anéis são regravados na orientação que o plotly (d3-geo)
espera, com o externo em sentido horário; na orientação do IBGE, o d3 pinta o globo inteiro
menos o polígono.
"""

import json
from pathlib import Path

import requests

DIR = Path(__file__).resolve().parent
MALHA = (
    "https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR"
    "?formato=application/vnd.geo%2Bjson&qualidade=minima&intrarregiao={nivel}"
)
ESTADOS = "https://servicodados.ibge.gov.br/api/v1/localidades/estados"


def _baixar(url: str) -> dict:
    resposta = requests.get(url, timeout=120)
    resposta.raise_for_status()
    return resposta.json()


def _area(anel: list[list[float]]) -> float:
    """Área com sinal (fórmula do laço): positiva no sentido anti-horário."""
    return sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(anel, anel[1:], strict=False))


def _orientar(geometria: dict) -> None:
    """Externo no sentido horário e buracos no anti-horário, como o d3-geo espera."""
    poligonos = (
        [geometria["coordinates"]] if geometria["type"] == "Polygon" else geometria["coordinates"]
    )
    for poligono in poligonos:
        for i, anel in enumerate(poligono):
            horario = _area(anel) < 0
            if horario != (i == 0):
                anel.reverse()


def main() -> None:
    sigla = {str(e["id"]): e["sigla"] for e in _baixar(ESTADOS)}
    for nivel, saida in (("UF", "uf.geojson"), ("municipio", "municipio.geojson")):
        malha = _baixar(MALHA.format(nivel=nivel))
        for feicao in malha["features"]:
            codigo = feicao["properties"]["codarea"]
            feicao["properties"] = {"cd_ibge": codigo, "uf": sigla[codigo[:2]]}
            _orientar(feicao["geometry"])
        (DIR / saida).write_text(json.dumps(malha, separators=(",", ":")))
        print(f"{saida}: {len(malha['features'])} feições")


if __name__ == "__main__":
    main()
