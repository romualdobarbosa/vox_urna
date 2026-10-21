import numpy as np
from scipy.optimize import minimize

# Categorias do 1º turno (k) e do 2º (j), em fração dos aptos (vox-urna.md, "Modelo").
K = ("lula", "flavio", "outros", "branco", "nulo", "abstencao")
J = ("lula", "flavio", "branco", "nulo", "abstencao")


class EstimacaoFalhou(RuntimeError):
    pass


def estimar_beta(x: np.ndarray, y: np.ndarray, peso: np.ndarray) -> np.ndarray:
    """β (K×J) de Goodman restrito: min Σ_i peso_i ‖y_i − x_i β‖², β ≥ 0, linhas somando 1.

    `x` (n×K) e `y` (n×J) são as frações dos aptos de cada seção no 1º e no 2º turno.
    Uma linha de β cuja categoria não aparece nas seções não é identificada e fica
    na divisão igual de onde o solver parte.
    """
    k, j = x.shape[1], y.shape[1]
    w = peso / peso.sum()
    a = x.T @ (w[:, None] * x)  # K×K
    c = x.T @ (w[:, None] * y)  # K×J

    def objetivo(b: np.ndarray) -> tuple[float, np.ndarray]:
        beta = b.reshape(k, j)
        ab = a @ beta
        return float(np.sum(beta * ab) - 2 * np.sum(beta * c)), (2 * (ab - c)).ravel()

    linhas = np.kron(np.eye(k), np.ones(j))
    resultado = minimize(
        objetivo,
        np.full(k * j, 1 / j),
        jac=True,
        method="SLSQP",
        bounds=[(0, 1)] * (k * j),
        constraints=[{"type": "eq", "fun": lambda b: linhas @ b - 1, "jac": lambda b: linhas}],
        options={"ftol": 1e-12, "maxiter": 500},
    )
    if not resultado.success:
        raise EstimacaoFalhou(resultado.message)
    beta = np.clip(resultado.x.reshape(k, j), 0, None)
    return beta / beta.sum(axis=1, keepdims=True)


def rmse_pp(x: np.ndarray, y: np.ndarray, peso: np.ndarray, beta: np.ndarray) -> float:
    """RMSE dos resíduos y − xβ, em pontos percentuais da fração dos aptos.

    A média dos quadrados corre sobre as seções, ponderadas pelos aptos, e sobre as J
    categorias do 2º turno.
    """
    residuo = y - x @ beta
    return float(100 * np.sqrt(np.sum(peso[:, None] * residuo**2) / (peso.sum() * y.shape[1])))
