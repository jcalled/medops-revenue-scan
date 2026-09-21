"""
Apresentação por link público, sem login, para mandar à OSS.

Gerar, listar e revogar é da administração da plataforma. Abrir é público, com o
token do link: o servidor guarda só o hash, e o link vence. O conteúdo é um
retrato dos números agregados tirado na hora de gerar — nenhuma AIH individual,
nenhum dado de hospital que não seja público — e os honorários só entram se
quem gerou escolheu mostrá-los.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Acesso
from app.api.routes.dados import require_admin_plataforma
from app.api.routes.explorar import Filtros, _titulo
from app.db import get_db
from app.domain.kit import referencia_padrao
from app.domain.recuperacao import meses_carregados
from app.domain.relatorio_recuperacao import montar_relatorio
from app.models import ManagementOrganization, OrganizationEstablishment, PublicPresentation

router = APIRouter(prefix="/api/revenue-scan", tags=["apresentacao-publica"])

# O que os slides usam de cada hospital: nada de AIH, nada de lista de motivos por AIH.
_CAMPOS_HOSPITAL = ("cnes", "nome", "total", "semelhantes", "prevenir", "cenarios")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _retrato(relatorio: dict[str, Any], mostrar_honorarios: bool) -> dict[str, Any]:
    retrato = {k: relatorio.get(k) for k in ("recorte", "meses", "referencia", "total", "cenarios", "antes_do_envio", "gerado_em",
                                             "por_mes", "baldes", "proximo_mes")}
    retrato["percentual"] = relatorio.get("percentual") if mostrar_honorarios else None
    retrato["hospitais"] = [{k: h.get(k) for k in _CAMPOS_HOSPITAL} for h in relatorio.get("hospitais", [])]
    if not mostrar_honorarios:
        for bloco in [retrato.get("cenarios") or {}, *[h.get("cenarios") or {} for h in retrato["hospitais"]]]:
            for cenario in bloco.values():
                if isinstance(cenario, dict):
                    cenario.pop("honorarios", None)
    return retrato


class PedidoLink(BaseModel):
    organizacao: int | None = None
    cnes: list[str] | None = None
    mostrar_honorarios: bool = False
    percentual: float = Field(default=15, ge=0, le=100)
    dias: int = Field(default=30, ge=1, le=180)
    # Período (AAAAMM); vazio: todos os processamentos carregados.
    de: str | None = Field(default=None, pattern=r"^\d{4}(0[1-9]|1[0-2])$")
    ate: str | None = Field(default=None, pattern=r"^\d{4}(0[1-9]|1[0-2])$")


def _json(p: PublicPresentation) -> dict[str, Any]:
    agora = datetime.now(timezone.utc)
    expira = p.expira_em if p.expira_em.tzinfo else p.expira_em.replace(tzinfo=timezone.utc)
    return {"id": p.id, "titulo": p.titulo, "organization_id": p.organization_id, "com_honorarios": p.percentual is not None,
            "criado_em": p.criado_em.isoformat() if p.criado_em else None, "expira_em": expira.isoformat(),
            "revogado_em": p.revogado_em.isoformat() if p.revogado_em else None,
            "ativo": p.revogado_em is None and expira > agora,
            "visualizacoes": p.visualizacoes, "ultima_visualizacao": p.ultima_visualizacao.isoformat() if p.ultima_visualizacao else None}


@router.post("/public-presentations", status_code=status.HTTP_201_CREATED)
def gerar(pedido: PedidoLink, acesso: Acesso = Depends(require_admin_plataforma), db: Session = Depends(get_db)) -> dict[str, Any]:
    cnes = sorted({c.strip().zfill(7) for c in pedido.cnes or [] if c.strip()})
    if pedido.organizacao:
        if db.get(ManagementOrganization, pedido.organizacao) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organização não encontrada.")
        cnes = sorted(set(cnes) | set(db.execute(select(OrganizationEstablishment.cnes)
                                                  .where(OrganizationEstablishment.organization_id == pedido.organizacao)).scalars()))
    if not cnes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Escolha uma organização ou hospitais.")
    meses = [m for m in meses_carregados(db, cnes)
             if (not pedido.de or m >= pedido.de) and (not pedido.ate or m <= pedido.ate)]
    if not meses:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Sem dados carregados para estes hospitais.")
    relatorio = montar_relatorio(db, cnes, meses, referencia_padrao(), percentual=pedido.percentual)
    titulo = _titulo(db, Filtros(organizacao=pedido.organizacao, cnes=None if pedido.organizacao else cnes))
    relatorio["recorte"] = {"titulo": titulo}
    token = secrets.token_urlsafe(32)
    link = PublicPresentation(
        token_hash=_hash(token), titulo=titulo, organization_id=pedido.organizacao, cnes=cnes,
        percentual=Decimal(str(pedido.percentual)) if pedido.mostrar_honorarios else None,
        retrato=_retrato(relatorio, pedido.mostrar_honorarios), criado_por=acesso.principal.user_id,
        expira_em=datetime.now(timezone.utc) + timedelta(days=pedido.dias))
    db.add(link)
    db.commit()
    # O token só sai aqui: depois, nem a lista o mostra.
    return {**_json(link), "token": token}


@router.get("/public-presentations")
def listar(_: Acesso = Depends(require_admin_plataforma), db: Session = Depends(get_db)) -> dict[str, Any]:
    links = db.execute(select(PublicPresentation).order_by(PublicPresentation.criado_em.desc(), PublicPresentation.id.desc()).limit(50)).scalars()
    return {"links": [_json(p) for p in links]}


@router.delete("/public-presentations/{link_id}")
def revogar(link_id: int, _: Acesso = Depends(require_admin_plataforma), db: Session = Depends(get_db)) -> dict[str, Any]:
    link = db.get(PublicPresentation, link_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Link não encontrado.")
    link.revogado_em = link.revogado_em or datetime.now(timezone.utc)
    db.commit()
    return _json(link)


@router.get("/public/presentations/{token}")
def abrir(token: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Público: o retrato da apresentação, se o link existe, não venceu e não foi revogado. Mesma resposta para os três."""
    link = db.execute(select(PublicPresentation).where(PublicPresentation.token_hash == _hash(token))).scalar_one_or_none()
    agora = datetime.now(timezone.utc)
    expira = None if link is None else (link.expira_em if link.expira_em.tzinfo else link.expira_em.replace(tzinfo=timezone.utc))
    if link is None or link.revogado_em is not None or expira <= agora:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Este link não existe mais: venceu ou foi desativado.")
    link.visualizacoes += 1
    link.ultima_visualizacao = agora
    db.commit()
    return {**link.retrato, "expira_em": expira.isoformat()}
