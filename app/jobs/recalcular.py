"""
Recalcula semelhantes, indicadores, oportunidades e score.

Os semelhantes vêm de todos os hospitais carregados, de qualquer UF; `--uf`
só escolhe de quais hospitais o scan é refeito. Recalcular o mesmo período
substitui o anterior.

Uso:
    python -m app.jobs.recalcular --uf CE
    python -m app.jobs.recalcular --uf TODAS --meses 3
    python -m app.jobs.recalcular --uf CE --competencias 202605,202606,202607
"""
from __future__ import annotations

import argparse
import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.classificacao import grupo_natureza
from app.domain.resumo import _lotes, competencias_carregadas
from app.engine.motor import CONFIRMADA, RevenueOpportunityEngine
from app.engine.perfil import montar_perfis
from app.jobs.carga_sih import ufs_do_argumento
from app.models import BenchmarkMetric, HospitalScore, Opportunity, PeerGroup, PeerGroupMember

logger = logging.getLogger(__name__)
_COMMIT_A_CADA = 200


def recalcular(db: Session, *, ufs: list[str] | None = None, competencias: list[str] | None = None,
               meses: int = 3) -> dict[str, Any]:
    lista = sorted(set(competencias)) if competencias else competencias_carregadas(db)[-meses:]
    if not lista:
        raise ValueError("Nenhuma competência do SIH carregada para recalcular")
    inicio, fim = lista[0], lista[-1]

    perfis = montar_perfis(db, lista)
    motor = RevenueOpportunityEngine(perfis, lista)
    alvos = sorted(c for c, p in perfis.items() if ufs is None or p.uf in set(ufs))

    for lote in _lotes(alvos):
        periodo = dict(periodo_inicio=inicio, periodo_fim=fim)
        db.execute(delete(Opportunity).filter_by(**periodo).where(Opportunity.cnes.in_(lote)))
        db.execute(delete(HospitalScore).filter_by(**periodo).where(HospitalScore.cnes.in_(lote)))
        for grupo in db.execute(select(PeerGroup).filter_by(**periodo).where(PeerGroup.cnes.in_(lote))).scalars():
            db.delete(grupo)
    db.flush()

    total_oportunidades = 0
    for i, cnes in enumerate(alvos, start=1):
        analise = motor.analisar(cnes)
        grupo = PeerGroup(cnes=cnes, periodo_inicio=inicio, periodo_fim=fim, criterio=analise.criterio)
        grupo.membros = [PeerGroupMember(cnes=c, distancia=d) for c, d in analise.pares]
        grupo.metricas = [BenchmarkMetric(cnes=cnes, **m) for m in analise.indicadores]
        db.add(grupo)
        db.add_all(Opportunity(cnes=cnes, periodo_inicio=inicio, periodo_fim=fim, **o) for o in analise.oportunidades)
        p = perfis[cnes]
        confirmado = sum(float(o["estimated_financial_impact"] or 0)
                         for o in analise.oportunidades if o["status"] == CONFIRMADA)
        db.add(HospitalScore(
            cnes=cnes, periodo_inicio=inicio, periodo_fim=fim, score=analise.score,
            impacto_estimado=analise.impacto_estimado, valor_apresentado=round(p.valor_apresentado, 2),
            principal_tipo=analise.principal_tipo, principal_categoria=analise.principal_categoria,
            impacto_confirmado=round(confirmado, 2), impacto_sinais=round(analise.impacto_estimado - confirmado, 2),
            uf=p.uf, codigo_municipio=p.codigo_municipio, natureza_codigo=p.natureza_codigo,
            natureza_grupo=grupo_natureza(p.natureza_codigo), gestao=p.gestao, porte=p.porte,
            leitos_sus=p.leitos_sus,
        ))
        total_oportunidades += len(analise.oportunidades)
        if i % _COMMIT_A_CADA == 0:
            db.commit()
    db.commit()
    logger.info("Recalculado %s–%s: %d hospitais, %d oportunidades", inicio, fim, len(alvos), total_oportunidades)
    return {"competencias": lista, "hospitais": len(alvos), "oportunidades": total_oportunidades}


def job_recalcular(ufs: list[str] | None = None, meses: int = 3) -> dict[str, Any]:
    """Entrada da fila (RQ): refaz semelhantes, oportunidades e score sem baixar nada."""
    from sqlalchemy.orm import sessionmaker

    from app.db import engine

    with sessionmaker(bind=engine(), expire_on_commit=False)() as db:
        return recalcular(db, ufs=ufs, meses=meses)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recalcula o scan (semelhantes, oportunidades e score).")
    parser.add_argument("--uf", default="TODAS", help="UFs separadas por vírgula ou TODAS (padrão)")
    parser.add_argument("--meses", type=int, default=3, help="Quantas competências recentes (padrão 3)")
    parser.add_argument("--competencias", help="AAAAMM separadas por vírgula, em vez de --meses")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from sqlalchemy.orm import sessionmaker

    from app.db import engine

    ufs = None if args.uf.strip().upper() in {"TODAS", "BR", "BRASIL"} else ufs_do_argumento(args.uf)
    competencias = [c.strip() for c in args.competencias.split(",")] if args.competencias else None
    with sessionmaker(bind=engine(), expire_on_commit=False)() as db:
        print(recalcular(db, ufs=ufs, competencias=competencias, meses=args.meses))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
