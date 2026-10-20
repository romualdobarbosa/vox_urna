import argparse
import logging
import sys
from pathlib import Path

from vox_urna.bronze.ingest import ingerir
from vox_urna.bronze.manifest import Status

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

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

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


if __name__ == "__main__":
    sys.exit(main())
