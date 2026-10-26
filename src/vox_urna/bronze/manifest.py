import csv
import os
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass, fields, replace
from enum import StrEnum
from pathlib import Path


class Status(StrEnum):
    SUCCESS = "success"
    CORRUPTED = "corrupted"
    FAILED = "failed"
    SUPERSEDED = "superseded"
    # Só nos resultados divulgados: o TSE ainda não fechou a totalização daquele arquivo.
    INCOMPLETE = "incomplete"


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


class Manifest:
    """Registro de cada download do bronze, por URL, regravado de forma atômica.

    `tipo` é a dataclass de cada linha: `Entrada` para os BUs do CKAN e `EntradaResultado`
    para os JSONs de resultados. Ela precisa ter `url`, `status` e a propriedade `chave`.
    """

    def __init__(self, caminho: Path, tipo: type = Entrada):
        self.caminho = caminho
        self._lock = threading.Lock()
        self._campos = [campo.name for campo in fields(tipo)]
        conversores = {campo.name: campo.type for campo in fields(tipo) if campo.type is not str}
        self._entradas: dict[str, Entrada] = {}
        if caminho.exists():
            with caminho.open(newline="", encoding="utf-8") as arquivo:
                for linha in csv.DictReader(arquivo):
                    for nome, conversor in conversores.items():
                        linha[nome] = conversor(linha[nome])
                    entrada = tipo(**linha)
                    self._entradas[entrada.url] = entrada
        self._urls_por_chave: dict[tuple, set[str]] = defaultdict(set)
        for entrada in self._entradas.values():
            self._urls_por_chave[entrada.chave].add(entrada.url)

    def get(self, url: str) -> Entrada | None:
        return self._entradas.get(url)

    @property
    def entradas(self) -> list[Entrada]:
        return list(self._entradas.values())

    def registrar(self, entrada: Entrada, *, salvar: bool = True) -> None:
        """Registra `entrada`; com `salvar=False`, só grava no próximo `salvar()`."""
        with self._lock:
            self._entradas[entrada.url] = entrada
            urls = self._urls_por_chave[entrada.chave]
            urls.add(entrada.url)
            if entrada.status is Status.SUCCESS:
                # O TSE republica com outro timestamp no nome: a versão anterior deixa de valer.
                for url in urls - {entrada.url}:
                    outra = self._entradas[url]
                    if outra.status is Status.SUCCESS:
                        self._entradas[url] = replace(outra, status=Status.SUPERSEDED)
            if salvar:
                self._salvar()

    def salvar(self) -> None:
        with self._lock:
            self._salvar()

    def _salvar(self) -> None:
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        temporario = self.caminho.with_suffix(".csv.tmp")
        with temporario.open("w", newline="", encoding="utf-8") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=self._campos)
            escritor.writeheader()
            for entrada in sorted(self._entradas.values(), key=lambda e: (e.chave, e.url)):
                escritor.writerow(asdict(entrada))
        os.replace(temporario, self.caminho)
