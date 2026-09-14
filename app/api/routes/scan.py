"""
Scan do hospital e ranking da organização, a partir do que o motor calculou.

As rotas não recalculam: leem o último período calculado dentro do contrato.
Hospital ou organização fora do escopo responde 404, como se não existisse.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import Acesso, require_revenue_scan
from app.api.routes.organizacoes import _escopo, organizacao_no_escopo
from app.db import get_db
from app.domain.resumo import _lotes, _nomes, resumo
from app.engine.categorias import POR_CODIGO
from app.engine.motor import CONFIRMADA, ROTULOS_TIPO
from app.models import (
    Establishment, HospitalScore, ManagementOrganization, Opportunity, OrganizationEstablishment, PeerGroup,
    SihErrorCode,
)

router = APIRouter(prefix="/api/revenue-scan", tags=["scan"])

_CNES = re.compile(r"^\d{1,7}$")
RESSALVA = (
    "Dados públicos do DATASUS (SIH/SUS e CNES). A rejeição registrada pelo SUS é fato; o impacto é oportunidade "
    "financeira estimada — a parte acima do padrão de hospitais semelhantes — até a validação com os dados do "
    "hospital."
)
INDICADORES = {
    "taxa_rejeicao_valor": ("Rejeição sobre o valor apresentado", "%"),
    "perda_liquida_pct": ("Perda líquida sobre o valor apresentado", "%"),
    "ticket_medio": ("Valor médio por AIH aprovada", "R$"),
    "permanencia_media": ("Permanência média", "dias"),
    "alta_complexidade": ("AIH de alta complexidade", "%"),
    "ocupacao": ("Ocupação dos leitos SUS (sem UTI)", "%"),
    "aih_mes": ("AIH aprovadas por mês", "AIH"),
}


def _meses_no_periodo(inicio: str, fim: str) -> int:
    return (int(fim[:4]) * 12 + int(fim[4:])) - (int(inicio[:4]) * 12 + int(inicio[4:])) + 1


def _competencias(inicio: str, fim: str) -> list[str]:
    ano, mes = int(inicio[:4]), int(inicio[4:])
    saida = []
    while f"{ano:04d}{mes:02d}" <= fim:
        saida.append(f"{ano:04d}{mes:02d}")
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return saida


def _hospital_no_escopo(db: Session, acesso: Acesso, cnes: str) -> bool:
    siglas, cnes_permitidos, _ = _escopo(acesso)
    if cnes_permitidos is not None and cnes not in cnes_permitidos:
        return False
    if siglas is None:
        return True
    organizacoes = db.execute(
        select(ManagementOrganization.sigla).join(OrganizationEstablishment)
        .where(OrganizationEstablishment.cnes == cnes)
    ).scalars()
    return any(s.upper() in siglas for s in organizacoes)


def _ultimos_scores(db: Session, cnes: list[str], limite_meses: int | None) -> dict[str, HospitalScore]:
    saida: dict[str, HospitalScore] = {}
    for lote in _lotes(cnes):
        for score in db.execute(
            select(HospitalScore).where(HospitalScore.cnes.in_(lote))
            .order_by(HospitalScore.periodo_fim, HospitalScore.gerado_em)
        ).scalars():
            if limite_meses and _meses_no_periodo(score.periodo_inicio, score.periodo_fim) > limite_meses:
                continue
            saida[score.cnes] = score
    return saida


def _impactos(db: Session, scores: dict[str, HospitalScore]) -> dict[str, dict[str, float]]:
    """
    Impacto de cada hospital separado em confirmado e sinal.

    Somar rejeição registrada pelo SUS com sinal estimado num número só faria o
    total parecer mais certo do que é. No Ceará, metade do impacto do ISGH era
    sinal (permanência, valor médio).
    """
    saida: dict[str, dict[str, float]] = {}
    for cnes, score in scores.items():
        saida[cnes] = {"confirmado": 0.0, "sinais": 0.0}
        for status, total in db.execute(
            select(Opportunity.status, func.sum(Opportunity.estimated_financial_impact))
            .filter_by(cnes=cnes, periodo_inicio=score.periodo_inicio, periodo_fim=score.periodo_fim)
            .group_by(Opportunity.status)
        ):
            saida[cnes]["confirmado" if status == CONFIRMADA else "sinais"] += float(total or 0)
    return saida


def _principal(tipo: str | None, categoria: str | None) -> str | None:
    if categoria and categoria in POR_CODIGO:
        return POR_CODIGO[categoria].nome
    return ROTULOS_TIPO.get(tipo or "", tipo)


def _oportunidade(o: Opportunity, descricoes: dict[str, str] | None = None) -> dict[str, Any]:
    evidencia = dict(o.evidence or {})
    if evidencia.get("motivos"):
        # Código sozinho ("060082") não diz nada a quem lê o scan.
        evidencia["motivos"] = [{**m, "descricao": (descricoes or {}).get(m.get("codigo"))} for m in evidencia["motivos"]]
    return {
        "id": o.id, "opportunity_type": o.opportunity_type, "categoria": o.categoria or None,
        "titulo": (o.evidence or {}).get("titulo") or _principal(o.opportunity_type, o.categoria),
        "status": o.status, "unidade": o.unidade, "observed_value": o.observed_value,
        "benchmark_value": o.benchmark_value, "gap": o.gap,
        "estimated_financial_impact": float(o.estimated_financial_impact) if o.estimated_financial_impact is not None
        else None,
        "confidence_score": o.confidence_score, "evidence": evidencia, "recommended_action": o.recommended_action,
    }


@router.get("/hospitals/{cnes}/scan")
def scan_do_hospital(cnes: str, acesso: Acesso = Depends(require_revenue_scan),
                     db: Session = Depends(get_db)) -> dict[str, Any]:
    if not _CNES.match(cnes):
        raise HTTPException(status_code=404, detail="Hospital não encontrado")
    numero = cnes.zfill(7)
    if not _hospital_no_escopo(db, acesso, numero):
        raise HTTPException(status_code=404, detail="Hospital não encontrado")
    _, _, limite = _escopo(acesso)
    score = _ultimos_scores(db, [numero], limite).get(numero)
    if score is None:
        raise HTTPException(status_code=404, detail="Hospital sem scan calculado no período do contrato")

    periodo = {"periodo_inicio": score.periodo_inicio, "periodo_fim": score.periodo_fim}
    grupo = db.execute(
        select(PeerGroup).filter_by(cnes=numero, **periodo)
        .options(selectinload(PeerGroup.membros), selectinload(PeerGroup.metricas))
    ).scalar_one_or_none()
    oportunidades = db.execute(select(Opportunity).filter_by(cnes=numero, **periodo)).scalars().all()
    oportunidades = sorted(oportunidades, key=lambda o: -float(o.estimated_financial_impact or 0))
    membros = grupo.membros if grupo else []
    nomes = _nomes(db, [numero, *(m.cnes for m in membros)])
    estabelecimento = db.get(Establishment, numero)
    organizacoes = db.execute(
        select(ManagementOrganization.sigla, OrganizationEstablishment.sigla).join(OrganizationEstablishment)
        .where(OrganizationEstablishment.cnes == numero)
    ).all()
    codigos = {m.get("codigo") for o in oportunidades for m in (o.evidence or {}).get("motivos", [])}
    descricoes = dict(db.execute(
        select(SihErrorCode.codigo, SihErrorCode.descricao).where(SihErrorCode.codigo.in_(codigos or {"-"}))
    ).all())
    criterio = grupo.criterio if grupo else {}
    atributos = criterio.get("atributos", {})
    metricas = {m.metrica: m for m in (grupo.metricas if grupo else [])}

    return {
        "hospital": {
            "cnes": numero,
            "nome": nomes.get(numero),
            "uf": (estabelecimento.uf if estabelecimento else None) or atributos.get("uf"),
            "codigo_municipio": estabelecimento.codigo_municipio if estabelecimento else None,
            "natureza": atributos.get("natureza"),
            "porte": atributos.get("porte"),
            "leitos_sus": atributos.get("leitos_sus"),
            "leitos_uti_sus": atributos.get("leitos_uti_sus"),
            "habilitacoes": atributos.get("habilitacoes"),
            "organizacoes": [{"sigla": org, "sigla_unidade": unidade} for org, unidade in organizacoes],
        },
        "periodo": {"inicio": score.periodo_inicio, "fim": score.periodo_fim},
        "score": score.score,
        "impacto_estimado": float(score.impacto_estimado),
        "impacto_confirmado": round(sum(float(o.estimated_financial_impact or 0)
                                        for o in oportunidades if o.status == CONFIRMADA), 2),
        "impacto_sinais": round(sum(float(o.estimated_financial_impact or 0)
                                    for o in oportunidades if o.status != CONFIRMADA), 2),
        "valor_apresentado": float(score.valor_apresentado),
        "principal_problema": _principal(score.principal_tipo, score.principal_categoria),
        "semelhantes": {
            "criterio": criterio.get("filtro"),
            "insuficientes": criterio.get("pares_insuficientes", False),
            "hospitais": [{"cnes": m.cnes, "nome": nomes.get(m.cnes), "distancia": m.distancia} for m in membros],
        },
        "indicadores": [
            {"metrica": nome, "rotulo": rotulo, "unidade": unidade,
             **({k: getattr(metricas[nome], k) for k in ("valor", "mediana", "p25", "p75", "percentil", "n_pares")}
                if nome in metricas else {})}
            for nome, (rotulo, unidade) in INDICADORES.items()
        ],
        "oportunidades": [_oportunidade(o, descricoes) for o in oportunidades],
        "classe_dado": "PUBLICO",
        "ressalva": RESSALVA,
    }


@router.get("/organizations/{organization_id}/opportunities")
def oportunidades_da_organizacao(organization_id: int, acesso: Acesso = Depends(require_revenue_scan),
                                 db: Session = Depends(get_db)) -> dict[str, Any]:
    org = organizacao_no_escopo(db, acesso, organization_id)
    _, cnes_permitidos, limite = _escopo(acesso)
    unidades = [u for u in org.unidades if cnes_permitidos is None or u.cnes in cnes_permitidos]
    scores = _ultimos_scores(db, [u.cnes for u in unidades], limite)
    nomes = _nomes(db, list(scores))
    siglas = {u.cnes: u.sigla for u in unidades}
    impactos = _impactos(db, scores)

    ranking = sorted((
        {"cnes": c, "sigla": siglas.get(c), "nome": nomes.get(c), "score": s.score,
         "impacto_estimado": float(s.impacto_estimado),
         "impacto_confirmado": round(impactos[c]["confirmado"], 2), "impacto_sinais": round(impactos[c]["sinais"], 2),
         "valor_apresentado": float(s.valor_apresentado),
         "principal_problema": _principal(s.principal_tipo, s.principal_categoria),
         "periodo": {"inicio": s.periodo_inicio, "fim": s.periodo_fim}}
        for c, s in scores.items()
    ), key=lambda h: (-h["score"], -h["impacto_estimado"]))

    rejeicao = None
    if scores:
        inicio = min(s.periodo_inicio for s in scores.values())
        fim = max(s.periodo_fim for s in scores.values())
        corpo = resumo(db, cnes=list(scores), competencias=_competencias(inicio, fim))
        rejeicao = {k: corpo[k] for k in ("competencias", "total", "meses", "rejeitadas_unicas", "voltaram_aprovadas",
                                          "perda_liquida_aih", "perda_liquida_valor")}

    return {
        "organizacao": {"id": org.id, "sigla": org.sigla, "nome": org.nome, "uf": org.uf},
        "hospitais_da_organizacao": len(unidades),
        "hospitais_analisados": len(scores),
        "sem_scan": sorted({u.cnes for u in unidades} - set(scores)),
        "impacto_estimado_total": round(sum(h["impacto_estimado"] for h in ranking), 2),
        "impacto_confirmado_total": round(sum(h["impacto_confirmado"] for h in ranking), 2),
        "impacto_sinais_total": round(sum(h["impacto_sinais"] for h in ranking), 2),
        "valor_apresentado_total": round(sum(h["valor_apresentado"] for h in ranking), 2),
        "ranking": ranking,
        "rejeicao": rejeicao,
        "classe_dado": "PUBLICO",
        "ressalva": RESSALVA,
    }
