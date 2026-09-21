"""
Homologação: lotes de AIH rejeitadas de um mês para o faturamento do hospital
conferir o que o sistema diz, e a confiabilidade por regra que sai disso. Da
administração da plataforma: é dado público, e o parecer vem do hospital pela
planilha — sem login para ele.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import Acesso
from app.api.routes.dados import require_admin_plataforma
from app.db import get_db
from app.domain import homologacao
from app.domain.resumo import _nomes
from app.models import HomologationBatch, HomologationItem, ManagementOrganization, OrganizationEstablishment, SihRejection

router = APIRouter(prefix="/api/revenue-scan/homologation", tags=["homologacao"])


class PedidoLote(BaseModel):
    organizacao: int | None = None
    cnes: list[str] | None = None
    competencia: str | None = Field(default=None, pattern=r"^\d{4}(0[1-9]|1[0-2])$")
    titulo: str | None = Field(default=None, max_length=200)


class Parecer(BaseModel):
    veredito: str | None = None
    comentario: str | None = Field(default=None, max_length=2000)
    respondido_por: str | None = Field(default=None, max_length=120)


def _lote(db: Session, lote_id: int) -> HomologationBatch:
    lote = db.execute(select(HomologationBatch).where(HomologationBatch.id == lote_id)
                      .options(selectinload(HomologationBatch.itens))).scalar_one_or_none()
    if lote is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Lote não encontrado.")
    return lote


def _item_json(i: HomologationItem, nomes: dict[str, str]) -> dict[str, Any]:
    return {"id": i.id, "n_aih": i.n_aih, "cnes": i.cnes, "hospital": nomes.get(i.cnes), "valor": float(i.valor),
            "motivos": i.motivos, "origem": i.origem, "regras": i.regras, "o_que_diz": i.o_que_diz, "correcao": i.correcao,
            "veredito": i.veredito, "comentario": i.comentario, "respondido_por": i.respondido_por,
            "respondido_em": i.respondido_em.isoformat() if i.respondido_em else None}


def _lote_json(db: Session, lote: HomologationBatch, com_itens: bool = True) -> dict[str, Any]:
    corpo = {"id": lote.id, "titulo": lote.titulo, "organization_id": lote.organization_id, "cnes": lote.cnes,
             "competencia": lote.competencia, "criado_em": lote.criado_em.isoformat() if lote.criado_em else None,
             "confiabilidade": homologacao.confiabilidade(lote), "vereditos": homologacao.VEREDITOS}
    if com_itens:
        nomes = _nomes(db, lote.cnes)
        corpo["itens"] = [_item_json(i, nomes) for i in lote.itens]
    return corpo


@router.get("/batches")
def listar(_: Acesso = Depends(require_admin_plataforma), db: Session = Depends(get_db)) -> dict[str, Any]:
    lotes = db.execute(select(HomologationBatch).options(selectinload(HomologationBatch.itens))
                       .order_by(HomologationBatch.criado_em.desc(), HomologationBatch.id.desc()).limit(50)).scalars()
    return {"lotes": [_lote_json(db, l, com_itens=False) for l in lotes]}


@router.post("/batches", status_code=status.HTTP_201_CREATED)
def criar(pedido: PedidoLote, acesso: Acesso = Depends(require_admin_plataforma), db: Session = Depends(get_db)) -> dict[str, Any]:
    cnes = sorted({c.strip().zfill(7) for c in pedido.cnes or [] if c.strip()})
    titulo = pedido.titulo
    if pedido.organizacao:
        org = db.get(ManagementOrganization, pedido.organizacao)
        if org is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organização não encontrada.")
        cnes = sorted(set(cnes) | set(db.execute(select(OrganizationEstablishment.cnes)
                                                  .where(OrganizationEstablishment.organization_id == org.id)).scalars()))
        titulo = titulo or org.sigla
    if not cnes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Escolha uma organização ou hospitais.")
    competencia = pedido.competencia or db.execute(
        select(func.max(SihRejection.competencia)).where(SihRejection.cnes.in_(cnes))).scalar()
    if not competencia:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Sem AIH rejeitada carregada para estes hospitais.")
    lote = homologacao.montar_lote(db, cnes=cnes, competencia=competencia,
                                   titulo=f"{titulo or 'Hospitais selecionados'} · processamento {competencia[4:]}/{competencia[:4]}",
                                   organization_id=pedido.organizacao, criado_por=acesso.principal.user_id)
    if not lote.itens:
        db.delete(lote)
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Nenhuma AIH rejeitada neste processamento para estes hospitais.")
    return _lote_json(db, _lote(db, lote.id))


@router.get("/batches/{lote_id}")
def detalhe(lote_id: int, _: Acesso = Depends(require_admin_plataforma), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _lote_json(db, _lote(db, lote_id))


@router.get("/batches/{lote_id}/sheet")
def baixar_planilha(lote_id: int, _: Acesso = Depends(require_admin_plataforma), db: Session = Depends(get_db)) -> Response:
    lote = _lote(db, lote_id)
    return Response(homologacao.planilha(db, lote).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="homologacao-lote-{lote.id}.csv"'})


@router.post("/batches/{lote_id}/sheet")
async def importar_planilha(lote_id: int, arquivo: UploadFile = File(...), _: Acesso = Depends(require_admin_plataforma),
                            db: Session = Depends(get_db)) -> dict[str, Any]:
    lote = _lote(db, lote_id)
    conteudo = await arquivo.read(10 * 1024 * 1024 + 1)
    if len(conteudo) > 10 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Planilha maior que 10 MB.")
    try:
        resultado = homologacao.importar(db, lote, conteudo, arquivo.filename or "planilha.csv")
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {**resultado, "lote": _lote_json(db, _lote(db, lote_id))}


@router.put("/batches/{lote_id}/items/{item_id}")
def registrar_parecer(lote_id: int, item_id: int, parecer: Parecer, _: Acesso = Depends(require_admin_plataforma),
                      db: Session = Depends(get_db)) -> dict[str, Any]:
    item = db.get(HomologationItem, item_id)
    if item is None or item.batch_id != lote_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Item não encontrado neste lote.")
    if parecer.veredito is not None and parecer.veredito not in homologacao.VEREDITOS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Veredito: {', '.join(homologacao.VEREDITOS)}.")
    item.veredito = parecer.veredito
    item.comentario = parecer.comentario if parecer.comentario is not None else item.comentario
    item.respondido_por = parecer.respondido_por if parecer.respondido_por is not None else item.respondido_por
    item.respondido_em = datetime.now(timezone.utc) if parecer.veredito else None
    db.commit()
    lote = _lote(db, lote_id)
    return {"item": _item_json(item, _nomes(db, [item.cnes])), "confiabilidade": homologacao.confiabilidade(lote)}
