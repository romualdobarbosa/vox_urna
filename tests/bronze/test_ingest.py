import hashlib

import pytest
import requests
import responses

from vox_urna.bronze.ckan import CKAN_API, resolver
from vox_urna.bronze.ingest import ingerir
from vox_urna.bronze.manifest import Manifest, Status

CDN = "https://cdn.tse.jus.br/estatistica/sead/eleicoes"
ZIP_AC = f"{CDN}/eleicoes2026/buweb/bweb_1t_AC_051020261403.zip"
ZIP_ZZ = f"{CDN}/eleicoes2026/buweb/bweb_1t_ZZ_051020261403.zip"
CONTEUDO = {ZIP_AC: b"zip do acre", ZIP_ZZ: b"zip do exterior"}


def sha512(conteudo: bytes) -> str:
    return hashlib.sha512(conteudo).hexdigest()


def registrar_ckan(urls_zip: list[str], ano: int = 2026) -> None:
    recursos = [{"url": u} for url in urls_zip for u in (url, url + ".sha512")]
    recursos.append({"url": f"{CDN}/eleicoes{ano}/buweb/leiame.pdf"})
    responses.get(
        f"{CKAN_API}/package_show?id=resultados-{ano}-boletim-de-urna",
        json={"success": True, "result": {"resources": recursos}},
    )


def registrar_arquivo(url: str, conteudo: bytes, sha_oficial: str | None = None) -> None:
    nome = url.rsplit("/", 1)[1]
    responses.get(url + ".sha512", body=f"{sha_oficial or sha512(conteudo)}  {nome}\n")
    responses.get(url, body=conteudo)


def chamadas_a(url: str) -> int:
    return sum(1 for chamada in responses.calls if chamada.request.url == url)


def rodar(tmp_path, **kwargs):
    return ingerir(tmp_path, [2026], espera_base=0, workers=1, **kwargs)


@responses.activate
def test_resolver_extrai_turno_uf_e_ignora_o_que_nao_e_bu():
    registrar_ckan([ZIP_AC, ZIP_ZZ])
    recursos = resolver(requests.Session(), 2026)
    assert [(r.ano, r.turno, r.uf) for r in recursos] == [(2026, 1, "AC"), (2026, 1, "ZZ")]


@responses.activate
def test_resolver_aceita_prefixo_maiusculo_de_2018():
    url = f"{CDN}/eleicoes2018/buweb/BWEB_2t_ZZ_301020181744.zip"
    registrar_ckan([url], ano=2018)
    [recurso] = resolver(requests.Session(), 2018)
    assert (recurso.turno, recurso.uf) == (2, "ZZ")


@responses.activate
def test_resolver_exige_sha512_oficial():
    responses.get(
        f"{CKAN_API}/package_show?id=resultados-2026-boletim-de-urna",
        json={"success": True, "result": {"resources": [{"url": ZIP_AC}]}},
    )
    with pytest.raises(ValueError, match="sha512"):
        resolver(requests.Session(), 2026)


@responses.activate
def test_baixa_confere_hash_e_registra_no_manifest(tmp_path):
    registrar_ckan([ZIP_AC, ZIP_ZZ])
    for url, conteudo in CONTEUDO.items():
        registrar_arquivo(url, conteudo)

    assert rodar(tmp_path) == {Status.SUCCESS: 2}

    arquivo = tmp_path / "ano=2026/turno=1/bweb_1t_AC_051020261403.zip"
    assert arquivo.read_bytes() == CONTEUDO[ZIP_AC]
    entrada = Manifest(tmp_path / "manifest.csv").get(ZIP_AC)
    assert entrada.status is Status.SUCCESS
    assert entrada.sha512 == sha512(CONTEUDO[ZIP_AC])
    assert entrada.bytes == len(CONTEUDO[ZIP_AC])
    assert (entrada.ano, entrada.turno, entrada.uf) == (2026, 1, "AC")


@responses.activate
def test_rerun_nao_baixa_de_novo(tmp_path):
    registrar_ckan([ZIP_AC])
    registrar_arquivo(ZIP_AC, CONTEUDO[ZIP_AC])
    rodar(tmp_path)

    assert rodar(tmp_path) == {"pulado": 1}
    assert chamadas_a(ZIP_AC) == 1


@responses.activate
def test_force_baixa_de_novo(tmp_path):
    registrar_ckan([ZIP_AC])
    registrar_arquivo(ZIP_AC, CONTEUDO[ZIP_AC])
    rodar(tmp_path)

    assert rodar(tmp_path, force=True) == {Status.SUCCESS: 1}
    assert chamadas_a(ZIP_AC) == 2


@responses.activate
def test_arquivo_apagado_do_disco_e_baixado_de_novo(tmp_path):
    registrar_ckan([ZIP_AC])
    registrar_arquivo(ZIP_AC, CONTEUDO[ZIP_AC])
    rodar(tmp_path)
    (tmp_path / "ano=2026/turno=1/bweb_1t_AC_051020261403.zip").unlink()

    assert rodar(tmp_path) == {Status.SUCCESS: 1}


@responses.activate
def test_hash_divergente_marca_corrupted_e_nao_deixa_arquivo(tmp_path):
    registrar_ckan([ZIP_AC])
    registrar_arquivo(ZIP_AC, CONTEUDO[ZIP_AC], sha_oficial="0" * 128)

    assert rodar(tmp_path) == {Status.CORRUPTED: 1}
    assert not list((tmp_path / "ano=2026/turno=1").iterdir())
    assert Manifest(tmp_path / "manifest.csv").get(ZIP_AC).status is Status.CORRUPTED


@responses.activate
def test_corrupted_e_baixado_de_novo_no_proximo_run(tmp_path):
    registrar_ckan([ZIP_AC])
    registrar_arquivo(ZIP_AC, CONTEUDO[ZIP_AC], sha_oficial="0" * 128)
    rodar(tmp_path)

    responses.reset()
    registrar_ckan([ZIP_AC])
    registrar_arquivo(ZIP_AC, CONTEUDO[ZIP_AC])
    assert rodar(tmp_path) == {Status.SUCCESS: 1}


@responses.activate
def test_erro_transitorio_e_repetido(tmp_path):
    registrar_ckan([ZIP_AC])
    responses.get(ZIP_AC + ".sha512", body=f"{sha512(CONTEUDO[ZIP_AC])}  x.zip")
    responses.get(ZIP_AC, status=503)
    responses.get(ZIP_AC, body=CONTEUDO[ZIP_AC])

    assert rodar(tmp_path) == {Status.SUCCESS: 1}
    assert chamadas_a(ZIP_AC) == 2


@responses.activate
def test_erro_permanente_marca_failed_sem_repetir(tmp_path):
    registrar_ckan([ZIP_AC])
    responses.get(ZIP_AC + ".sha512", status=404)

    assert rodar(tmp_path) == {Status.FAILED: 1}
    assert chamadas_a(ZIP_AC + ".sha512") == 1


@responses.activate
def test_republicacao_do_tse_substitui_a_versao_anterior(tmp_path):
    registrar_ckan([ZIP_AC])
    registrar_arquivo(ZIP_AC, CONTEUDO[ZIP_AC])
    rodar(tmp_path)

    nova = ZIP_AC.replace("051020261403", "061020260900")
    responses.reset()
    registrar_ckan([nova])
    registrar_arquivo(nova, b"zip do acre corrigido")
    rodar(tmp_path)

    manifest = Manifest(tmp_path / "manifest.csv")
    assert manifest.get(ZIP_AC).status is Status.SUPERSEDED
    assert manifest.get(nova).status is Status.SUCCESS


@responses.activate
def test_filtra_por_uf(tmp_path):
    registrar_ckan([ZIP_AC, ZIP_ZZ])
    registrar_arquivo(ZIP_ZZ, CONTEUDO[ZIP_ZZ])

    assert rodar(tmp_path, ufs=["zz"]) == {Status.SUCCESS: 1}
    assert chamadas_a(ZIP_AC) == 0
