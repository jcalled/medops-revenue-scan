"""
Importa a planilha de prospecção de organizações pelo terminal (a tela faz o mesmo):

    python -m app.seed.prospeccao OSS_Prospecao_Brasil_2026.xlsx
"""
from __future__ import annotations

import argparse
from pathlib import Path

from app.domain.prospeccao import importar, ler_planilha


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Importa a planilha de prospecção (.xlsx).")
    parser.add_argument("planilha", type=Path)
    args = parser.parse_args(argv)

    from sqlalchemy.orm import sessionmaker

    from app.db import engine

    with sessionmaker(bind=engine(), expire_on_commit=False)() as db:
        print(importar(db, ler_planilha(args.planilha.read_bytes()), usuario=None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
