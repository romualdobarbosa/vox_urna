import argparse
import logging
import sys
from pathlib import Path

from vox_urna.bronze.ingest import ingerir
from vox_urna.bronze.manifest import Status
from vox_urna.bronze.resultados import ANOS as ANOS_RESULTADOS
from vox_urna.bronze.resultados import ingerir_resultados
from vox_urna.bronze.urna import PLEITOS as PLEITOS_URNA
from vox_urna.bronze.urna import ingerir_urna
from vox_urna.gold.construcao import GoldInconsistente
from vox_urna.gold.construcao import construir as construir_gold
from vox_urna.silver.construcao import RejeicaoExcessiva, construir
from vox_urna.silver.resultados import (
    ResultadoIncompleto,
    ResultadoInconsistente,
    construir_resultados,
)

ANOS = (2018, 2022, 2026)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vox-urna")
    parser.add_argument("--dados", type=Path, default=Path("data"), help="raiz das camadas")
    sub = parser.add_subparsers(dest="camada", required=True)

    bronze = sub.add_parser("bronze", help="baixa os boletins de urna do TSE")
    bronze.add_argument("--ano", type=int, nargs="+", default=list(ANOS), choices=ANOS)
    bronze.add_argument("--turno", type=int, nargs="+", choices=(1, 2))
    bronze.add_argument("--uf", nargs="+", help="ex: AC ZZ (padrão: todas)")
    bronze.add_argument(
        "--force", action="store_true", help="baixa de novo mesmo o que já foi baixado"
    )
    bronze.add_argument("--workers", type=int, default=4)

    resultados = sub.add_parser(
        "bronze-resultados",
        help="baixa os resultados divulgados pelo TSE (JSON por Brasil, UF e município)",
    )
    resultados.add_argument(
        "--ano", type=int, nargs="+", default=list(ANOS_RESULTADOS), choices=ANOS_RESULTADOS
    )
    resultados.add_argument("--turno", type=int, nargs="+", choices=(1, 2))
    resultados.add_argument("--uf", nargs="+", help="ex: AC ZZ (padrão: todas e o Brasil)")
    resultados.add_argument(
        "--force", action="store_true", help="baixa de novo mesmo o que já foi baixado"
    )
    resultados.add_argument("--workers", type=int, default=4)

    urna = sub.add_parser(
        "bronze-urna",
        help="baixa o BU de cada seção do site de resultados (arquivos de urna)",
    )
    urna.add_argument("--ano", type=int, default=2026, choices=sorted({a for a, _ in PLEITOS_URNA}))
    urna.add_argument("--turno", type=int, default=2, choices=(1, 2))
    urna.add_argument("--uf", nargs="+", help="ex: AC ZZ (padrão: todas)")
    urna.add_argument(
        "--force", action="store_true", help="baixa de novo mesmo o que já foi baixado"
    )
    urna.add_argument("--workers", type=int, default=16)

    silver = sub.add_parser("silver", help="filtra Presidente e gera as tabelas por seção")
    silver.add_argument(
        "--force", action="store_true", help="reconstrói mesmo o que não mudou no bronze"
    )
    silver.add_argument("--workers", type=int, default=8)

    gold = sub.add_parser("gold", help="estima a transferência de votos e projeta 2026")
    gold.add_argument(
        "--min-secoes",
        type=int,
        default=10,
        help="seções mínimas para a zona ter β próprio; abaixo disso usa o da UF",
    )

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.camada == "bronze":
        return _bronze(args)
    if args.camada == "bronze-resultados":
        return _bronze_resultados(args)
    if args.camada == "bronze-urna":
        return _bronze_urna(args)
    if args.camada == "silver":
        return _silver(args)
    return _gold(args)


def _bronze(args: argparse.Namespace) -> int:
    resumo = ingerir(
        args.dados / "bronze",
        args.ano,
        turnos=args.turno,
        ufs=args.uf,
        force=args.force,
        workers=args.workers,
    )
    print(", ".join(f"{desfecho}: {n}" for desfecho, n in sorted(resumo.items())))
    return 1 if resumo[Status.CORRUPTED] or resumo[Status.FAILED] else 0


def _bronze_resultados(args: argparse.Namespace) -> int:
    resumo = ingerir_resultados(
        args.dados / "bronze" / "resultados",
        args.ano,
        turnos=args.turno,
        ufs=args.uf,
        force=args.force,
        workers=args.workers,
    )
    print(", ".join(f"{desfecho}: {n}" for desfecho, n in sorted(resumo.items())))
    falhas = (Status.CORRUPTED, Status.FAILED, Status.INCOMPLETE)
    return 1 if any(resumo[status] for status in falhas) else 0


def _bronze_urna(args: argparse.Namespace) -> int:
    if (args.ano, args.turno) not in PLEITOS_URNA:
        print(f"sem pleito conhecido para {args.ano}/{args.turno}", file=sys.stderr)
        return 1
    resumo = ingerir_urna(
        args.dados / "bronze" / "urna",
        args.ano,
        args.turno,
        ufs=args.uf,
        force=args.force,
        workers=args.workers,
    )
    print(", ".join(f"{desfecho}: {n}" for desfecho, n in sorted(resumo.items())))
    falhas = (Status.CORRUPTED, Status.FAILED, Status.INCOMPLETE)
    return 1 if any(resumo[status] for status in falhas) else 0


def _silver(args: argparse.Namespace) -> int:
    # Os resultados divulgados vêm primeiro: o BU dos arquivos de urna tira deles os nomes.
    try:
        divulgados = construir_resultados(args.dados, force=args.force)
    except (ResultadoIncompleto, ResultadoInconsistente) as erro:
        print(f"silver dos resultados abortado: {erro}", file=sys.stderr)
        return 1
    for r in divulgados:
        estado = "pulado" if r.pulado else "construído"
        print(f"resultados {r.ano}/{r.turno}: {estado}, {r.municipios} municípios")

    try:
        resultados = construir(args.dados, force=args.force, workers=args.workers)
    except RejeicaoExcessiva as erro:
        print(f"silver abortado: {erro}", file=sys.stderr)
        return 1
    for r in resultados:
        estado = "pulado" if r.pulado else "construído"
        print(
            f"{r.ano}/{r.turno}: {estado} (fonte {r.fonte}), {r.secoes} seções, "
            f"{r.rejeitadas} em quarentena"
        )
    return 0


def _gold(args: argparse.Namespace) -> int:
    try:
        tabelas = construir_gold(args.dados, min_secoes=args.min_secoes)
    except GoldInconsistente as erro:
        print(f"gold abortado: {erro}", file=sys.stderr)
        return 1
    for t in tabelas:
        print(f"{t.nome}: {t.linhas} linhas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
