"""
CRM simples da prospecção de organizações. Só administração da plataforma: é o
funil comercial da MedOps, não aparece para tenant.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import Acesso
from app.api.routes.dados import require_admin_plataforma
from app.api.routes.scan import _ultimos_scores
from app.db import get_db
from app.domain import prospeccao as crm
from app.models import ManagementOrganization, OrganizationEstablishment, Prospect, ProspectEvent

router = APIRouter(prefix="/api/revenue-scan/crm", tags=["prospeccao"])

_TAMANHO_MAXIMO = 5 * 1024 * 1024


def _organizacao(db: Session, organization_id: int | None) -> dict[str, Any] | None:
    if organization_id is None:
        return None
    org = db.execute(select(ManagementOrganization).where(ManagementOrganization.id == organization_id)
                     .options(selectinload(ManagementOrganization.unidades))).scalar_one_or_none()
    if org is None:
        return None
    scores = _ultimos_scores(db, [u.cnes for u in org.unidades], None)
    periodos = [(s.periodo_inicio, s.periodo_fim) for s in scores.values()]
    return {
        "id": org.id, "sigla": org.sigla, "nome": org.nome,
        "hospitais": len(org.unidades),
        "confirmados": sum(1 for u in org.unidades if u.situacao == "CONFIRMADO"),
        "com_scan": len(scores),
        "oportunidade_confirmada": round(sum(float(s.impacto_confirmado or 0) for s in scores.values()), 2),
        "periodo": {"inicio": min(p[0] for p in periodos), "fim": max(p[1] for p in periodos)} if periodos else None,
    }


def _json(db: Session, p: Prospect, com_eventos: bool = False) -> dict[str, Any]:
    corpo = {
        "id": p.id, "nome": p.nome, "sigla_sugerida": crm.sigla_sugerida(p.nome), "uf": p.uf, "rank": p.rank,
        "score": p.score, "prioridade": p.prioridade, "presenca": p.presenca, "situacao_escopo": p.situacao_escopo,
        "hospitais_confirmados": p.hospitais_confirmados, "rede": p.rede, "principais_unidades": p.principais_unidades,
        "lideranca": p.lideranca, "contato_publico": p.contato_publico, "site": p.site, "fonte": p.fonte, "fit": p.fit,
        "confianca": p.confianca, "etapa": p.etapa, "etapa_nome": crm.ETAPAS.get(p.etapa, p.etapa),
        "contato_nome": p.contato_nome, "contato_cargo": p.contato_cargo, "contato_email": p.contato_email,
        "contato_telefone": p.contato_telefone, "responsavel": p.responsavel, "proxima_acao": p.proxima_acao,
        "proxima_acao_em": p.proxima_acao_em.isoformat() if p.proxima_acao_em else None,
        "proposta_percentual": float(p.proposta_percentual) if p.proposta_percentual is not None else None,
        "proposta_fixo": float(p.proposta_fixo) if p.proposta_fixo is not None else None,
        "motivo_perda": p.motivo_perda,
        "organizacao": _organizacao(db, p.organization_id),
        "atualizado_em": p.atualizado_em.isoformat() if p.atualizado_em else None,
    }
    if com_eventos:
        # Mais recente primeiro; ordena aqui porque o evento recém-criado ainda não passou pelo order_by.
        eventos = sorted(p.eventos, key=lambda e: e.id or 0, reverse=True)
        corpo["eventos"] = [{"id": e.id, "tipo": e.tipo, "texto": e.texto, "criado_por": e.criado_por,
                             "criado_em": e.criado_em.isoformat() if e.criado_em else None} for e in eventos]
    return corpo


def _prospect(db: Session, prospect_id: int) -> Prospect:
    p = db.execute(select(Prospect).where(Prospect.id == prospect_id)
                   .options(selectinload(Prospect.eventos))).scalar_one_or_none()
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organização em prospecção não encontrada")
    return p


class Campos(BaseModel):
    nome: str | None = Field(default=None, min_length=2, max_length=255)
    uf: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    prioridade: str | None = Field(default=None, max_length=4)
    presenca: str | None = Field(default=None, max_length=255)
    principais_unidades: str | None = None
    site: str | None = Field(default=None, max_length=255)
    fonte: str | None = Field(default=None, max_length=500)
    etapa: str | None = None
    contato_nome: str | None = Field(default=None, max_length=120)
    contato_cargo: str | None = Field(default=None, max_length=120)
    contato_email: str | None = Field(default=None, max_length=255)
    contato_telefone: str | None = Field(default=None, max_length=60)
    responsavel: str | None = Field(default=None, max_length=120)
    proxima_acao: str | None = None
    proxima_acao_em: date | None = None
    proposta_percentual: float | None = Field(default=None, ge=0, le=100)
    proposta_fixo: float | None = Field(default=None, ge=0)
    motivo_perda: str | None = None
    organization_id: int | None = None


def _aplicar(db: Session, p: Prospect, campos: Campos, usuario: int | None) -> None:
    dados = campos.model_dump(exclude_unset=True)
    if "etapa" in dados and dados["etapa"] not in crm.ETAPAS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Etapa desconhecida. Use {', '.join(crm.ETAPAS)}.")
    if dados.get("organization_id") is not None and db.get(ManagementOrganization, dados["organization_id"]) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Organização não encontrada.")
    if "etapa" in dados and dados["etapa"] != p.etapa:
        p.eventos.append(ProspectEvent(
            tipo="ETAPA", criado_por=usuario,
            texto=f"{crm.ETAPAS.get(p.etapa, p.etapa)} → {crm.ETAPAS[dados['etapa']]}"
                  + (f". Motivo: {dados['motivo_perda']}" if dados["etapa"] == "PERDIDA" and dados.get("motivo_perda") else ""),
        ))
    proposta = {k: dados[k] for k in ("proposta_percentual", "proposta_fixo") if k in dados}
    atual = {k: (float(getattr(p, k)) if getattr(p, k) is not None else None) for k in proposta}
    # Só registra proposta quando o valor muda: salvar o formulário de novo não é proposta nova.
    if any(proposta[k] is not None and proposta[k] != atual[k] for k in proposta):
        pct = proposta.get("proposta_percentual", p.proposta_percentual)
        fixo = proposta.get("proposta_fixo", p.proposta_fixo)
        p.eventos.append(ProspectEvent(tipo="PROPOSTA", criado_por=usuario,
                                       texto=f"Proposta: {pct if pct is not None else '—'}% + R$ {fixo if fixo is not None else '—'} por hospital."))
    for campo, valor in dados.items():
        if campo == "uf" and valor:
            valor = valor.upper()
        setattr(p, campo, valor)


@router.get("/prospects")
def listar(
    etapa: str | None = Query(default=None),
    uf: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=100),
    _: Acesso = Depends(require_admin_plataforma),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    consulta = select(Prospect).order_by(Prospect.rank.is_(None), Prospect.rank, Prospect.nome)
    if etapa:
        consulta = consulta.where(Prospect.etapa == etapa)
    if uf:
        consulta = consulta.where(Prospect.uf == uf.upper())
    termo = crm.normalizar(q)
    prospects = [p for p in db.execute(consulta).scalars() if not termo or termo in crm.normalizar(p.nome)]
    return {"etapas": crm.ETAPAS, "por_etapa": crm.contar_por_etapa(db),
            "prospects": [_json(db, p) for p in prospects]}


@router.post("/prospects", status_code=status.HTTP_201_CREATED)
def criar(campos: Campos, acesso: Acesso = Depends(require_admin_plataforma), db: Session = Depends(get_db)) -> dict[str, Any]:
    if not campos.nome:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Informe o nome da organização.")
    if db.execute(select(Prospect.id).where(Prospect.nome == campos.nome.strip())).first():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Essa organização já está na prospecção.")
    p = Prospect(nome=campos.nome.strip(), etapa="MAPEADA")
    db.add(p)
    _aplicar(db, p, campos.model_copy(update={"nome": campos.nome.strip()}), acesso.principal.user_id)
    p.eventos.append(ProspectEvent(tipo="NOTA", texto="Cadastrada manualmente.", criado_por=acesso.principal.user_id))
    db.commit()
    return _json(db, p, com_eventos=True)


@router.post("/prospects/import")
async def importar_planilha(arquivo: UploadFile = File(...), acesso: Acesso = Depends(require_admin_plataforma),
                            db: Session = Depends(get_db)) -> dict[str, Any]:
    conteudo = await arquivo.read(_TAMANHO_MAXIMO + 1)
    if len(conteudo) > _TAMANHO_MAXIMO:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Planilha maior que 5 MB.")
    try:
        registros = crm.ler_planilha(conteudo)
    except crm.PlanilhaInvalida as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return crm.importar(db, registros, acesso.principal.user_id)


@router.get("/prospects/{prospect_id}")
def detalhe(prospect_id: int, _: Acesso = Depends(require_admin_plataforma), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _json(db, _prospect(db, prospect_id), com_eventos=True)


@router.patch("/prospects/{prospect_id}")
def mudar(prospect_id: int, campos: Campos, acesso: Acesso = Depends(require_admin_plataforma),
          db: Session = Depends(get_db)) -> dict[str, Any]:
    p = _prospect(db, prospect_id)
    _aplicar(db, p, campos, acesso.principal.user_id)
    db.commit()
    return _json(db, p, com_eventos=True)


class Nota(BaseModel):
    texto: str = Field(min_length=1, max_length=5000)


@router.post("/prospects/{prospect_id}/notes", status_code=status.HTTP_201_CREATED)
def anotar(prospect_id: int, nota: Nota, acesso: Acesso = Depends(require_admin_plataforma),
           db: Session = Depends(get_db)) -> dict[str, Any]:
    p = _prospect(db, prospect_id)
    p.eventos.append(ProspectEvent(tipo="NOTA", texto=nota.texto.strip(), criado_por=acesso.principal.user_id))
    db.commit()
    return _json(db, p, com_eventos=True)


class NovaOrganizacao(BaseModel):
    sigla: str | None = Field(default=None, min_length=2, max_length=30)


@router.post("/prospects/{prospect_id}/organization", status_code=status.HTTP_201_CREATED)
def criar_organizacao(prospect_id: int, pedido: NovaOrganizacao, acesso: Acesso = Depends(require_admin_plataforma),
                      db: Session = Depends(get_db)) -> dict[str, Any]:
    """Cria a organização gestora a partir da prospecção, para cadastrar os hospitais e rodar o scan."""
    p = _prospect(db, prospect_id)
    if p.organization_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Já tem organização vinculada.")
    sigla = (pedido.sigla or crm.sigla_sugerida(p.nome) or "").strip().upper()
    if not sigla:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Informe a sigla da organização.")
    org = db.execute(select(ManagementOrganization).where(ManagementOrganization.sigla == sigla)).scalar_one_or_none()
    if org is None:
        org = ManagementOrganization(sigla=sigla, nome=p.nome, uf=p.uf, site=p.site, fonte=p.fonte)
        db.add(org)
        db.flush()
    p.organization_id = org.id
    p.eventos.append(ProspectEvent(tipo="NOTA", texto=f"Organização {sigla} vinculada.", criado_por=acesso.principal.user_id))
    db.commit()
    return _json(db, p, com_eventos=True)


@router.get("/prospects/{prospect_id}/suggestions")
def sugestoes(prospect_id: int, _: Acesso = Depends(require_admin_plataforma), db: Session = Depends(get_db)) -> dict[str, Any]:
    p = _prospect(db, prospect_id)
    vinculados: set[str] = set()
    if p.organization_id is not None:
        vinculados = set(db.execute(select(OrganizationEstablishment.cnes)
                                    .where(OrganizationEstablishment.organization_id == p.organization_id)).scalars())
    return {"ufs": crm.ufs_do_prospect(p), "sugestoes": crm.sugerir_unidades(db, p, vinculados)}
