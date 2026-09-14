"""
Explorar hospitais e montar qualquer recorte: uma OSS, os hospitais das
prefeituras de um estado, os estaduais de um município, uma seleção feita à mão
ou o Brasil carregado inteiro.

Tudo dentro do contrato: o escopo do tenant (CNES e organizações liberados)
entra em toda consulta antes dos filtros de quem está usando.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import Acesso, require_revenue_scan
from app.api.routes.organizacoes import _escopo
from app.api.routes.scan import RESSALVA, _competencias, _meses_no_periodo, _principal
from app.db import get_db
from app.domain.classificacao import GESTOES, NATUREZAS, ORDEM_PORTES
from app.domain.prova import confirmado_por_mes
from app.domain.resumo import _lotes, resumo
from app.models import Establishment, HospitalScore, ManagementOrganization, Municipality, OrganizationEstablishment

router = APIRouter(prefix="/api/revenue-scan", tags=["explorar"])

_CNES = re.compile(r"^\d{1,7}$")
_MAX_SELECAO = 1000
ORDENS = {
    "score": lambda s: (-s.score, -float(s.impacto_confirmado or 0)),
    "confirmado": lambda s: -float(s.impacto_confirmado or 0),
    "sinais": lambda s: -float(s.impacto_sinais or 0),
    "apresentado": lambda s: -float(s.valor_apresentado or 0),
}


@dataclass
class Filtros:
    uf: str | None = None
    municipio: str | None = None
    natureza: str | None = None
    gestao: str | None = None
    porte: str | None = None
    organizacao: int | None = None
    q: str | None = None
    cnes: list[str] | None = None

    @property
    def vazio(self) -> bool:
        return not any(v for v in asdict(self).values())


def filtros(
    uf: str | None = Query(default=None, description="Sigla da UF"),
    municipio: str | None = Query(default=None, description="Código do município no CNES (6 dígitos)"),
    natureza: str | None = Query(default=None, description=", ".join(NATUREZAS)),
    gestao: str | None = Query(default=None, description=", ".join(GESTOES)),
    porte: str | None = Query(default=None),
    organizacao: int | None = Query(default=None, description="Id da organização gestora"),
    q: str | None = Query(default=None, description="Nome ou CNES"),
    cnes: str | None = Query(default=None, description="CNES separados por vírgula"),
) -> Filtros:
    lista = None
    if cnes:
        lista = [c.strip() for c in cnes.split(",") if c.strip()]
        if len(lista) > _MAX_SELECAO or any(not _CNES.match(c) for c in lista):
            raise HTTPException(status_code=422, detail=f"Seleção inválida: até {_MAX_SELECAO} CNES numéricos.")
        lista = sorted({c.zfill(7) for c in lista})
    if natureza and natureza.upper() not in NATUREZAS:
        raise HTTPException(status_code=422, detail=f"Natureza desconhecida. Use {', '.join(NATUREZAS)}.")
    return Filtros(
        uf=uf.strip().upper() if uf else None, municipio=municipio.strip() if municipio else None,
        natureza=natureza.upper() if natureza else None, gestao=gestao.strip().upper() if gestao else None,
        porte=porte or None, organizacao=organizacao, q=(q or "").strip() or None, cnes=lista,
    )


def hospitais_filtrados(db: Session, acesso: Acesso, f: Filtros) -> list[tuple[HospitalScore, Establishment | None]]:
    """O último scan de cada hospital que passa pelo contrato e pelos filtros."""
    siglas, cnes_escopo, limite = _escopo(acesso)
    ultimo = (select(HospitalScore.cnes, func.max(HospitalScore.periodo_fim).label("fim"))
              .group_by(HospitalScore.cnes).subquery())
    consulta = (
        select(HospitalScore, Establishment)
        .join(ultimo, and_(HospitalScore.cnes == ultimo.c.cnes, HospitalScore.periodo_fim == ultimo.c.fim))
        .outerjoin(Establishment, Establishment.cnes == HospitalScore.cnes)
    )
    if cnes_escopo is not None:
        consulta = consulta.where(HospitalScore.cnes.in_(sorted(cnes_escopo)))
    if siglas is not None:
        consulta = consulta.where(HospitalScore.cnes.in_(
            select(OrganizationEstablishment.cnes).join(ManagementOrganization)
            .where(func.upper(ManagementOrganization.sigla).in_(sorted(siglas)))
        ))
    if f.uf:
        consulta = consulta.where(HospitalScore.uf == f.uf)
    if f.municipio:
        consulta = consulta.where(HospitalScore.codigo_municipio == f.municipio)
    if f.natureza:
        consulta = consulta.where(HospitalScore.natureza_grupo == f.natureza)
    if f.gestao:
        consulta = consulta.where(HospitalScore.gestao == f.gestao)
    if f.porte:
        consulta = consulta.where(HospitalScore.porte == f.porte)
    if f.organizacao:
        consulta = consulta.where(HospitalScore.cnes.in_(
            select(OrganizationEstablishment.cnes).where(OrganizationEstablishment.organization_id == f.organizacao)
        ))
    if f.cnes:
        consulta = consulta.where(HospitalScore.cnes.in_(f.cnes))
    if f.q:
        termo = f"%{f.q.lower()}%"
        consulta = consulta.where(or_(
            func.lower(Establishment.nome_fantasia).like(termo), HospitalScore.cnes == f.q.zfill(7)[:7],
        ))

    melhores: dict[str, tuple[HospitalScore, Establishment | None]] = {}
    for score, estabelecimento in db.execute(consulta):
        if limite and _meses_no_periodo(score.periodo_inicio, score.periodo_fim) > limite:
            continue
        atual = melhores.get(score.cnes)
        if atual is None or score.gerado_em >= atual[0].gerado_em:
            melhores[score.cnes] = (score, estabelecimento)
    return list(melhores.values())


def _organizacoes_por_cnes(db: Session, cnes: list[str]) -> dict[str, list[str]]:
    saida: dict[str, list[str]] = {}
    for lote in _lotes(cnes):
        for numero, sigla in db.execute(
            select(OrganizationEstablishment.cnes, ManagementOrganization.sigla).join(ManagementOrganization)
            .where(OrganizationEstablishment.cnes.in_(lote))
        ):
            saida.setdefault(numero, []).append(sigla)
    return saida


def _municipios(db: Session, codigos: set[str]) -> dict[str, str]:
    saida: dict[str, str] = {}
    for lote in _lotes(sorted(c for c in codigos if c)):
        for codigo, nome, uf in db.execute(
            select(Municipality.codigo_cnes, Municipality.nome, Municipality.uf).where(Municipality.codigo_cnes.in_(lote))
        ):
            saida[codigo] = f"{nome}/{uf}"
    return saida


def _item(score: HospitalScore, estabelecimento: Establishment | None, organizacoes: dict[str, list[str]],
          municipios: dict[str, str]) -> dict[str, Any]:
    return {
        "cnes": score.cnes,
        "nome": (estabelecimento.nome_fantasia or estabelecimento.razao_social) if estabelecimento else None,
        "uf": score.uf,
        "municipio": municipios.get(score.codigo_municipio or "", score.codigo_municipio),
        "natureza": NATUREZAS.get(score.natureza_grupo or "", None),
        "natureza_grupo": score.natureza_grupo,
        "gestao": GESTOES.get(score.gestao or "", None),
        "porte": score.porte,
        "leitos_sus": score.leitos_sus,
        "score": score.score,
        "impacto_confirmado": float(score.impacto_confirmado or 0),
        "impacto_sinais": float(score.impacto_sinais or 0),
        "impacto_estimado": float(score.impacto_estimado or 0),
        "valor_apresentado": float(score.valor_apresentado or 0),
        "principal_problema": _principal(score.principal_tipo, score.principal_categoria),
        "organizacoes": organizacoes.get(score.cnes, []),
        "periodo": {"inicio": score.periodo_inicio, "fim": score.periodo_fim},
    }


def _ordenar(linhas: list[tuple[HospitalScore, Establishment | None]], ordem: str):
    if ordem == "nome":
        return sorted(linhas, key=lambda l: ((l[1].nome_fantasia if l[1] else None) or "~", l[0].cnes))
    if ordem not in ORDENS:
        raise HTTPException(status_code=422, detail=f"Ordem desconhecida. Use {', '.join([*ORDENS, 'nome'])}.")
    return sorted(linhas, key=lambda l: (ORDENS[ordem](l[0]), l[0].cnes))


@router.get("/hospitals")
def listar_hospitais(
    f: Filtros = Depends(filtros),
    ordem: str = Query(default="score"),
    pagina: int = Query(default=1, ge=1),
    por_pagina: int = Query(default=50, ge=1, le=200),
    acesso: Acesso = Depends(require_revenue_scan),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    linhas = _ordenar(hospitais_filtrados(db, acesso, f), ordem)
    pagina_linhas = linhas[(pagina - 1) * por_pagina: pagina * por_pagina]
    cnes = [s.cnes for s, _ in pagina_linhas]
    organizacoes = _organizacoes_por_cnes(db, cnes)
    municipios = _municipios(db, {s.codigo_municipio for s, _ in pagina_linhas})
    return {
        "total": len(linhas), "pagina": pagina, "por_pagina": por_pagina,
        "itens": [_item(s, e, organizacoes, municipios) for s, e in pagina_linhas],
    }


@router.get("/filters")
def filtros_disponiveis(
    uf: str | None = Query(default=None, description="Para listar os municípios desta UF"),
    acesso: Acesso = Depends(require_revenue_scan),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """As opções que existem nos dados carregados e liberados, com quantos hospitais cada uma tem."""
    linhas = hospitais_filtrados(db, acesso, Filtros())
    scores = [s for s, _ in linhas]
    permitidos = {s.cnes for s in scores}

    municipios: list[dict[str, Any]] = []
    if uf:
        contagem = Counter(s.codigo_municipio for s in scores if s.uf == uf.upper() and s.codigo_municipio)
        nomes = _municipios(db, set(contagem))
        municipios = sorted(({"codigo": c, "nome": nomes.get(c, c), "hospitais": n} for c, n in contagem.items()),
                            key=lambda m: m["nome"])

    organizacoes = []
    for org in db.execute(select(ManagementOrganization).order_by(ManagementOrganization.sigla)).scalars():
        com_scan = sum(1 for u in org.unidades if u.cnes in permitidos)
        if com_scan:
            organizacoes.append({"id": org.id, "sigla": org.sigla, "nome": org.nome, "uf": org.uf,
                                 "hospitais": com_scan, "hospitais_cadastrados": len(org.unidades)})

    def opcoes(valores: Counter, rotulos: dict[str, str] | None = None, ordem: tuple[str, ...] | None = None):
        chaves = [c for c in (ordem or sorted(valores, key=str)) if c in valores]
        return [{"valor": c, "rotulo": (rotulos or {}).get(c, c), "hospitais": valores[c]} for c in chaves]

    return {
        "hospitais": len(scores),
        "periodo": {"inicio": min((s.periodo_inicio for s in scores), default=None),
                    "fim": max((s.periodo_fim for s in scores), default=None)},
        "ufs": opcoes(Counter(s.uf for s in scores if s.uf)),
        "municipios": municipios,
        "naturezas": opcoes(Counter(s.natureza_grupo for s in scores if s.natureza_grupo), NATUREZAS, tuple(NATUREZAS)),
        "gestoes": opcoes(Counter(s.gestao for s in scores if s.gestao), GESTOES, tuple(GESTOES)),
        "portes": opcoes(Counter(s.porte for s in scores if s.porte), None, ORDEM_PORTES),
        "organizacoes": organizacoes,
    }


def _titulo(db: Session, f: Filtros) -> str:
    partes = []
    if f.organizacao:
        org = db.get(ManagementOrganization, f.organizacao)
        partes.append(f"{org.nome} ({org.sigla})" if org else "Organização")
    if f.cnes:
        partes.append(f"Seleção de {len(f.cnes)} hospita{'l' if len(f.cnes) == 1 else 'is'}")
    if f.natureza:
        partes.append(NATUREZAS[f.natureza])
    if f.gestao:
        partes.append(GESTOES.get(f.gestao, f.gestao))
    if f.porte:
        partes.append(f.porte)
    if f.municipio:
        partes.append(_municipios(db, {f.municipio}).get(f.municipio, f"município {f.municipio}"))
    elif f.uf:
        partes.append(f.uf)
    if f.q:
        partes.append(f"busca “{f.q}”")
    return " · ".join(partes) if partes else "Todos os hospitais carregados"


@router.get("/panorama")
def panorama(
    f: Filtros = Depends(filtros),
    limite_ranking: int = Query(default=100, ge=1, le=500),
    ordem: str = Query(default="score", description="score, confirmado, sinais, apresentado ou nome"),
    acesso: Acesso = Depends(require_revenue_scan),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    O painel executivo de qualquer recorte: números, ranking e rejeição do período.

    Score mede a intensidade sobre a própria produção — um hospital pequeno pode
    ter score alto com pouco dinheiro. Para prospecção, `ordem=confirmado` põe o
    valor na frente.
    """
    linhas = _ordenar(hospitais_filtrados(db, acesso, f), ordem)
    scores = [s for s, _ in linhas]
    cnes = [s.cnes for s in scores]
    exibidos = linhas[:limite_ranking]
    organizacoes = _organizacoes_por_cnes(db, [s.cnes for s, _ in exibidos])
    municipios = _municipios(db, {s.codigo_municipio for s, _ in exibidos})

    # Confirmada por mês, provável AIH a AIH: é a base do mês na simulação do híbrido.
    por_mes = confirmado_por_mes(db, scores)

    rejeicao = None
    if scores:
        corpo = resumo(db, cnes=cnes, competencias=_competencias(min(s.periodo_inicio for s in scores),
                                                                 max(s.periodo_fim for s in scores)))
        rejeicao = {k: corpo[k] for k in ("competencias", "total", "meses", "rejeitadas_unicas", "voltaram_aprovadas",
                                          "perda_liquida_aih", "perda_liquida_valor")}

    sem_scan: list[str] = []
    cadastrados = len(scores)
    if f.organizacao:
        unidades = set(db.execute(select(OrganizationEstablishment.cnes)
                                  .where(OrganizationEstablishment.organization_id == f.organizacao)).scalars())
        _, cnes_escopo, _ = _escopo(acesso)
        if cnes_escopo is not None:
            unidades &= cnes_escopo
        sem_scan, cadastrados = sorted(unidades - set(cnes)), len(unidades)

    return {
        "recorte": {"titulo": _titulo(db, f), "filtros": {k: v for k, v in asdict(f).items() if v}},
        "hospitais_do_recorte": cadastrados,
        "hospitais_analisados": len(scores),
        "sem_scan": sem_scan,
        "impacto_confirmado_total": round(sum(float(s.impacto_confirmado or 0) for s in scores), 2),
        "impacto_sinais_total": round(sum(float(s.impacto_sinais or 0) for s in scores), 2),
        "impacto_estimado_total": round(sum(float(s.impacto_estimado or 0) for s in scores), 2),
        "valor_apresentado_total": round(sum(float(s.valor_apresentado or 0) for s in scores), 2),
        "confirmado_por_mes": {m: round(sum(v.get(m, 0.0) for v in por_mes.values()), 2)
                               for m in (rejeicao["competencias"] if rejeicao else [])},
        "ranking": [{**_item(s, e, organizacoes, municipios), "confirmado_por_mes": por_mes.get(s.cnes, {})}
                    for s, e in exibidos],
        "ranking_limitado": len(linhas) > limite_ranking,
        "rejeicao": rejeicao,
        "classe_dado": "PUBLICO",
        "ressalva": RESSALVA,
    }
