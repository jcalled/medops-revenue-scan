"""
Kits por motivo de rejeição, a situação do trabalho de cada AIH e o FaturaSUS
por motivo. A regra está em app/domain/kits_motivo.py.

Quem vê os kits: todo cliente do Revenue Scan — é o manual de correção. Quem
escreve kit: só a administração da plataforma. Situação da AIH: quem alcança o
hospital pelo contrato.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Acesso, require_revenue_scan
from app.api.routes.dados import require_admin_plataforma
from app.api.routes.scan import _hospital_no_escopo
from app.db import get_db
from app.domain import kits_motivo as km
from app.models import AihTreatment, MotiveKit, SihErrorCode, SihRejection

router = APIRouter(prefix="/api/revenue-scan", tags=["kits-motivo"])

_CODIGO = re.compile(r"^\d{6}$")


def _padrao(opcoes) -> str:
    return "^(" + "|".join(opcoes) + ")$"


class KitEntrada(BaseModel):
    titulo: str = Field(min_length=3, max_length=160)
    significado: str = Field(min_length=3, max_length=4000)
    classe: str = Field(pattern=_padrao(km.DIFICULDADE))
    onde_corrigir: str = Field(pattern=_padrao(km.ONDE))
    passos: list[str] = Field(default_factory=list, max_length=30)
    dados_do_hospital: list[str] = Field(default_factory=list, max_length=30)
    evidencias: list[str] = Field(default_factory=list, max_length=30)
    prevencao: str | None = Field(default=None, max_length=2000)
    fonte: str | None = Field(default=None, max_length=2000)
    revisao: str = Field(default="A_CONFIRMAR", pattern=_padrao(km.REVISOES))


class TratativaEntrada(BaseModel):
    situacao: str = Field(max_length=16)
    competencia_reapresentacao: str | None = Field(default=None, max_length=6)
    justificativa: str | None = Field(default=None, max_length=2000)
    responsavel: str | None = Field(default=None, max_length=120)
    faturasus: str | None = Field(default=None, max_length=12)


@router.get("/motive-kits")
def catalogo(
    uf: str | None = Query(default=None, pattern=r"^[A-Za-z]{2}$", description="Só os dados carregados desta UF"),
    acesso: Acesso = Depends(require_revenue_scan),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return {
        **km.catalogo(db, [uf.upper()] if uf else None),
        "classes": list(km.DIFICULDADE), "onde": km.ONDE, "revisoes": km.REVISOES, "situacoes": km.SITUACOES,
        "faturasus": km.FATURASUS, "resultados": km.RESULTADOS,
    }


@router.get("/motive-kits/{codigo}")
def kit_do_motivo(
    codigo: str, acesso: Acesso = Depends(require_revenue_scan), db: Session = Depends(get_db),
) -> dict[str, Any]:
    kit = db.get(MotiveKit, codigo)
    if kit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Este motivo ainda não tem kit.")
    oficial = db.get(SihErrorCode, codigo)
    return km.kit_json(kit, km.uso_por_motivo(db).get(codigo), oficial.descricao if oficial else None,
                       km.estatisticas_prevencao(db).get(codigo), km.confiabilidade_faturasus(db).get(codigo))


@router.put("/motive-kits/{codigo}")
def salvar_kit(
    codigo: str,
    entrada: KitEntrada,
    _: Any = Depends(require_admin_plataforma),
    acesso: Acesso = Depends(require_revenue_scan),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not _CODIGO.match(codigo):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="O código do motivo tem seis dígitos.")
    return km.kit_json(km.salvar_kit(db, codigo, entrada.model_dump(), acesso.principal.user_id))


def _cnes_da_aih(db: Session, acesso: Acesso, n_aih: str) -> str:
    cnes = db.execute(
        select(SihRejection.cnes).where(SihRejection.n_aih == n_aih).order_by(SihRejection.competencia.desc()).limit(1)
    ).scalar_one_or_none()
    if cnes is None or not (acesso.principal.platform_admin or _hospital_no_escopo(db, acesso, cnes)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="AIH rejeitada não encontrada no escopo do contrato.")
    return cnes


def _situacao(db: Session, n_aih: str, cnes: str) -> dict[str, Any]:
    t = db.get(AihTreatment, n_aih)
    resultado = km.resultados(db, [t]).get(n_aih) if t else None
    return {"n_aih": n_aih, "cnes": cnes, "tratativa": km.tratativa_json(t, resultado),
            "historico": km.historico(db, n_aih), "situacoes": km.SITUACOES, "faturasus": km.FATURASUS}


@router.get("/aih/{n_aih}/treatment")
def ver_situacao(
    n_aih: str = Path(max_length=13), acesso: Acesso = Depends(require_revenue_scan), db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _situacao(db, n_aih, _cnes_da_aih(db, acesso, n_aih))


@router.put("/aih/{n_aih}/treatment")
def registrar_situacao(
    entrada: TratativaEntrada,
    n_aih: str = Path(max_length=13),
    acesso: Acesso = Depends(require_revenue_scan),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    cnes = _cnes_da_aih(db, acesso, n_aih)
    try:
        km.registrar_tratativa(db, n_aih, cnes, **entrada.model_dump(), usuario=acesso.principal.user_id,
                               tenant_id=acesso.principal.tenant_id)
    except km.TratativaInvalida as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _situacao(db, n_aih, cnes)
