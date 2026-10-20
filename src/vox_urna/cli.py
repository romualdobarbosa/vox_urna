import argparse
import logging
import sys
from pathlib import Path

from vox_urna.bronze.ingest import ingerir
from vox_urna.bronze.manifest import Status
from vox_urna.gold.construcao import GoldInconsistente
from vox_urna.gold.construcao import construir as construir_gold
from vox_urna.silver.construcao import RejeicaoExcessiva, construir

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


def _silver(args: argparse.Namespace) -> int:
    try:
        resultados = construir(args.dados, force=args.force, workers=args.workers)
    except RejeicaoExcessiva as erro:
        print(f"silver abortado: {erro}", file=sys.stderr)
        return 1
    for r in resultados:
        estado = "pulado" if r.pulado else "construído"
        print(f"{r.ano}/{r.turno}: {estado}, {r.secoes} seções, {r.rejeitadas} em quarentena")
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
