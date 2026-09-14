"""
Acompanhamento da recuperação e fatura do mês. A regra está em app/domain/recuperacao.py.

Quem vê: a administração da plataforma vê todos; o tenant vê os dele, dentro do
escopo do contrato. Quem abre, confere e muda condições: só a administração.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import Acesso, require_revenue_scan
from app.api.routes.dados import require_admin_plataforma
from app.api.routes.organizacoes import _escopo
from app.db import get_db
from app.domain import recuperacao as rec
from app.domain.resumo import _nomes
from app.models import ManagementOrganization, RecoveryTracking

router = APIRouter(prefix="/api/revenue-scan/recovery", tags=["recuperacao"])

_CNES = re.compile(r"^\d{1,7}$")


def _alcanca(acesso: Acesso, t: RecoveryTracking) -> bool:
    if acesso.principal.platform_admin:
        return True
    _, cnes_permitidos, _ = _escopo(acesso)
    return t.tenant_id == acesso.principal.tenant_id and (cnes_permitidos is None or set(t.cnes) <= cnes_permitidos)


def _acompanhamento(db: Session, acesso: Acesso, tracking_id: int) -> RecoveryTracking:
    t = db.execute(
        select(RecoveryTracking).where(RecoveryTracking.id == tracking_id).options(selectinload(RecoveryTracking.itens))
    ).scalar_one_or_none()
    if t is None or not _alcanca(acesso, t):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Acompanhamento não encontrado")
    return t


def _json(db: Session, t: RecoveryTracking, com_itens: bool = False) -> dict[str, Any]:
    nomes = _nomes(db, t.cnes)
    ultimo = rec.ultimo_mes(db, t.cnes)
    org = db.get(ManagementOrganization, t.organization_id) if t.organization_id else None
    corpo = {
        "id": t.id,
        "nome": t.nome,
        "tenant_id": t.tenant_id,
        "organizacao": {"id": org.id, "sigla": org.sigla, "nome": org.nome} if org else None,
        "cnes": list(t.cnes),
        "hospitais": [{"cnes": c, "nome": nomes.get(c)} for c in t.cnes],
        "inicio": t.inicio,
        "percentual": float(t.percentual),
        "fixo_por_hospital": float(t.fixo_por_hospital),
        "status": t.status,
        "criado_em": t.criado_em.isoformat() if t.criado_em else None,
        "conferido_em": t.conferido_em.isoformat() if t.conferido_em else None,
        "ultimo_mes_carregado": ultimo,
        "linha_de_base": {
            "por_hospital": {c: float((t.linha_de_base or {}).get(c, 0)) for c in t.cnes},
            "mensal": round(sum(float(v) for v in (t.linha_de_base or {}).values()), 2),
            "meses": list(t.linha_de_base_meses or []),
            "origem": t.linha_de_base_origem,
        },
        "resumo": rec.resumo(t, ultimo),
    }
    if com_itens:
        proximo = rec.mais_meses(ultimo, 1) if ultimo else None
        descricoes = rec.descricoes_de_motivos(db)
        # Fila de trabalho: em aberto que vencem no próximo processamento, depois as demais pelo prazo e
        # pelo valor, as de prazo vencido por último; em seguida as recuperadas, das mais recentes.
        ordem_prazo = {"VENCENDO": 0, None: 1, "VENCIDO": 2}
        abertas = sorted((i for i in t.itens if i.situacao == rec.ABERTA), key=lambda i: (
            ordem_prazo[rec.situacao_do_prazo(i, proximo)], rec.prazo_estimado(i.dt_saida) or "999999",
            -float(i.valor_rejeitado or 0)))
        recuperadas = sorted((i for i in t.itens if i.situacao != rec.ABERTA),
                             key=lambda i: (i.competencia_aprovacao or "", float(i.valor_rejeitado or 0)), reverse=True)
        corpo["itens"] = [rec.item_json(i, nomes, descricoes, proximo) for i in abertas + recuperadas]
    return corpo


class NovoAcompanhamento(BaseModel):
    nome: str = Field(min_length=2, max_length=120)
    organization_id: int | None = None
    cnes: list[str] | None = None
    inicio: str = Field(pattern=r"^\d{6}$")
    percentual: float = Field(default=15, ge=0, le=100)
    fixo_por_hospital: float = Field(default=6900, ge=0)
    tenant_id: int | None = None


class Condicoes(BaseModel):
    nome: str | None = Field(default=None, min_length=2, max_length=120)
    percentual: float | None = Field(default=None, ge=0, le=100)
    fixo_por_hospital: float | None = Field(default=None, ge=0)
    status: str | None = Field(default=None, pattern=r"^(ATIVO|ENCERRADO)$")
    # Linha de base negociada com a organização, por CNES (R$ por mês).
    linha_de_base: dict[str, float] | None = None


@router.get("")
def listar(acesso: Acesso = Depends(require_revenue_scan), db: Session = Depends(get_db)) -> dict[str, Any]:
    consulta = select(RecoveryTracking).options(selectinload(RecoveryTracking.itens)).order_by(RecoveryTracking.id.desc())
    if not acesso.principal.platform_admin:
        consulta = consulta.where(RecoveryTracking.tenant_id == acesso.principal.tenant_id)
    return {"acompanhamentos": [_json(db, t) for t in db.execute(consulta).scalars() if _alcanca(acesso, t)]}


@router.post("", status_code=status.HTTP_201_CREATED)
def abrir(pedido: NovoAcompanhamento, acesso: Acesso = Depends(require_admin_plataforma),
          db: Session = Depends(get_db)) -> dict[str, Any]:
    cnes: list[str] = []
    if pedido.cnes:
        if not all(_CNES.match(c.strip()) for c in pedido.cnes):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="CNES deve ter até 7 dígitos.")
        cnes = [c.strip().zfill(7) for c in pedido.cnes]
    if pedido.organization_id is not None:
        org = db.execute(select(ManagementOrganization).where(ManagementOrganization.id == pedido.organization_id)
                         .options(selectinload(ManagementOrganization.unidades))).scalar_one_or_none()
        if org is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Organização não encontrada.")
        cnes = cnes or [u.cnes for u in org.unidades]
    cnes = list(dict.fromkeys(cnes))
    if not cnes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Informe os hospitais ou a organização.")

    t = RecoveryTracking(
        nome=pedido.nome.strip(), organization_id=pedido.organization_id, cnes=cnes, inicio=pedido.inicio,
        percentual=pedido.percentual, fixo_por_hospital=pedido.fixo_por_hospital, tenant_id=pedido.tenant_id,
        status=rec.ATIVO, criado_por=acesso.principal.user_id,
    )
    t.linha_de_base, t.linha_de_base_meses = rec.calcular_linha_de_base(db, cnes, pedido.inicio)
    t.linha_de_base_origem = "CALCULADA"
    db.add(t)
    db.flush()
    rec.conferir(db, t)
    db.commit()
    return _json(db, t)


@router.get("/{tracking_id}")
def detalhe(tracking_id: int, acesso: Acesso = Depends(require_revenue_scan),
            db: Session = Depends(get_db)) -> dict[str, Any]:
    return _json(db, _acompanhamento(db, acesso, tracking_id), com_itens=True)


@router.post("/{tracking_id}/check")
def conferir_agora(tracking_id: int, acesso: Acesso = Depends(require_admin_plataforma),
                   db: Session = Depends(get_db)) -> dict[str, Any]:
    t = _acompanhamento(db, acesso, tracking_id)
    if t.status != rec.ATIVO:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Acompanhamento encerrado: reative para conferir.")
    resultado = rec.conferir(db, t)
    db.commit()
    return resultado


@router.patch("/{tracking_id}")
def mudar_condicoes(tracking_id: int, pedido: Condicoes, acesso: Acesso = Depends(require_admin_plataforma),
                    db: Session = Depends(get_db)) -> dict[str, Any]:
    t = _acompanhamento(db, acesso, tracking_id)
    campos = pedido.model_dump(exclude_none=True)
    linha = campos.pop("linha_de_base", None)
    if linha is not None:
        fora = sorted(set(linha) - set(t.cnes))
        if fora or any(v < 0 for v in linha.values()):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f"Linha de base só para os hospitais do acompanhamento e sem valor negativo{': ' + ', '.join(fora) if fora else ''}.")
        t.linha_de_base = {c: round(float(linha.get(c, (t.linha_de_base or {}).get(c, 0))), 2) for c in t.cnes}
        t.linha_de_base_origem = "NEGOCIADA"
    for campo, valor in campos.items():
        setattr(t, campo, valor)
    db.commit()
    return _json(db, t)


@router.post("/{tracking_id}/baseline")
def recalcular_linha_de_base(tracking_id: int, acesso: Acesso = Depends(require_admin_plataforma),
                             db: Session = Depends(get_db)) -> dict[str, Any]:
    """Volta a linha de base para a calculada pelos meses carregados antes do início."""
    t = _acompanhamento(db, acesso, tracking_id)
    t.linha_de_base, t.linha_de_base_meses = rec.calcular_linha_de_base(db, t.cnes, t.inicio)
    t.linha_de_base_origem = "CALCULADA"
    db.commit()
    return _json(db, t)


@router.get("/{tracking_id}/invoice")
def fatura_do_mes(
    tracking_id: int,
    competencia: str = Query(..., pattern=r"^\d{6}$", description="Mês de processamento AAAAMM"),
    acesso: Acesso = Depends(require_revenue_scan),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return rec.fatura(db, _acompanhamento(db, acesso, tracking_id), competencia)
