"""
Atualização dos dados do DATASUS pela tela, sem terminal.

O que já está carregado por UF, o que o DATASUS publicou, e a fila de cargas e
recálculos. É da administração da plataforma: o dado público vale para todos
os clientes, então um tenant não dispara carga.

A carga roda no worker (fila revenue-scan), uma de cada vez: baixa RD, RJ e ER
de cada competência, os leitos e habilitações do CNES, grava, apaga os arquivos
e recalcula o scan da UF.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.ibge import CODIGO_UF, validar_uf
from app.api.deps import Acesso, require_revenue_scan
from app.db import get_db
from app.jobs import carga_sih
from app.jobs import fila as fila_jobs
from app.models import CnesBed, DataLoad, HospitalScore, SihHospitalMonth

router = APIRouter(prefix="/api/revenue-scan/data", tags=["dados"])

_COMPETENCIA = re.compile(r"^\d{6}$")


def require_admin_plataforma(acesso: Acesso = Depends(require_revenue_scan)) -> Acesso:
    if not acesso.principal.platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="Atualizar os dados do DATASUS é da administração da plataforma: "
                                   "o dado vale para todos os clientes.")
    return acesso


def _ufs(lista: list[str]) -> list[str]:
    if any(u.strip().upper() in {"TODAS", "BR", "BRASIL"} for u in lista):
        return sorted(CODIGO_UF)
    try:
        return list(dict.fromkeys(validar_uf(u) for u in lista if u.strip()))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def _fila_fora(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                         detail=f"A fila de cargas não respondeu ({type(exc).__name__}). Confira o Redis e o "
                                "worker-revenue-scan e tente de novo.")


def _iso(valor: Any) -> str | None:
    return valor.isoformat() if valor else None


@router.get("/status")
def status_dos_dados(_: Acesso = Depends(require_admin_plataforma), db: Session = Depends(get_db)) -> dict[str, Any]:
    competencias: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for uf, competencia, hospitais in db.execute(
        select(SihHospitalMonth.uf, SihHospitalMonth.competencia, func.count(func.distinct(SihHospitalMonth.cnes)))
        .group_by(SihHospitalMonth.uf, SihHospitalMonth.competencia)
    ):
        competencias[uf].append({"competencia": competencia, "hospitais": hospitais})
    hospitais_uf = dict(db.execute(
        select(SihHospitalMonth.uf, func.count(func.distinct(SihHospitalMonth.cnes))).group_by(SihHospitalMonth.uf)
    ).all())
    cnes = dict(db.execute(select(CnesBed.uf, func.max(CnesBed.competencia)).group_by(CnesBed.uf)).all())
    scans = {uf: (n, inicio, fim, gerado) for uf, n, inicio, fim, gerado in db.execute(
        select(HospitalScore.uf, func.count(HospitalScore.id), func.max(HospitalScore.periodo_inicio),
               func.max(HospitalScore.periodo_fim), func.max(HospitalScore.gerado_em)).group_by(HospitalScore.uf)
    )}
    ultimas = dict(db.execute(
        select(DataLoad.uf, func.max(DataLoad.concluido_em)).where(DataLoad.status == "OK").group_by(DataLoad.uf)
    ).all())
    mais_recente = max((c["competencia"] for itens in competencias.values() for c in itens), default=None)

    ufs = []
    for uf in sorted(CODIGO_UF):
        itens = sorted(competencias.get(uf, []), key=lambda c: c["competencia"])
        scan = scans.get(uf)
        ufs.append({
            "uf": uf,
            "competencias": itens,
            "hospitais": hospitais_uf.get(uf, 0),
            "cnes_competencia": cnes.get(uf),
            "scan": {"hospitais": scan[0], "periodo_inicio": scan[1], "periodo_fim": scan[2], "gerado_em": _iso(scan[3])}
            if scan else None,
            "ultima_carga_em": _iso(ultimas.get(uf)),
            # Atrás do mês mais recente carregado em qualquer UF: vale atualizar.
            "desatualizada": bool(itens) and mais_recente is not None and itens[-1]["competencia"] < mais_recente,
        })

    cargas = [{
        "id": c.id, "fonte": c.fonte, "uf": c.uf, "competencia": c.competencia, "origem": c.origem,
        "arquivo": c.arquivo, "status": c.status, "linhas": c.linhas, "erro": (c.erro or "")[:400] or None,
        "iniciado_em": _iso(c.iniciado_em), "concluido_em": _iso(c.concluido_em),
    } for c in db.execute(select(DataLoad).order_by(DataLoad.iniciado_em.desc(), DataLoad.id.desc()).limit(60)).scalars()]

    try:
        fila = {"disponivel": True, "trabalhos": fila_jobs.trabalhos()}
    except Exception as exc:  # noqa: BLE001 — sem Redis a tela ainda mostra o que está carregado
        fila = {"disponivel": False, "erro": type(exc).__name__, "trabalhos": []}

    return {"ufs": ufs, "mais_recente": mais_recente, "cargas": cargas, "fila": fila}


@router.get("/published")
def competencias_publicadas(
    uf: str = Query(..., description="Sigla da UF"),
    _: Acesso = Depends(require_admin_plataforma),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Competências com RD, RJ e ER publicados no FTP do DATASUS, marcando as já carregadas."""
    [sigla] = _ufs([uf])
    try:
        adapters = carga_sih.adapters_padrao()
        comuns = set.intersection(*(set(adapters[t].competencias_disponiveis(sigla)) for t in carga_sih.TIPOS))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"O FTP do DATASUS não respondeu ({type(exc).__name__}). Tente de novo.") from exc
    carregadas = set(db.execute(
        select(SihHospitalMonth.competencia).where(SihHospitalMonth.uf == sigla).distinct()).scalars())
    return {"uf": sigla, "publicadas": [{"competencia": c, "carregada": c in carregadas}
                                        for c in sorted(comuns, reverse=True)[:24]]}


class PedidoCarga(BaseModel):
    ufs: list[str] = Field(min_length=1)
    competencias: list[str] | None = None
    quantidade: int = Field(default=3, ge=1, le=24)


class PedidoRecalculo(BaseModel):
    ufs: list[str] | None = None


def _em_andamento(tipo: str) -> set[str]:
    return {uf for t in fila_jobs.trabalhos(limite=200)
            if t["tipo"] == tipo and t["status"] in fila_jobs.EM_ANDAMENTO for uf in t["ufs"]}


@router.post("/loads", status_code=status.HTTP_202_ACCEPTED)
def enfileirar_cargas(pedido: PedidoCarga, _: Acesso = Depends(require_admin_plataforma)) -> dict[str, Any]:
    ufs = _ufs(pedido.ufs)
    competencias = sorted({c.strip() for c in pedido.competencias or [] if c.strip()}) or None
    if competencias and not all(_COMPETENCIA.match(c) for c in competencias):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Competência deve ser AAAAMM.")
    try:
        # Mesma UF já na fila ou rodando: carregar de novo só duplicaria o trabalho.
        ocupadas = _em_andamento("CARGA")
        trabalhos = [{"uf": uf, "id": fila_jobs.enfileirar_carga_uf(uf, competencias, pedido.quantidade)}
                     for uf in ufs if uf not in ocupadas]
    except Exception as exc:  # noqa: BLE001
        raise _fila_fora(exc) from exc
    return {"trabalhos": trabalhos, "ignoradas": sorted(set(ufs) & ocupadas)}


@router.post("/recalculate", status_code=status.HTTP_202_ACCEPTED)
def enfileirar_recalculo(pedido: PedidoRecalculo, _: Acesso = Depends(require_admin_plataforma)) -> dict[str, Any]:
    ufs = _ufs(pedido.ufs) if pedido.ufs else None
    try:
        return {"id": fila_jobs.enfileirar_recalculo(ufs)}
    except Exception as exc:  # noqa: BLE001
        raise _fila_fora(exc) from exc
