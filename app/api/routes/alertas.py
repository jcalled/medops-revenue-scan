"""
Alertas mensais por organização: para quem mandar, o que incluir, a
pré-visualização, o "enviar agora" e o histórico. Da administração da plataforma:
o conteúdo é dado público, mas os e-mails são dos contatos da OSS.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Acesso
from app.api.routes.dados import require_admin_plataforma
from app.config import get_settings
from app.db import get_db
from app.domain.alertas import gerar_alertas, montar_alerta, renderizar
from app.models import AlertIssue, AlertSubscription, ManagementOrganization

router = APIRouter(prefix="/api/revenue-scan/alerts", tags=["alertas"])

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class DadosAssinatura(BaseModel):
    emails: list[str] = Field(default_factory=list, max_length=20)
    ativo: bool = True
    incluir_honorarios: bool = False
    percentual: float = Field(default=15, ge=0, le=100)


def _assinatura_json(a: AlertSubscription) -> dict[str, Any]:
    return {"organization_id": a.organization_id, "emails": a.emails, "ativo": a.ativo,
            "incluir_honorarios": a.incluir_honorarios, "percentual": float(a.percentual),
            "atualizado_em": a.atualizado_em.isoformat() if a.atualizado_em else None}


def _emissao_json(e: AlertIssue, siglas: dict[int, str]) -> dict[str, Any]:
    return {"id": e.id, "organization_id": e.organization_id, "sigla": siglas.get(e.organization_id),
            "competencia": e.competencia, "referencia": e.referencia, "status": e.status, "erro": e.erro,
            "destinatarios": e.destinatarios, "gerado_em": e.gerado_em.isoformat() if e.gerado_em else None,
            "resumo": {k: e.conteudo.get(k) for k in ("rejeitado_mes", "teria_pegado_mes", "vence_neste_mes", "recuperavel")}}


def _org(db: Session, organization_id: int) -> ManagementOrganization:
    org = db.get(ManagementOrganization, organization_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organização não encontrada.")
    return org


@router.get("")
def painel(_: Acesso = Depends(require_admin_plataforma), db: Session = Depends(get_db)) -> dict[str, Any]:
    siglas = dict(db.execute(select(ManagementOrganization.id, ManagementOrganization.sigla)).all())
    return {
        "email_configurado": get_settings().email_configurado,
        "assinaturas": [_assinatura_json(a) for a in db.execute(select(AlertSubscription)).scalars()],
        "envios": [_emissao_json(e, siglas) for e in db.execute(
            select(AlertIssue).order_by(AlertIssue.gerado_em.desc(), AlertIssue.id.desc()).limit(60)).scalars()],
    }


@router.put("/organizations/{organization_id}")
def configurar(organization_id: int, dados: DadosAssinatura, _: Acesso = Depends(require_admin_plataforma),
               db: Session = Depends(get_db)) -> dict[str, Any]:
    _org(db, organization_id)
    emails = list(dict.fromkeys(e.strip().lower() for e in dados.emails if e.strip()))
    invalidos = [e for e in emails if not _EMAIL.match(e)]
    if invalidos:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"E-mail inválido: {', '.join(invalidos)}")
    assinatura = db.execute(select(AlertSubscription).where(AlertSubscription.organization_id == organization_id)).scalar_one_or_none()
    if assinatura is None:
        assinatura = AlertSubscription(organization_id=organization_id)
        db.add(assinatura)
    assinatura.emails, assinatura.ativo = emails, dados.ativo
    assinatura.incluir_honorarios, assinatura.percentual = dados.incluir_honorarios, Decimal(str(dados.percentual))
    assinatura.atualizado_em = datetime.now(timezone.utc)
    db.commit()
    return _assinatura_json(assinatura)


@router.get("/organizations/{organization_id}/preview")
def previsualizar(organization_id: int, _: Acesso = Depends(require_admin_plataforma),
                  db: Session = Depends(get_db)) -> dict[str, Any]:
    org = _org(db, organization_id)
    assinatura = db.execute(select(AlertSubscription).where(AlertSubscription.organization_id == organization_id)).scalar_one_or_none()
    conteudo = montar_alerta(db, org, assinatura=assinatura)
    if conteudo is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Sem hospitais com dados carregados nesta organização.")
    assunto, html, _texto = renderizar(conteudo, get_settings().app_url)
    return {"assunto": assunto, "html": html, "conteudo": conteudo}


@router.post("/organizations/{organization_id}/send")
def enviar_agora(organization_id: int, _: Acesso = Depends(require_admin_plataforma),
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    _org(db, organization_id)
    if db.execute(select(AlertSubscription.id).where(AlertSubscription.organization_id == organization_id)).first() is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Configure os destinatários do alerta antes de enviar.")
    gerados = gerar_alertas(db, organizacao=organization_id, forcar=True)
    if not gerados:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Sem dados carregados para esta organização.")
    siglas = dict(db.execute(select(ManagementOrganization.id, ManagementOrganization.sigla)).all())
    return _emissao_json(gerados[0], siglas)
