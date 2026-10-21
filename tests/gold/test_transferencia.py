import numpy as np
import pytest

from vox_urna.gold.transferencia import J, K, estimar_beta, rmse_pp

BETA = np.array(
    [
        # lula  flavio branco nulo  abstencao
        [0.95, 0.01, 0.01, 0.01, 0.02],  # lula
        [0.02, 0.93, 0.01, 0.01, 0.03],  # flavio
        [0.45, 0.30, 0.08, 0.10, 0.07],  # outros
        [0.20, 0.20, 0.40, 0.10, 0.10],  # branco
        [0.15, 0.25, 0.10, 0.40, 0.10],  # nulo
        [0.03, 0.05, 0.00, 0.01, 0.91],  # abstencao
    ]
)


def secoes_sinteticas(beta, n, ruido, seed=0):
    """Seções com 1º turno variado e 2º turno gerado por `beta`, mais ruído amostral."""
    rng = np.random.default_rng(seed)
    x = rng.dirichlet([8, 7, 2, 0.5, 0.8, 4], size=n)
    y = x @ beta + rng.normal(0, ruido, size=(n, len(J)))
    return x, y, rng.integers(150, 450, size=n).astype(float)


def test_recupera_beta_conhecido_sem_ruido():
    x, y, peso = secoes_sinteticas(BETA, 200, ruido=0)

    np.testing.assert_allclose(estimar_beta(x, y, peso), BETA, atol=1e-4)


@pytest.mark.parametrize("seed", range(3))
def test_recupera_beta_conhecido_com_ruido(seed):
    x, y, peso = secoes_sinteticas(BETA, 400, ruido=0.005, seed=seed)
    beta = estimar_beta(x, y, peso)

    # As linhas dos lados e da abstenção são as mais pesadas no eleitorado e as mais firmes.
    for linha in ("lula", "flavio", "abstencao"):
        i = K.index(linha)
        np.testing.assert_allclose(beta[i], BETA[i], atol=0.02)
    np.testing.assert_allclose(beta[K.index("outros")], BETA[K.index("outros")], atol=0.1)


def test_beta_respeita_as_restricoes_mesmo_com_dados_ruins():
    rng = np.random.default_rng(1)
    x = rng.dirichlet(np.ones(len(K)), size=12)
    y = rng.dirichlet(np.ones(len(J)), size=12)
    beta = estimar_beta(x, y, np.ones(12))

    assert beta.shape == (len(K), len(J))
    assert (beta >= 0).all()
    np.testing.assert_allclose(beta.sum(axis=1), 1)


def test_rmse_e_a_raiz_da_media_dos_residuos_ponderada_pelos_aptos_em_pp():
    x, y, peso = secoes_sinteticas(BETA, 50, ruido=0)
    assert rmse_pp(x, y, peso, BETA) == pytest.approx(0, abs=1e-12)

    # Resíduo de 0,01 em todas as categorias de uma seção e zero na outra, com o dobro
    # dos aptos: a média dos quadrados é 0,01² / 3.
    desvio = np.zeros((2, len(J)))
    desvio[0] = 0.01
    x2 = x[:2]
    assert rmse_pp(x2, x2 @ BETA + desvio, np.array([100.0, 200.0]), BETA) == pytest.approx(
        100 * 0.01 / np.sqrt(3)
    )
