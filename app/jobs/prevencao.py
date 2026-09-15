"""
Prevenção pelo worker: manda as AIH rejeitadas de uma UF ao FaturaSUS do núcleo.

Roda sozinha depois de cada carga (quando a chave INTERNAL_SERVICE_TOKEN está
configurada) e pela tela de dados. Pelo terminal:

    python -m app.jobs.prevencao --uf CE
    python -m app.jobs.prevencao --uf CE --competencias 202606,202607
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.faturasus import FaturaSusNucleo
from app.adapters.ibge import validar_uf
from app.config import get_settings
from app.domain.kits_motivo import atualizar_estatisticas_prevencao
from app.domain.prevencao import Motor, avaliar_competencia
from app.models import DataLoad, SihRejection

logger = logging.getLogger(__name__)
SEM_CHAVE = "INTERNAL_SERVICE_TOKEN não configurado: a prevenção não roda."


def motor_padrao() -> FaturaSusNucleo | None:
    settings = get_settings()
    if len(settings.internal_service_token) < 32:
        return None
    return FaturaSusNucleo(settings.core_api_url, settings.internal_service_token)


def rodar(db: Session, uf: str, competencias: list[str] | None, motor: Motor) -> list[dict[str, Any]]:
    uf = validar_uf(uf)
    meses = sorted(set(competencias)) if competencias else sorted(set(db.execute(
        select(SihRejection.competencia).where(SihRejection.uf == uf).distinct()).scalars()))
    saida = []
    for competencia in meses:
        carga = DataLoad(fonte="FATURASUS", uf=uf, competencia=competencia, origem="API", status="RUNNING")
        db.add(carga)
        db.commit()
        try:
            resultado = avaliar_competencia(db, uf, competencia, motor)
        except Exception as exc:
            db.rollback()
            carga.status, carga.erro = "FAILED", f"{exc.__class__.__name__}: {exc}"[:2000]
            carga.concluido_em = datetime.now(timezone.utc)
            db.commit()
            raise
        carga.status, carga.linhas, carga.concluido_em = "OK", resultado["avaliadas"], datetime.now(timezone.utc)
        db.commit()
        logger.info("Prevenção %s %s: %s", uf, competencia, resultado)
        saida.append({"competencia": competencia, **resultado})
    if meses:
        # A taxa do FaturaSUS por motivo, que os kits mostram, acompanha a prevenção da UF.
        atualizar_estatisticas_prevencao(db, uf)
    return saida


def job_prevencao_uf(uf: str, competencias: list[str] | None = None) -> list[dict[str, Any]] | dict[str, str]:
    """Entrada da fila (RQ)."""
    from sqlalchemy.orm import sessionmaker

    from app.db import engine

    motor = motor_padrao()
    if motor is None:
        logger.warning(SEM_CHAVE)
        return {"pulado": SEM_CHAVE}
    with sessionmaker(bind=engine(), expire_on_commit=False)() as db, motor:
        return rodar(db, uf, competencias, motor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prevenção: o FaturaSUS avalia as AIH rejeitadas de uma UF.")
    parser.add_argument("--uf", required=True)
    parser.add_argument("--competencias", help="AAAAMM separadas por vírgula; sem isto, todas as carregadas")
    parser.add_argument("--so-estatisticas", action="store_true",
                        help="Só refaz a taxa do FaturaSUS por motivo com a prevenção já gravada, sem chamar o núcleo")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.so_estatisticas:
        from sqlalchemy.orm import sessionmaker

        from app.db import engine

        with sessionmaker(bind=engine())() as db:
            print(f"{atualizar_estatisticas_prevencao(db, validar_uf(args.uf))} motivos")
        return 0
    competencias = [c.strip() for c in args.competencias.split(",")] if args.competencias else None
    print(job_prevencao_uf(args.uf, competencias))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
