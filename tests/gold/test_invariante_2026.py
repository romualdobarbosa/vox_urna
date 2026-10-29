import subprocess
from pathlib import Path

import duckdb
import numpy as np
import pytest

# Commit em que a projeção de 2026 foi publicada antes do backtest. O backtest refatorou a
# ligação, e nada de 2026 pode mudar por causa disso (vox-urna.md, "Avaliação do método").
# O painel de 2026, que só existe depois do 2º turno, fica fora da comparação.
COMMIT = "33d66e0"
RAIZ = Path(__file__).parents[2]
# (tabela, chave, filtro). A projeção e a ligação têm as somas em ordem paralela no DuckDB,
# e os β publicados vêm de um build com outra ordem de seções no solver: as diferenças
# ficam abaixo de 1e-6 (2e-8 no β), por isso a comparação usa tolerância.
TABELAS = [
    ("projecao_2026", "cenario, nivel, uf, cd_municipio, zona", "true"),
    ("ligacao_2026", "ano_ref, uf, cd_municipio, zona, k", "true"),
    (
        "taxa_match",
        "ligacao, passo",
        "ligacao not like 'ligacao_2022%' and ligacao <> 'painel_secao_2026'",
    ),
]


def ler(caminho: Path, chave: str, filtro: str) -> dict[str, list]:
    rel = duckdb.sql(f"select * from '{caminho}' where {filtro} order by {chave}")
    return {c: list(v) for c, v in zip(rel.columns, zip(*rel.fetchall(), strict=True), strict=True)}


@pytest.mark.parametrize(("tabela", "chave", "filtro"), TABELAS)
def test_tabelas_de_2026_iguais_as_do_commit_publicado(tmp_path, tabela, chave, filtro):
    atual = RAIZ / "data" / "gold" / f"{tabela}.parquet"
    try:
        conteudo = subprocess.run(
            ["git", "show", f"{COMMIT}:data/gold/{tabela}.parquet"],
            cwd=RAIZ,
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip(f"sem o git ou sem o commit {COMMIT}")
    publicado = tmp_path / f"{tabela}.parquet"
    publicado.write_bytes(conteudo)

    antes, depois = ler(publicado, chave, filtro), ler(atual, chave, filtro)
    assert antes.keys() == depois.keys()
    for coluna, valores in antes.items():
        if isinstance(valores[0], float):
            np.testing.assert_allclose(depois[coluna], valores, rtol=1e-7, atol=1e-6)
        else:
            assert depois[coluna] == valores, coluna
