import csv
import os
import threading
from dataclasses import asdict, dataclass, fields, replace
from enum import StrEnum
from pathlib import Path


class Status(StrEnum):
    SUCCESS = "success"
    CORRUPTED = "corrupted"
    FAILED = "failed"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class Entrada:
    url: str
    ano: int
    turno: int
    uf: str
    caminho: str
    sha512: str
    bytes: int
    downloaded_at: str
    status: Status

    @property
    def chave(self) -> tuple[int, int, str]:
        return (self.ano, self.turno, self.uf)


_CAMPOS = [campo.name for campo in fields(Entrada)]


class Manifest:
    """Registro de cada download do bronze, por URL, regravado de forma atômica."""

    def __init__(self, caminho: Path):
        self.caminho = caminho
        self._lock = threading.Lock()
        self._entradas: dict[str, Entrada] = {}
        if caminho.exists():
            with caminho.open(newline="", encoding="utf-8") as arquivo:
                for linha in csv.DictReader(arquivo):
                    entrada = Entrada(
                        **{
                            **linha,
                            "ano": int(linha["ano"]),
                            "turno": int(linha["turno"]),
                            "bytes": int(linha["bytes"]),
                            "status": Status(linha["status"]),
                        }
                    )
                    self._entradas[entrada.url] = entrada

    def get(self, url: str) -> Entrada | None:
        return self._entradas.get(url)

    @property
    def entradas(self) -> list[Entrada]:
        return list(self._entradas.values())

    def registrar(self, entrada: Entrada) -> None:
        with self._lock:
            self._entradas[entrada.url] = entrada
            if entrada.status is Status.SUCCESS:
                # O TSE republica com outro timestamp no nome: a versão anterior deixa de valer.
                for url, outra in self._entradas.items():
                    if (
                        url != entrada.url
                        and outra.chave == entrada.chave
                        and outra.status is Status.SUCCESS
                    ):
                        self._entradas[url] = replace(outra, status=Status.SUPERSEDED)
            self._salvar()

    def _salvar(self) -> None:
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        temporario = self.caminho.with_suffix(".csv.tmp")
        with temporario.open("w", newline="", encoding="utf-8") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=_CAMPOS)
            escritor.writeheader()
            for entrada in sorted(self._entradas.values(), key=lambda e: (e.chave, e.url)):
                escritor.writerow(asdict(entrada))
        os.replace(temporario, self.caminho)
