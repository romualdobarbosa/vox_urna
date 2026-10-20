import zipfile
from pathlib import Path

# Nomes de 2018 que mudaram depois; o silver usa sempre o nome atual.
RENOMEAR = {
    "SG_ UF": "SG_UF",
    "DS_AGREGADAS": "DS_SECOES_AGREGADAS",
    "CD_FLASCARD_URNA_EFETIVADA": "CD_FLASHCARD_URNA_EFETIVADA",
    "QT_ELEITORES_BIOMETRIA_NH": "QT_ELEI_BIOM_SEM_HABILITACAO",
}

# Pré-filtro barato por bytes; o filtro exato (CD_CARGO_PERGUNTA = 1) é feito no DuckDB.
_MARCA_PRESIDENTE = b"Presidente"


def extrair_presidente(zip_bu: Path, destino: Path) -> int:
    """Copia para `destino` (UTF-8) o cabeçalho normalizado e as linhas que citam Presidente.

    O CSV do TSE tem dezenas de GB descomprimido; só ~7% das linhas são de Presidente,
    então ele é lido em streaming de dentro do zip, sem nunca ir inteiro para o disco.
    """
    with zipfile.ZipFile(zip_bu) as arquivo_zip:
        [nome_csv] = [n for n in arquivo_zip.namelist() if n.lower().endswith(".csv")]
        destino.parent.mkdir(parents=True, exist_ok=True)
        linhas = 0
        with arquivo_zip.open(nome_csv) as origem, destino.open("w", encoding="utf-8") as saida:
            saida.write(_cabecalho(origem.readline()) + "\n")
            for linha in origem:
                if _MARCA_PRESIDENTE in linha:
                    saida.write(linha.decode("latin-1").rstrip("\r\n") + "\n")
                    linhas += 1
    return linhas


def _cabecalho(linha: bytes) -> str:
    colunas = [c.strip().strip('"') for c in linha.decode("latin-1").rstrip("\r\n").split(";")]
    return ";".join(RENOMEAR.get(c, c) for c in colunas)
