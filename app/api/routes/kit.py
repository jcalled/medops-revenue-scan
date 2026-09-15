"""
Kit de recuperação de qualquer recorte do painel (OSS, UF, município, natureza,
seleção de hospitais), dentro do escopo do contrato. A regra está em
app/domain/kit.py.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Acesso, require_revenue_scan
from app.api.routes.explorar import Filtros, _titulo, filtros, hospitais_filtrados
from app.api.routes.scan import _competencias
from app.db import get_db
from app.domain.kit import CLASSES, montar_kit, referencia_padrao

router = APIRouter(prefix="/api/revenue-scan", tags=["kit"])


@router.get("/kit")
def kit_de_recuperacao(
    f: Filtros = Depends(filtros),
    referencia: str | None = Query(default=None, pattern=r"^\d{4}(0[1-9]|1[0-2])$",
                                   description="Mês em que o hospital ainda apresenta (AAAAMM); padrão, o corrente"),
    lista: str = Query(default="trabalho", pattern=r"^(trabalho|todas)$",
                       description="trabalho: recuperáveis, investigar e gestor; todas: inclui já recebidas e vencidas"),
    limite: int = Query(default=5000, ge=1, le=50000),
    acesso: Acesso = Depends(require_revenue_scan),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    scores = [s for s, _ in hospitais_filtrados(db, acesso, f)]
    recorte = {"titulo": _titulo(db, f), "filtros": {k: v for k, v in asdict(f).items() if v}}
    ref = referencia or referencia_padrao()
    if not scores:
        return {"recorte": recorte, "hospitais_analisados": 0, "referencia": ref, "classes_nomes": CLASSES}
    meses = _competencias(min(s.periodo_inicio for s in scores), max(s.periodo_fim for s in scores))
    ufs = sorted({s.uf for s in scores if s.uf})
    corpo = montar_kit(db, [s.cnes for s in scores], meses, ufs, ref, lista=lista, limite=limite)
    return {
        "recorte": recorte,
        "hospitais_analisados": len(scores),
        "periodo": {"inicio": meses[0], "fim": meses[-1], "competencias": meses},
        "classes_nomes": CLASSES,
        **corpo,
        "ressalva": (
            "Reapresentação: janela estimada de seis meses após a alta (art. 401, § 2º, PRC SAES/MS 1/2022), para AIH já apresentada e rejeitada ou bloqueada. Confirmar calendário do gestor. Os dados públicos chegam com atraso: parte das AIH "
            "pode já ter sido reapresentada em processamentos ainda não carregados. Chance média e incerta dependem "
            "de fatos que só o hospital confirma — se a habilitação existe, quantos leitos funcionam."
        ),
    }
