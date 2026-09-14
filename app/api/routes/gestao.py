"""
Cadastro das organizações gestoras e dos hospitais de cada uma, pela tela.

Hospital estadual gerido por OSS aparece no CNES com o CNPJ da secretaria: o
vínculo vem da organização (site, transparência) e fica com fonte, situação e
data de verificação. Só administração da plataforma mexe.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import Acesso
from app.api.routes.dados import require_admin_plataforma
from app.api.routes.scan import _ultimos_scores
from app.db import get_db
from app.domain.resumo import _nomes
from app.models import Establishment, ManagementOrganization, OrganizationEstablishment, Prospect
from app.seed.organizacoes import SITUACOES, buscar_estabelecimentos

router = APIRouter(prefix="/api/revenue-scan", tags=["gestao"])

_CNES = re.compile(r"^\d{1,7}$")


class DadosOrganizacao(BaseModel):
    sigla: str | None = Field(default=None, min_length=2, max_length=30)
    nome: str | None = Field(default=None, min_length=2, max_length=255)
    cnpj: str | None = Field(default=None, max_length=18)
    uf: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    site: str | None = Field(default=None, max_length=255)
    fonte: str | None = Field(default=None, max_length=500)


class DadosUnidade(BaseModel):
    sigla: str | None = Field(default=None, max_length=20)
    situacao: str = Field(default="A_CONFIRMAR")
    fonte: str | None = Field(default=None, max_length=500)


def _org(db: Session, organization_id: int) -> ManagementOrganization:
    org = db.execute(select(ManagementOrganization).where(ManagementOrganization.id == organization_id)
                     .options(selectinload(ManagementOrganization.unidades))).scalar_one_or_none()
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organização não encontrada")
    return org


def _json(db: Session, org: ManagementOrganization) -> dict[str, Any]:
    cnes = [u.cnes for u in org.unidades]
    nomes = _nomes(db, cnes)
    estabelecimentos = {e.cnes: e for e in db.execute(select(Establishment).where(Establishment.cnes.in_(cnes))).scalars()} if cnes else {}
    scores = _ultimos_scores(db, cnes, None)
    prospect = db.execute(select(Prospect.id).where(Prospect.organization_id == org.id)).scalar_one_or_none()
    return {
        "id": org.id, "sigla": org.sigla, "nome": org.nome, "cnpj": org.cnpj, "uf": org.uf, "site": org.site,
        "fonte": org.fonte, "prospect_id": prospect,
        "unidades": [{
            "cnes": u.cnes, "sigla": u.sigla, "nome": nomes.get(u.cnes),
            "uf": estabelecimentos[u.cnes].uf if u.cnes in estabelecimentos else None,
            "situacao": u.situacao, "fonte": u.fonte,
            "verificado_em": u.verificado_em.isoformat() if u.verificado_em else None,
            "scan": {"score": scores[u.cnes].score, "impacto_confirmado": float(scores[u.cnes].impacto_confirmado or 0),
                     "periodo_fim": scores[u.cnes].periodo_fim} if u.cnes in scores else None,
        } for u in org.unidades],
    }


@router.post("/organizations", status_code=status.HTTP_201_CREATED)
def criar_organizacao(dados: DadosOrganizacao, _: Acesso = Depends(require_admin_plataforma),
                      db: Session = Depends(get_db)) -> dict[str, Any]:
    if not dados.sigla or not dados.nome:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Sigla e nome são obrigatórios.")
    sigla = dados.sigla.strip().upper()
    if db.execute(select(ManagementOrganization.id).where(ManagementOrganization.sigla == sigla)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Já existe a organização {sigla}.")
    org = ManagementOrganization(sigla=sigla, nome=dados.nome.strip(), cnpj=re.sub(r"\D", "", dados.cnpj or "") or None,
                                 uf=dados.uf.upper() if dados.uf else None, site=dados.site, fonte=dados.fonte)
    db.add(org)
    db.commit()
    return _json(db, _org(db, org.id))


@router.get("/organizations/{organization_id}/units")
def unidades(organization_id: int, _: Acesso = Depends(require_admin_plataforma),
             db: Session = Depends(get_db)) -> dict[str, Any]:
    return _json(db, _org(db, organization_id))


@router.patch("/organizations/{organization_id}")
def mudar_organizacao(organization_id: int, dados: DadosOrganizacao, _: Acesso = Depends(require_admin_plataforma),
                      db: Session = Depends(get_db)) -> dict[str, Any]:
    org = _org(db, organization_id)
    campos = dados.model_dump(exclude_unset=True)
    if campos.get("sigla"):
        sigla = campos["sigla"].strip().upper()
        outra = db.execute(select(ManagementOrganization.id).where(ManagementOrganization.sigla == sigla,
                                                                   ManagementOrganization.id != org.id)).first()
        if outra:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Já existe a organização {sigla}.")
        campos["sigla"] = sigla
    if "cnpj" in campos:
        campos["cnpj"] = re.sub(r"\D", "", campos["cnpj"] or "") or None
    if campos.get("uf"):
        campos["uf"] = campos["uf"].upper()
    for campo, valor in campos.items():
        setattr(org, campo, valor)
    db.commit()
    return _json(db, org)


@router.put("/organizations/{organization_id}/units/{cnes}")
def vincular_unidade(organization_id: int, cnes: str, dados: DadosUnidade, _: Acesso = Depends(require_admin_plataforma),
                     db: Session = Depends(get_db)) -> dict[str, Any]:
    if not _CNES.match(cnes):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="CNES deve ter até 7 dígitos.")
    situacao = dados.situacao.upper()
    if situacao not in SITUACOES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Situação: CONFIRMADO ou A_CONFIRMAR.")
    org = _org(db, organization_id)
    numero = cnes.zfill(7)
    unidade = next((u for u in org.unidades if u.cnes == numero), None)
    if unidade is None:
        unidade = OrganizationEstablishment(cnes=numero)
        org.unidades.append(unidade)
    unidade.sigla = dados.sigla or unidade.sigla
    unidade.fonte = dados.fonte or unidade.fonte
    unidade.situacao = situacao
    # Conferido agora: a data diz até quando o vínculo foi verificado.
    unidade.verificado_em = date.today() if situacao == "CONFIRMADO" else unidade.verificado_em
    db.commit()
    return _json(db, org)


@router.delete("/organizations/{organization_id}/units/{cnes}")
def desvincular_unidade(organization_id: int, cnes: str, _: Acesso = Depends(require_admin_plataforma),
                        db: Session = Depends(get_db)) -> dict[str, Any]:
    org = _org(db, organization_id)
    unidade = next((u for u in org.unidades if u.cnes == cnes.zfill(7)), None)
    if unidade is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Hospital não está nesta organização")
    org.unidades.remove(unidade)
    db.commit()
    return _json(db, org)


@router.get("/establishments")
def buscar(q: str = Query(..., min_length=3, max_length=100), uf: str | None = Query(default=None, pattern=r"^[A-Za-z]{2}$"),
           _: Acesso = Depends(require_admin_plataforma), db: Session = Depends(get_db)) -> dict[str, Any]:
    termo = q.strip()
    if _CNES.match(termo):
        e = db.get(Establishment, termo.zfill(7))
        achados = [{"cnes": e.cnes, "nome_fantasia": e.nome_fantasia, "razao_social": e.razao_social, "uf": e.uf}] if e else []
    else:
        achados = buscar_estabelecimentos(db, termo, uf, limite=30)
    return {"estabelecimentos": achados}
