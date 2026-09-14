"""
Organizações e o resumo de rejeição do SIH, dentro do escopo do contrato.

Escopo: `organizations` (siglas) limita quais organizações o tenant vê; `cnes`
limita quais hospitais aparecem dentro delas; `period_months` limita quantos
meses. Sem escopo, o contrato vale para todas — o dado é público. Organização
fora do escopo responde 404, sem dizer que existe.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import Acesso, require_revenue_scan
from app.db import get_db
from app.domain.resumo import resumo_organizacao
from app.models import ManagementOrganization

router = APIRouter(prefix="/api/revenue-scan", tags=["organizacoes"])
_COMPETENCIA = re.compile(r"^\d{6}$")


def _escopo(acesso: Acesso) -> tuple[set[str] | None, set[str] | None, int | None]:
    escopo = acesso.escopo
    siglas = {str(s).upper() for s in escopo["organizations"]} if escopo.get("organizations") else None
    cnes = {str(c).zfill(7) for c in escopo["cnes"]} if escopo.get("cnes") else None
    return siglas, cnes, escopo.get("period_months")


def organizacao_no_escopo(db: Session, acesso: Acesso, organization_id: int) -> ManagementOrganization:
    """A organização, se o contrato a alcança; 404 igual para inexistente e fora do escopo."""
    siglas, cnes, _ = _escopo(acesso)
    org = db.execute(
        select(ManagementOrganization).where(ManagementOrganization.id == organization_id)
        .options(selectinload(ManagementOrganization.unidades))
    ).scalar_one_or_none()
    fora = org is None or (siglas is not None and org.sigla.upper() not in siglas) \
        or (cnes is not None and not any(u.cnes in cnes for u in org.unidades))
    if fora:
        raise HTTPException(status_code=404, detail="Organização não encontrada")
    return org


@router.get("/organizations")
def listar_organizacoes(acesso: Acesso = Depends(require_revenue_scan), db: Session = Depends(get_db)) -> dict[str, Any]:
    siglas, cnes, _ = _escopo(acesso)
    orgs = db.execute(
        select(ManagementOrganization).options(selectinload(ManagementOrganization.unidades))
        .order_by(ManagementOrganization.sigla)
    ).scalars().all()
    saida = []
    for org in orgs:
        if siglas is not None and org.sigla.upper() not in siglas:
            continue
        unidades = [u for u in org.unidades if cnes is None or u.cnes in cnes]
        if cnes is not None and not unidades:
            continue
        saida.append({"id": org.id, "sigla": org.sigla, "nome": org.nome, "uf": org.uf, "hospitais": len(unidades)})
    return {"organizations": saida}


@router.get("/organizations/{organization_id}/summary")
def resumo(
    organization_id: int,
    competencias: str | None = Query(default=None, description="AAAAMM separadas por vírgula"),
    acesso: Acesso = Depends(require_revenue_scan),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _, cnes, limite = _escopo(acesso)
    org = organizacao_no_escopo(db, acesso, organization_id)

    lista = None
    if competencias:
        lista = [c.strip() for c in competencias.split(",") if c.strip()]
        invalidas = [c for c in lista if not _COMPETENCIA.match(c)]
        if invalidas:
            raise HTTPException(status_code=422, detail=f"Competência inválida: {', '.join(invalidas)}. Use AAAAMM.")
    return resumo_organizacao(db, org, cnes_permitidos=cnes, competencias=lista, limite_meses=limite)
