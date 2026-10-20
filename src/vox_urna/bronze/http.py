import logging
import time
from collections.abc import Callable

import requests

log = logging.getLogger(__name__)

_ERROS_TRANSITORIOS = (
    requests.ConnectionError,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


def _transitorio(erro: Exception) -> bool:
    if isinstance(erro, requests.HTTPError) and erro.response is not None:
        status = erro.response.status_code
        return status == 429 or status >= 500
    return isinstance(erro, _ERROS_TRANSITORIOS)


def com_retry[T](operacao: Callable[[], T], *, tentativas: int = 5, espera_base: float = 1.0) -> T:
    """Repete `operacao` em erros transitórios, com backoff exponencial (1s, 2s, 4s…)."""
    for tentativa in range(1, tentativas + 1):
        try:
            return operacao()
        except requests.RequestException as erro:
            if not _transitorio(erro) or tentativa == tentativas:
                raise
            espera = espera_base * 2 ** (tentativa - 1)
            log.warning(
                "tentativa %d/%d falhou (%s); nova em %.0fs", tentativa, tentativas, erro, espera
            )
            time.sleep(espera)
    raise AssertionError("inalcançável")
