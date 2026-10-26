"""Silver dos resultados divulgados: uma linha por Brasil, UF e município, e os votos de cada um.

Diferente do BU, aqui não há quarentena: são totais oficiais já consolidados pelo TSE, então
qualquer conta que não feche (dentro do arquivo ou entre município, UF e Brasil) derruba o build.
"""

import json
import logging
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from vox_urna.bronze.manifest import Manifest, Status
from vox_urna.bronze.resultados import EntradaResultado, municipios

log = logging.getLogger(__name__)

TABELAS = ("resultado", "resultado_votos")
_CARGO_PRESIDENTE = "1"

# Colunas numéricas de `resultado`, todas somáveis de município para UF e de UF para Brasil.
TOTAIS = (
    "eleitorado",
    "aptos",
    "comparecimento",
    "abstencao",
    "validos",
    "branco",
    "nulo",
    "nulo_tecnico",
    "anulados",
)


class ResultadoIncompleto(RuntimeError):
    pass


class ResultadoInconsistente(RuntimeError):
    pass


@dataclass(frozen=True)
class ConstrucaoResultado:
    ano: int
    turno: int
    municipios: int
    pulado: bool = False


def construir_resultados(dir_dados: Path, *, force: bool = False) -> list[ConstrucaoResultado]:
    """Constrói o silver dos resultados de cada (ano, turno) baixado cujo conteúdo mudou."""
    dir_bronze, dir_silver = dir_dados / "bronze" / "resultados", dir_dados / "silver"
    caminho_manifest = dir_bronze / "manifest.csv"
    if not caminho_manifest.exists():
        return []
    registro_path = dir_silver / "_construcao.json"
    registro = json.loads(registro_path.read_text()) if registro_path.exists() else {}

    por_turno: dict[tuple[int, int], list[EntradaResultado]] = defaultdict(list)
    for entrada in Manifest(caminho_manifest, EntradaResultado).entradas:
        if entrada.status is Status.SUCCESS:
            por_turno[(entrada.ano, entrada.turno)].append(entrada)

    resultados = []
    for (ano, turno), entradas in sorted(por_turno.items()):
        chave = f"resultados-{ano}-{turno}"
        impressao = sorted(e.sha512 for e in entradas)
        anterior = registro.get(chave)
        if not force and anterior and anterior["bronze_sha512"] == impressao:
            resultados.append(ConstrucaoResultado(ano, turno, anterior["municipios"], pulado=True))
            continue

        resultado = _construir_turno(dir_bronze, dir_silver, ano, turno, entradas)
        registro[chave] = {
            "bronze_sha512": impressao,
            "municipios": resultado.municipios,
            "construido_em": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        registro_path.parent.mkdir(parents=True, exist_ok=True)
        registro_path.write_text(json.dumps(registro, indent=2, sort_keys=True))
        resultados.append(resultado)
    return resultados


def _construir_turno(
    dir_bronze: Path, dir_silver: Path, ano: int, turno: int, entradas: list[EntradaResultado]
) -> ConstrucaoResultado:
    por_chave = {(e.abrangencia, e.uf, e.cd_municipio): e for e in entradas}
    config = por_chave.pop(("config", "", ""), None)
    if config is None:
        raise ResultadoIncompleto(f"{ano}/{turno}: sem a lista de municípios")
    lista = json.loads((dir_bronze / config.caminho).read_bytes())

    todos = municipios(lista)
    esperados = (
        {("brasil", "", "")}
        | {("uf", uf, "") for uf, _ in todos}
        | {("municipio", uf, cd) for uf, cd in todos}
    )
    faltando = sorted(esperados - por_chave.keys())
    if faltando:
        raise ResultadoIncompleto(
            f"{ano}/{turno}: faltam {len(faltando)} de {len(esperados)} arquivos, "
            f"ex: {faltando[:3]}"
        )

    cadastro = {
        (abrangencia["cd"].upper(), m["cd"]): (m["nm"], int(m["cdi"]) if m["cdi"] else None)
        for abrangencia in lista["abr"]
        for m in abrangencia["mu"]
    }
    linhas, votos, problemas = [], [], []
    for chave in sorted(esperados):
        entrada = por_chave[chave]
        linha, candidatos = _ler(dir_bronze / entrada.caminho, entrada, cadastro)
        problemas += _conferir_linha(linha, candidatos)
        linhas.append(linha)
        votos += [{**_identidade(linha), **c} for c in candidatos]
    problemas += _conferir_hierarquia(linhas, votos)
    if problemas:
        raise ResultadoInconsistente(
            f"{ano}/{turno}: {len(problemas)} contas não fecham, ex: {problemas[:5]}"
        )

    _gravar(dir_silver, ano, turno, linhas, votos)
    n_municipios = sum(linha["nivel"] == "municipio" for linha in linhas)
    log.info("%s/%s: %d municípios nos resultados divulgados", ano, turno, n_municipios)
    return ConstrucaoResultado(ano, turno, n_municipios)


def _ler(
    caminho: Path, entrada: EntradaResultado, cadastro: dict[tuple[str, str], tuple]
) -> tuple[dict, list[dict]]:
    dados = json.loads(caminho.read_bytes())
    e, v = dados["e"], dados["v"]
    [cargo] = [c for c in dados["carg"] if c["cd"] == _CARGO_PRESIDENTE]
    nm_municipio, cd_ibge = cadastro.get((entrada.uf, entrada.cd_municipio), (None, None))
    linha = {
        "nivel": entrada.abrangencia,
        "uf": entrada.uf or None,
        "cd_municipio": int(entrada.cd_municipio) if entrada.cd_municipio else None,
        "nm_municipio": nm_municipio,
        "cd_ibge": cd_ibge,
        "eleitorado": int(e["te"]),
        # Eleitores das seções apuradas: o que o BU chama de aptos (sem as não instaladas).
        "aptos": int(e["esa"]),
        "comparecimento": int(e["c"]),
        "abstencao": int(e["a"]),
        "validos": int(v["vv"]),
        "branco": int(v["vb"]),
        "nulo": int(v["vn"]),
        # Votos em candidato cujo registro caiu: o TSE soma aos nulos, o BU conta como nominal.
        "nulo_tecnico": int(v["vnt"]),
        "anulados": int(v["van"]) + int(v["vansj"]),
        "gerado_em": entrada.gerado_em,
    }
    candidatos = [
        {
            "nr_votavel": int(candidato["n"]),
            "nm_votavel": candidato["nmu"],
            "sg_partido": partido["sg"],
            "votos": int(candidato["vap"]),
        }
        for agremiacao in cargo["agr"]
        for partido in agremiacao["par"]
        for candidato in partido["cand"]
    ]
    return linha, candidatos


def _identidade(linha: dict) -> dict:
    return {campo: linha[campo] for campo in ("nivel", "uf", "cd_municipio")}


def _rotulo(linha: dict) -> str:
    return "/".join(str(linha[c]) for c in ("nivel", "uf", "cd_municipio") if linha[c])


def _conferir_linha(linha: dict, candidatos: list[dict]) -> list[str]:
    problemas = []
    if linha["aptos"] != linha["comparecimento"] + linha["abstencao"]:
        problemas.append(f"{_rotulo(linha)}: aptos <> comparecimento + abstenção")
    votos = sum(linha[c] for c in ("validos", "branco", "nulo", "nulo_tecnico", "anulados"))
    if linha["comparecimento"] != votos:
        problemas.append(f"{_rotulo(linha)}: comparecimento <> soma dos votos")
    if linha["validos"] != sum(c["votos"] for c in candidatos):
        problemas.append(f"{_rotulo(linha)}: válidos <> soma dos candidatos")
    return problemas


def _conferir_hierarquia(linhas: list[dict], votos: list[dict]) -> list[str]:
    """Os municípios somam a UF, e as UFs somam o Brasil, em cada total e em cada candidato."""

    def chave(registro: dict) -> tuple:
        return (registro["nivel"], registro["uf"], registro["cd_municipio"])

    valores: dict[tuple, Counter] = defaultdict(Counter)
    for linha in linhas:
        valores[chave(linha)].update({c: linha[c] for c in TOTAIS})
    for voto in votos:
        valores[chave(voto)][f"votos_{voto['nr_votavel']}"] += voto["votos"]

    somado: dict[tuple, Counter] = defaultdict(Counter)
    for (nivel, uf, _), valor in valores.items():
        if nivel == "municipio":
            somado[("uf", uf, None)].update(valor)
        elif nivel == "uf":
            somado[("brasil", None, None)].update(valor)

    problemas = []
    for pai, soma in somado.items():
        proprio = valores[pai]
        diferentes = sorted(c for c in soma.keys() | proprio.keys() if soma[c] != proprio[c])
        if diferentes:
            problemas.append(f"{pai[1] or 'brasil'}: soma dos filhos <> {diferentes}")
    return problemas


def _gravar(dir_silver: Path, ano: int, turno: int, linhas: list[dict], votos: list[dict]) -> None:
    particao = Path(f"ano={ano}") / f"turno={turno}"
    saida_tmp = dir_silver / "_tmp_resultados" / particao
    shutil.rmtree(saida_tmp, ignore_errors=True)
    try:
        con = duckdb.connect()
        totais = ", ".join(f"{c} integer" for c in TOTAIS)
        con.execute(
            f"""
            create table resultado (
                nivel varchar, uf varchar, cd_municipio integer, nm_municipio varchar,
                cd_ibge integer, {totais}, gerado_em timestamptz
            )
            """
        )
        con.execute(
            """
            create table resultado_votos (
                nivel varchar, uf varchar, cd_municipio integer,
                nr_votavel integer, nm_votavel varchar, sg_partido varchar, votos integer
            )
            """
        )
        for tabela, registros in (("resultado", linhas), ("resultado_votos", votos)):
            colunas = list(registros[0])
            con.executemany(
                f"insert into {tabela} ({', '.join(colunas)}) "
                f"values ({', '.join('?' for _ in colunas)})",
                [list(r.values()) for r in registros],
            )
            destino = saida_tmp / tabela
            destino.mkdir(parents=True)
            con.execute(
                f"copy (select * from {tabela} order by all) "
                f"to '{destino / 'dados.parquet'}' (format parquet)"
            )
        con.close()

        for tabela in TABELAS:
            destino = dir_silver / tabela / particao
            shutil.rmtree(destino, ignore_errors=True)
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(saida_tmp / tabela, destino)
    finally:
        shutil.rmtree(dir_silver / "_tmp_resultados", ignore_errors=True)
