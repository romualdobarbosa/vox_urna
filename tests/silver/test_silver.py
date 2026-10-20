import hashlib
import io
import shutil
import zipfile
from pathlib import Path

import duckdb
import pytest

from vox_urna.bronze.manifest import Entrada, Manifest, Status
from vox_urna.silver.construcao import RejeicaoExcessiva, construir
from vox_urna.silver.extracao import extrair_presidente

FIXTURES = Path(__file__).parent.parent / "fixtures" / "bu"
AC_2018 = "BWEB_1t_AC_101020181938.zip"
AC_2022 = "bweb_1t_AC_051020221321.zip"
AC_2026 = "bweb_1t_AC_051020261403.zip"
BA_2018_COM_URNA_ANULADA = "BWEB_1t_BA_101020181938.zip"


def montar_bronze(dados: Path, *zips: str | tuple[str, bytes]) -> None:
    """Põe zips no bronze de `dados` e registra cada um no manifest com seu sha512 real."""
    manifest = Manifest(dados / "bronze" / "manifest.csv")
    for item in zips:
        nome, conteudo = item if isinstance(item, tuple) else (item, None)
        _, turno, uf, carimbo = Path(nome).stem.split("_")
        ano = int(carimbo[4:8])
        caminho = Path(f"ano={ano}") / f"turno={turno[0]}" / nome
        destino = dados / "bronze" / caminho
        destino.parent.mkdir(parents=True, exist_ok=True)
        if conteudo is None:
            shutil.copy(FIXTURES / nome, destino)
        else:
            destino.write_bytes(conteudo)
        manifest.registrar(
            Entrada(
                url=f"https://cdn.tse.jus.br/{nome}",
                ano=ano,
                turno=int(turno[0]),
                uf=uf,
                caminho=caminho.as_posix(),
                sha512=hashlib.sha512(destino.read_bytes()).hexdigest(),
                bytes=destino.stat().st_size,
                downloaded_at="2026-10-20T00:00:00+00:00",
                status=Status.SUCCESS,
            )
        )


def editar_presidente(nome: str, editar) -> tuple[str, bytes]:
    """Cópia do zip de fixture com as linhas transformadas por `editar`."""
    with zipfile.ZipFile(FIXTURES / nome) as origem:
        [csv] = origem.namelist()
        cabecalho, *linhas = origem.read(csv).decode("latin-1").splitlines(keepends=True)
    colunas = [c.strip('"') for c in cabecalho.rstrip("\r\n").split(";")]
    presidente = [i for i, linha in enumerate(linhas) if '"Presidente"' in linha]
    linhas = editar(linhas, presidente, colunas)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as saida:
        saida.writestr(csv, (cabecalho + "".join(linhas)).encode("latin-1"))
    return nome, buffer.getvalue()


def ler(dados: Path, tabela: str) -> list[dict]:
    rel = duckdb.sql(
        f"select * from read_parquet('{dados}/silver/{tabela}/*/*/*.parquet', "
        "hive_partitioning = true) order by all"
    )
    return [dict(zip(rel.columns, linha, strict=True)) for linha in rel.fetchall()]


def rodar(dados: Path, **kwargs):
    return construir(dados, workers=1, **kwargs)


def test_extracao_normaliza_cabecalho_de_2018_e_converte_para_utf8(tmp_path):
    destino = tmp_path / "ac.csv"
    linhas = extrair_presidente(FIXTURES / AC_2018, destino)

    cabecalho, *resto = destino.read_text(encoding="utf-8").splitlines()
    colunas = cabecalho.split(";")
    assert "SG_UF" in colunas and "SG_ UF" not in colunas
    assert "DS_SECOES_AGREGADAS" in colunas and "DS_AGREGADAS" not in colunas
    assert linhas == len(resto) > 0
    assert all("Presidente" in linha for linha in resto)


def test_uma_linha_por_secao_e_invariantes_nos_tres_anos(tmp_path):
    montar_bronze(tmp_path, AC_2018, AC_2022, AC_2026)
    resultados = rodar(tmp_path)

    assert [(r.ano, r.secoes, r.rejeitadas) for r in resultados] == [
        (2018, 3, 0),
        (2022, 3, 0),
        (2026, 3, 0),
    ]
    secoes = ler(tmp_path, "secao")
    votos = ler(tmp_path, "votos")
    for s in secoes:
        assert s["branco"] + s["nulo"] + s["nominais"] == s["comparecimento"]
        assert s["aptos"] == s["comparecimento"] + s["abstencao"]
        chave = (s["ano"], s["uf"], s["cd_municipio"], s["zona"], s["secao"])
        assert s["nominais"] == sum(
            v["votos"]
            for v in votos
            if (v["ano"], v["uf"], v["cd_municipio"], v["zona"], v["secao"]) == chave
        )


def test_codigo_de_municipio_de_2018_perde_o_zero_a_esquerda(tmp_path):
    montar_bronze(tmp_path, AC_2018)
    rodar(tmp_path)

    assert {(s["uf"], s["cd_municipio"]) for s in ler(tmp_path, "secao")} == {("AC", 1007)}


def test_urna_anulada_vai_para_quarentena(tmp_path):
    montar_bronze(tmp_path, BA_2018_COM_URNA_ANULADA)
    rodar(tmp_path, limite_rejeicao=0.5)

    [rejeitada] = ler(tmp_path, "rejeitadas")
    assert (rejeitada["nm_municipio"], rejeitada["zona"], rejeitada["secao"]) == (
        "SALVADOR",
        18,
        202,
    )
    assert rejeitada["motivo"] == "urna_anulada"
    assert 202 not in {s["secao"] for s in ler(tmp_path, "secao")}
    assert 202 not in {v["secao"] for v in ler(tmp_path, "votos")}


def test_rejeicao_acima_do_limite_aborta_sem_gravar(tmp_path):
    montar_bronze(tmp_path, BA_2018_COM_URNA_ANULADA)

    with pytest.raises(RejeicaoExcessiva, match="1 de 3"):
        rodar(tmp_path)
    assert not (tmp_path / "silver" / "secao").exists()


def test_votos_diferentes_do_comparecimento_vao_para_quarentena(tmp_path):
    def somar_um_voto(linhas, presidente, colunas):
        i = presidente[0]
        campos = linhas[i].rstrip("\r\n").split(";")
        qt = colunas.index("QT_VOTOS")
        campos[qt] = str(int(campos[qt]) + 1)
        linhas[i] = ";".join(campos) + "\n"
        return linhas

    montar_bronze(tmp_path, editar_presidente(AC_2026, somar_um_voto))
    rodar(tmp_path, limite_rejeicao=0.5)

    [rejeitada] = ler(tmp_path, "rejeitadas")
    assert rejeitada["motivo"] == "votos_diferente_do_comparecimento"


def test_secao_duplicada_vai_para_quarentena(tmp_path):
    def duplicar(linhas, presidente, colunas):
        return linhas + [linhas[presidente[0]]]

    montar_bronze(tmp_path, editar_presidente(AC_2026, duplicar))
    rodar(tmp_path, limite_rejeicao=0.5)

    assert {r["motivo"] for r in ler(tmp_path, "rejeitadas")} == {"secao_duplicada"}


def test_rerun_pula_turno_sem_mudanca_no_bronze(tmp_path):
    montar_bronze(tmp_path, AC_2026)
    rodar(tmp_path)

    [resultado] = rodar(tmp_path)
    assert resultado.pulado
    assert resultado.secoes == 3
    [resultado] = rodar(tmp_path, force=True)
    assert not resultado.pulado


def test_bronze_novo_reconstroi_o_turno(tmp_path):
    montar_bronze(tmp_path, AC_2026)
    rodar(tmp_path)

    def reordenar(linhas, presidente, colunas):
        return linhas[::-1]

    montar_bronze(tmp_path, editar_presidente(AC_2026, reordenar))
    [resultado] = rodar(tmp_path)
    assert not resultado.pulado
