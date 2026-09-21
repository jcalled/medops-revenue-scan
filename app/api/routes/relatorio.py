"""
Relatório de recuperação por hospital, mês a mês, e a carga do DATASUS na hora.

A leitura segue o escopo do contrato, como o kit. Atualizar os dados é da
administração da plataforma: o dado público vale para todos os clientes. O
DATASUS publica por UF, então atualizar um hospital baixa a UF dele — só os
meses que ainda faltam.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.ibge import validar_uf
from app.api.deps import Acesso, require_revenue_scan
from app.api.routes.dados import _em_andamento, _fila_fora, require_admin_plataforma
from app.api.routes.explorar import Filtros, _titulo, filtros, hospitais_filtrados
from app.db import get_db
from app.domain.kit import referencia_padrao
from app.domain.recuperacao import meses_carregados
from app.domain.relatorio_recuperacao import montar_relatorio
from app.jobs import carga_sih
from app.jobs import fila as fila_jobs
from app.models import Establishment, ManagementOrganization, OrganizationEstablishment, SihHospitalMonth

router = APIRouter(prefix="/api/revenue-scan/recovery-report", tags=["relatorio"])

_AAAAMM = r"^\d{4}(0[1-9]|1[0-2])$"


@router.get("")
def relatorio_de_recuperacao(
    f: Filtros = Depends(filtros),
    percentual: float = Query(default=15, ge=0, le=100),
    inicio: str | None = Query(default=None, pattern=_AAAAMM,
                               description="Início do contrato (AAAAMM): só o aprovado a partir dele é cobrável"),
    referencia: str | None = Query(default=None, pattern=_AAAAMM),
    acesso: Acesso = Depends(require_revenue_scan),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if f.vazio:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Escolha uma organização ou hospitais.")
    linhas = hospitais_filtrados(db, acesso, f)
    cnes = [s.cnes for s, _ in linhas]
    recorte = {"titulo": _titulo(db, f), "filtros": {k: v for k, v in asdict(f).items() if v}}
    if not cnes:
        return {"recorte": recorte, "hospitais": [], "meses": [], "sem_dados": True}
    meses = meses_carregados(db, cnes)
    corpo = montar_relatorio(db, cnes, meses, referencia or referencia_padrao(), percentual=percentual, inicio=inicio)
    return {
        "recorte": recorte,
        "ufs": sorted({s.uf for s, _ in linhas if s.uf}),
        **corpo,
        "ressalva": (
            "Dados públicos do DATASUS (SIH: RD, RJ e ER), com atraso de publicação. Recuperada é a AIH que aparece "
            "aprovada no RD num processamento posterior à rejeição: produção aprovada, não comprovante de recebimento. "
            "Prazo de reapresentação estimado em até seis meses contados da alta (Portaria SAES/MS 1.110/2021); "
            "confirmar o calendário do gestor. Valores a recuperar são oportunidade financeira estimada."
        ),
    }


class PedidoAtualizacao(BaseModel):
    organizacao: int | None = None
    cnes: list[str] | None = None
    ufs: list[str] | None = None
    meses: int = Field(default=6, ge=1, le=12)


def _ufs_da_selecao(db: Session, pedido: PedidoAtualizacao) -> set[str]:
    ufs = {validar_uf(u) for u in pedido.ufs or [] if u.strip()}
    cnes = {c.strip().zfill(7) for c in pedido.cnes or [] if c.strip()}
    if pedido.organizacao:
        org = db.get(ManagementOrganization, pedido.organizacao)
        if org is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organização não encontrada.")
        cnes |= set(db.execute(select(OrganizationEstablishment.cnes)
                               .where(OrganizationEstablishment.organization_id == org.id)).scalars())
        if org.uf:
            ufs.add(org.uf)
    if cnes:
        ufs |= {u for u in db.execute(select(Establishment.uf).where(Establishment.cnes.in_(sorted(cnes)))).scalars() if u}
        ufs |= set(db.execute(select(SihHospitalMonth.uf).where(SihHospitalMonth.cnes.in_(sorted(cnes))).distinct()).scalars())
    return ufs


@router.post("/refresh", status_code=status.HTTP_202_ACCEPTED)
def atualizar_agora(pedido: PedidoAtualizacao, _: Acesso = Depends(require_admin_plataforma),
                    db: Session = Depends(get_db)) -> dict[str, Any]:
    """Confere no FTP do DATASUS os meses mais recentes de cada UF da seleção e enfileira só os que faltam."""
    ufs = _ufs_da_selecao(db, pedido)
    if not ufs:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Não achei a UF dos hospitais escolhidos. Informe a UF.")
    try:
        adapters = carga_sih.adapters_padrao()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="O FTP do DATASUS não respondeu.") from exc
    try:
        ocupadas = _em_andamento("CARGA")
    except Exception as exc:  # noqa: BLE001
        raise _fila_fora(exc) from exc

    resultado = []
    for uf in sorted(ufs):
        try:
            recentes = carga_sih.competencias_para_carga(adapters, uf, pedido.meses)
        except Exception as exc:  # noqa: BLE001
            resultado.append({"uf": uf, "situacao": "FTP_FORA", "detalhe": type(exc).__name__, "faltando": []})
            continue
        carregadas = set(db.execute(select(SihHospitalMonth.competencia)
                                    .where(SihHospitalMonth.uf == uf).distinct()).scalars())
        faltando = [c for c in recentes if c not in carregadas]
        item = {"uf": uf, "publicadas": recentes, "faltando": faltando, "trabalho": None}
        if not faltando:
            item["situacao"] = "EM_DIA"
        elif uf in ocupadas:
            item["situacao"] = "JA_NA_FILA"
        else:
            try:
                item["trabalho"] = fila_jobs.enfileirar_carga_uf(uf, faltando, len(faltando))
            except Exception as exc:  # noqa: BLE001
                raise _fila_fora(exc) from exc
            item["situacao"] = "ENFILEIRADA"
        resultado.append(item)
    return {"ufs": resultado}
