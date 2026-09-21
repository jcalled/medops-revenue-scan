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
from app.api.routes.explorar import (
    NATUREZAS, Filtros, _municipios, _organizacoes_por_cnes, _titulo, filtros, hospitais_filtrados,
)
from app.db import get_db
from app.domain.kit import referencia_padrao
from app.domain.recuperacao import meses_carregados
from app.domain.oficio import montar_oficio
from app.domain.relatorio_recuperacao import montar_pacote, montar_relatorio
from app.jobs import carga_sih
from app.jobs import fila as fila_jobs
from app.models import Establishment, ManagementOrganization, OrganizationEstablishment, SihHospitalMonth

router = APIRouter(prefix="/api/revenue-scan/recovery-report", tags=["relatorio"])

_AAAAMM = r"^\d{4}(0[1-9]|1[0-2])$"


def periodo(de: str | None = Query(default=None, pattern=_AAAAMM, description="Primeiro processamento (AAAAMM)"),
            ate: str | None = Query(default=None, pattern=_AAAAMM, description="Último processamento (AAAAMM); de = ate é um mês só"),
            ) -> tuple[str | None, str | None]:
    if de and ate and de > ate:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="O início do período vem depois do fim.")
    return de, ate


def meses_do_periodo(db: Session, cnes: list[str], intervalo: tuple[str | None, str | None]) -> list[str]:
    """Os processamentos carregados dentro do período escolhido (vazio: todos)."""
    de, ate = intervalo
    return [m for m in meses_carregados(db, cnes) if (not de or m >= de) and (not ate or m <= ate)]


@router.get("")
def relatorio_de_recuperacao(
    f: Filtros = Depends(filtros),
    percentual: float = Query(default=15, ge=0, le=100),
    inicio: str | None = Query(default=None, pattern=_AAAAMM,
                               description="Início do contrato (AAAAMM): só o aprovado a partir dele é cobrável"),
    referencia: str | None = Query(default=None, pattern=_AAAAMM),
    intervalo: tuple[str | None, str | None] = Depends(periodo),
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
    meses = meses_do_periodo(db, cnes, intervalo)
    if not meses:
        return {"recorte": recorte, "hospitais": [], "meses": [], "sem_dados": True}
    corpo = montar_relatorio(db, cnes, meses, referencia or referencia_padrao(), percentual=percentual, inicio=inicio)
    return {
        "recorte": recorte,
        "ufs": sorted({s.uf for s, _ in linhas if s.uf}),
        **corpo,
        "ressalva": (
            "Dados públicos do DATASUS (SIH: RD, RJ e ER), com atraso de publicação. Recuperada é a AIH que aparece "
            "aprovada no RD num processamento posterior à rejeição: produção aprovada, não comprovante de recebimento. "
            "Prazo de reapresentação estimado em até seis meses contados da alta (PRC SAES/MS 1/2022, art. 401, § 2º); "
            "confirmar o calendário do gestor. Valores a recuperar são oportunidade financeira estimada."
        ),
    }


@router.get("/package")
def pacote_de_correcao(
    f: Filtros = Depends(filtros),
    referencia: str | None = Query(default=None, pattern=_AAAAMM),
    intervalo: tuple[str | None, str | None] = Depends(periodo),
    acesso: Acesso = Depends(require_revenue_scan),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Por hospital e motivo: o que corrigir, onde, com que documentos e a regra, e as AIH ainda no prazo."""
    if f.vazio:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Escolha uma organização ou hospitais.")
    cnes = [s.cnes for s, _ in hospitais_filtrados(db, acesso, f)]
    recorte = {"titulo": _titulo(db, f), "filtros": {k: v for k, v in asdict(f).items() if v}}
    if not cnes:
        return {"recorte": recorte, "hospitais": [], "planilha": [], "meses": []}
    corpo = montar_pacote(db, cnes, meses_do_periodo(db, cnes, intervalo), referencia or referencia_padrao())
    return {
        "recorte": recorte,
        **corpo,
        "ressalva": (
            "Instruções a partir do motivo oficial da rejeição (arquivo ER do DATASUS) e dos kits da MedOps, com a "
            "regra de cada um. O arquivo corrigido sai do FaturaSUS a partir do TXT do SISAIH01 do hospital; sem ele, "
            "o hospital aplica as instruções no próprio sistema e reapresenta dentro do prazo, sem alterar datas."
        ),
    }


@router.get("/letter")
def oficio_para_a_secretaria(
    f: Filtros = Depends(filtros),
    intervalo: tuple[str | None, str | None] = Depends(periodo),
    acesso: Acesso = Depends(require_revenue_scan),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Números para o ofício de cada hospital à secretaria: habilitação, leitos, códigos sem descrição e teto de APAC."""
    if f.vazio:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Escolha uma organização ou hospitais.")
    linhas = hospitais_filtrados(db, acesso, f)
    cnes = [s.cnes for s, _ in linhas]
    recorte = {"titulo": _titulo(db, f), "filtros": {k: v for k, v in asdict(f).items() if v}}
    if not cnes:
        return {"recorte": recorte, "hospitais": [], "meses": []}
    corpo = montar_oficio(db, cnes, meses_do_periodo(db, cnes, intervalo))
    ufs = {s.cnes: s.uf for s, _ in linhas}
    for h in corpo["hospitais"]:
        h["uf"] = ufs.get(h["cnes"])
    return {"recorte": recorte, **corpo}


ORDENS_RANKING = {
    "a_recuperar": lambda h: h["a_recuperar"]["valor"],
    "vence_neste_mes": lambda h: h["vence_neste_mes"]["valor"],
    "recuperado": lambda h: h["recuperado"]["valor"],
    "taxa_recuperacao": lambda h: h["taxa_recuperacao"],
    "rejeitado": lambda h: h["rejeitado"]["valor"],
}
MAX_RANKING = 1500


@router.get("/ranking")
def ranking_de_recuperacao(
    f: Filtros = Depends(filtros),
    ordem: str = Query(default="a_recuperar", description=", ".join(ORDENS_RANKING)),
    limite: int = Query(default=200, ge=1, le=MAX_RANKING),
    referencia: str | None = Query(default=None, pattern=_AAAAMM),
    acesso: Acesso = Depends(require_revenue_scan),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Hospitais do recorte em ordem de quanto ainda dá para recuperar — ou de quanto
    já recuperam sozinhos —, para escolher quem procurar. Sem recorte, todas as UFs
    carregadas; pode demorar no Brasil inteiro.
    """
    if ordem not in ORDENS_RANKING:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Ordem desconhecida. Use {', '.join(ORDENS_RANKING)}.")
    linhas = hospitais_filtrados(db, acesso, f)
    if len(linhas) > MAX_RANKING * 4:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"{len(linhas)} hospitais no recorte: escolha uma UF ou uma natureza para calcular.")
    scores = {s.cnes: (s, e) for s, e in linhas}
    cnes = list(scores)
    recorte = {"titulo": _titulo(db, f), "filtros": {k: v for k, v in asdict(f).items() if v}}
    if not cnes:
        return {"recorte": recorte, "hospitais": [], "total_hospitais": 0, "meses": []}
    meses = meses_carregados(db, cnes)
    corpo = montar_relatorio(db, cnes, meses, referencia or referencia_padrao())
    organizacoes = _organizacoes_por_cnes(db, cnes)
    municipios = _municipios(db, {s.codigo_municipio for s, _ in linhas})

    itens = []
    for h in corpo["hospitais"]:
        s, _ = scores[h["cnes"]]
        t = h["total"]
        base = t["rejeitadas"]["valor"] - t["JA_APROVADA"]["valor"]
        itens.append({
            "cnes": h["cnes"], "nome": h["nome"], "uf": s.uf,
            "municipio": municipios.get(s.codigo_municipio or "", s.codigo_municipio),
            "natureza": NATUREZAS.get(s.natureza_grupo or "", None), "leitos_sus": s.leitos_sus,
            "organizacoes": organizacoes.get(h["cnes"], []),
            "rejeitado": t["rejeitadas"], "recuperado": t["RECUPERADA"], "a_recuperar": t["A_RECUPERAR"],
            "depende_gestor": t["DEPENDE_GESTOR"], "nao_recuperavel": t["PERDIDA"], "botao": t["botao"],
            "vence_neste_mes": t["vence_neste_mes"],
            # Quanto do que foi rejeitado já voltou aprovado: é o esforço de recuperação do próprio hospital.
            "taxa_recuperacao": round(t["RECUPERADA"]["valor"] / base, 4) if base > 0 else 0.0,
            "motivo_principal": h["motivos"][0] if h["motivos"] else None,
        })
    itens.sort(key=lambda h: (-ORDENS_RANKING[ordem](h), h["cnes"]))
    return {
        "recorte": recorte, "referencia": corpo["referencia"], "meses": meses, "ordem": ordem,
        "total_hospitais": len(itens), "total": corpo["total"], "hospitais": itens[:limite],
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
