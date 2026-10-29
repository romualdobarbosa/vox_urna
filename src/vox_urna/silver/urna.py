"""BUs dos arquivos de urna em linhas no formato do bweb, para o silver ler como lê o CKAN."""

import csv
import json
import logging
from collections.abc import Mapping
from pathlib import Path

import duckdb

from vox_urna.bronze.manifest import Manifest, Status
from vox_urna.bronze.urna import EntradaUrna, decodificar_bu

log = logging.getLogger(__name__)

# Eleição de Presidente dentro do pleito: o BU traz também a de governador.
ELEICOES_PRESIDENTE = {(2026, 2): 6258}

# Só as colunas do bweb que o silver lê, com os nomes atuais.
COLUNAS = (
    "SG_UF",
    "CD_MUNICIPIO",
    "NM_MUNICIPIO",
    "NR_ZONA",
    "NR_SECAO",
    "CD_CARGO_PERGUNTA",
    "CD_TIPO_VOTAVEL",
    "NR_VOTAVEL",
    "NM_VOTAVEL",
    "SG_PARTIDO",
    "QT_VOTOS",
    "QT_APTOS",
    "QT_COMPARECIMENTO",
    "QT_ABSTENCOES",
    "DS_TIPO_URNA",
    "DS_SECOES_AGREGADAS",
    # Não existe no bweb: a situação da seção no site de resultados (Totalizada ou Recebida).
    "ST_SECAO",
)

# TipoVoto do ASN.1 → CD_TIPO_VOTAVEL do bweb. Branco e nulo têm número fixo no bweb.
_TIPO_VOTAVEL = {"nominal": 1, "branco": 2, "nulo": 3, "legenda": 4, "cargoSemCandidato": 5}
_NR_FIXO = {"branco": (95, "Branco"), "nulo": (96, "Nulo")}
_PRESIDENTE = ("cargoConstitucional", "presidente")

# Número do candidato → (nome de urna, sigla do partido).
Candidatos = Mapping[int, tuple[str, str]]


def candidatos_divulgados(dir_silver: Path, ano: int, turno: int) -> dict[int, tuple[str, str]]:
    """O nome e o partido de cada candidato, do silver `resultado_votos` do Brasil.

    É o mesmo nome de urna que o bweb traz, e o BU binário não tem.
    """
    particao = dir_silver / "resultado_votos" / f"ano={ano}" / f"turno={turno}"
    if not any(particao.glob("*.parquet")):
        raise RuntimeError(
            f"{ano}/{turno}: sem o silver resultado_votos, de onde vêm os nomes dos candidatos "
            "do BU dos arquivos de urna; rode bronze-resultados e o silver dos resultados antes"
        )
    linhas = duckdb.execute(
        "select nr_votavel, nm_votavel, sg_partido from read_parquet($glob) where nivel = 'brasil'",
        {"glob": str(particao / "*.parquet")},
    ).fetchall()
    return {numero: (nome, partido) for numero, nome, partido in linhas}


def linhas_presidente(
    bu: dict,
    uf: str,
    nm_municipio: str,
    eleicao: int,
    agregadas: list[str] | None = None,
    candidatos: Candidatos | None = None,
) -> list[dict]:
    """As linhas de Presidente de um BU decodificado, uma por votável, como no bweb.

    O BU não traz nome de candidato nem sigla de partido, só os números: o NM_VOTAVEL e o
    SG_PARTIDO dos nominais vêm de `candidatos` e ficam vazios sem ele. As seções agregadas
    também não vêm no BU, vêm da config (`nsa`). Um BU sem votável de Presidente (no exterior,
    um busa só com abstenção) não gera linha, e a seção fica fora do silver, como no bweb.
    """
    identificacao = bu["identificacaoSecao"]
    [resultado] = [r for r in bu["resultadosVotacaoPorEleicao"] if r["idEleicao"] == eleicao]
    aptos = resultado["qtdEleitoresAptos"]
    [(comparecimento, votaveis)] = [
        (votacao["qtdComparecimento"], cargo["votosVotaveis"])
        for votacao in resultado["resultadosVotacao"]
        for cargo in votacao["totaisVotosCargo"]
        if cargo["codigoCargo"] == _PRESIDENTE
    ]
    comum = {
        "SG_UF": uf,
        "CD_MUNICIPIO": identificacao["municipioZona"]["municipio"],
        "NM_MUNICIPIO": nm_municipio,
        "NR_ZONA": identificacao["municipioZona"]["zona"],
        "NR_SECAO": identificacao["secao"],
        "CD_CARGO_PERGUNTA": 1,
        "QT_APTOS": aptos,
        "QT_COMPARECIMENTO": comparecimento,
        "QT_ABSTENCOES": aptos - comparecimento,
        # O arquivo de urna não diz se a urna foi anulada; a conferência com o resultado
        # divulgado (que tem os anulados) é o que pega esse caso.
        "DS_TIPO_URNA": "Apurada",
        # Como no bweb: "43 / 44", sem zeros à esquerda.
        "DS_SECOES_AGREGADAS": " / ".join(str(int(n)) for n in agregadas)
        if agregadas
        else "#NULO#",
    }
    linhas = []
    for votavel in votaveis:
        tipo = votavel["tipoVoto"]
        partido = ""
        if tipo in _NR_FIXO:
            numero, nome = _NR_FIXO[tipo]
        else:
            numero, nome = votavel.get("identificacaoVotavel", {}).get("codigo", -1), ""
            if candidatos is not None:
                if numero not in candidatos:
                    raise ValueError(f"candidato {numero} fora dos resultados divulgados")
                nome, partido = candidatos[numero]
        linhas.append(
            comum
            | {
                "CD_TIPO_VOTAVEL": _TIPO_VOTAVEL.get(tipo, -1),
                "NR_VOTAVEL": numero,
                "NM_VOTAVEL": nome,
                "SG_PARTIDO": partido,
                "QT_VOTOS": votavel["quantidadeVotos"],
            }
        )
    return linhas


def extrair_presidente_urna(
    dir_bronze: Path,
    ano: int,
    turno: int,
    uf: str,
    destino: Path,
    *,
    candidatos: Candidatos | None = None,
) -> int:
    """Grava em `destino` as linhas de Presidente dos BUs baixados da UF, no CSV do bweb.

    O silver lê esse CSV como lê o que `extrair_presidente` tira do zip do CKAN.
    """
    particao = Path(f"ano={ano}") / f"turno={turno}"
    eleicao = ELEICOES_PRESIDENTE[(ano, turno)]
    [caminho_config] = (dir_bronze / particao / "config").glob(f"{uf.lower()}-p*-cs.json")
    config = json.loads(caminho_config.read_bytes())
    municipios = [municipio for abrangencia in config["abr"] for municipio in abrangencia["mu"]]
    nomes = {int(municipio["cd"]): municipio["nm"] for municipio in municipios}
    agregadas = {
        (int(municipio["cd"]), int(zona["cd"]), int(secao["ns"])): secao["nsa"]
        for municipio in municipios
        for zona in municipio["zon"]
        for secao in zona["sec"]
        if "nsa" in secao
    }

    manifest = Manifest(dir_bronze / particao / uf / "manifest.csv", EntradaUrna)
    entradas = sorted(
        (e for e in manifest.entradas if e.status is Status.SUCCESS), key=lambda e: e.chave
    )
    destino.parent.mkdir(parents=True, exist_ok=True)
    linhas = 0
    with destino.open("w", newline="", encoding="utf-8") as saida:
        escritor = csv.DictWriter(saida, fieldnames=COLUNAS, delimiter=";", quoting=csv.QUOTE_ALL)
        escritor.writeheader()
        for entrada in entradas:
            bu = decodificar_bu((dir_bronze / entrada.caminho).read_bytes())
            identificacao = bu["identificacaoSecao"]
            municipio = identificacao["municipioZona"]["municipio"]
            chave = (municipio, identificacao["municipioZona"]["zona"], identificacao["secao"])
            registros = linhas_presidente(
                bu, uf, nomes[municipio], eleicao, agregadas.get(chave), candidatos
            )
            for registro in registros:
                registro["ST_SECAO"] = entrada.st_secao
            if not registros:
                log.warning("%s %s/%s/%s: BU sem voto de Presidente, fora do silver", uf, *chave)
            escritor.writerows(registros)
            linhas += len(registros)
    return linhas
