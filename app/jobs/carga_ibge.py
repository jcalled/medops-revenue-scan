"""
Municípios do IBGE, para o scan mostrar "Fortaleza/CE" em vez de "230440".

Uso:
    python -m app.jobs.carga_ibge
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.adapters.base import OrigemArquivo
from app.adapters.ibge import municipios_ibge
from app.jobs.carga_sih import _agora
from app.models import DataLoad, Municipality


def carregar_municipios(db: Session, municipios: list[dict[str, Any]] | None = None) -> int:
    carga = DataLoad(fonte="IBGE_MUNICIPIOS", origem=OrigemArquivo.API.value)
    db.add(carga)
    db.commit()
    try:
        lista = municipios if municipios is not None else municipios_ibge()
        for m in lista:
            db.merge(Municipality(**m))
        carga.linhas, carga.status, carga.concluido_em = len(lista), "OK", _agora()
        db.commit()
        return len(lista)
    except Exception as exc:
        db.rollback()
        carga.status, carga.erro, carga.concluido_em = "FAILED", str(exc)[:2000], _agora()
        db.commit()
        raise


def main() -> int:
    from sqlalchemy.orm import sessionmaker

    from app.db import engine

    with sessionmaker(bind=engine())() as db:
        print(f"Municípios: {carregar_municipios(db)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
