"""
Piloto de êxito: provar a recuperação antes do contrato, só com dado público.

Escolhem-se AIH reapresentáveis de um hospital (as que vencem primeiro, as de
chance maior), o hospital recebe o pacote de correção só delas e reapresenta; a
cada mês novo do DATASUS o sistema confere no RD quais voltaram aprovadas, com o
mês e o valor. É um acompanhamento do tipo PILOTO: só as AIH escolhidas, e só
conta o que voltar depois de o piloto começar. Da administração da plataforma.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import Acesso
from app.api.routes.dados import require_admin_plataforma
from app.db import get_db
from app.domain import recuperacao as rec
from app.domain.kit import CLASSES, classificar, referencia_padrao
from app.domain.kits_motivo import classes_confirmadas
from app.domain.prova import aih_rejeitadas
from app.domain.recuperacao import meses_carregados
from app.domain.relatorio_recuperacao import MOTIVOS_DO_BOTAO, _apontamento, _grupo, _prevencao, montar_pacote
from app.domain.resumo import _nomes
from app.engine.categorias import POR_CODIGO
from app.models import ManagementOrganization, OrganizationEstablishment, RecoveryItem, RecoveryTracking, SihRejection

router = APIRouter(prefix="/api/revenue-scan/pilot", tags=["piloto"])

ORDEM_CLASSE = {"ALTA": 0, "MEDIA": 1, "INCERTA": 2, "GESTOR": 3, "INVESTIGAR": 4}
MAX_AIH = 500


def _cnes(db: Session, organizacao: int | None, cnes: list[str] | None) -> list[str]:
    lista = {c.strip().zfill(7) for c in cnes or [] if c.strip()}
    if organizacao:
        if db.get(ManagementOrganization, organizacao) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organização não encontrada.")
        lista |= set(db.execute(select(OrganizationEstablishment.cnes)
                                .where(OrganizationEstablishment.organization_id == organizacao)).scalars())
    if not lista:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Escolha uma organização ou hospitais.")
    return sorted(lista)


def candidatas(db: Session, cnes: list[str], incluir_gestor: bool) -> list[dict[str, Any]]:
    """As AIH que ainda dá para reapresentar, da que vence primeiro e de maior chance para a menor."""
    meses = meses_carregados(db, cnes)
    linhas = aih_rejeitadas(db, cnes, meses)
    referencia, confirmados = referencia_padrao(), classes_confirmadas(db)
    prevencao, nomes = _prevencao(db, linhas), _nomes(db, cnes)
    saida = []
    for l in linhas:
        classe, prazo = classificar(l, referencia, confirmados)
        grupo = _grupo(l, classe)
        if grupo != "A_RECUPERAR" and not (incluir_gestor and grupo == "DEPENDE_GESTOR"):
            continue
        codigos = [m["codigo"] for m in l["motivos"]]
        visto = _apontamento(prevencao.get((l["n_aih"], l["competencia"])))
        saida.append({
            "n_aih": l["n_aih"], "cnes": l["cnes"], "hospital": nomes.get(l["cnes"]), "competencia": l["competencia"],
            "valor": l["valor"], "prazo": prazo, "vence_neste_mes": prazo == referencia, "classe": classe,
            "classe_nome": CLASSES.get(classe, classe), "grupo": grupo,
            "motivos": [{"codigo": m["codigo"], "descricao": m["descricao"]} for m in l["motivos"]],
            "categoria": l["categoria"], "categoria_nome": POR_CODIGO[l["categoria"]].nome if l["categoria"] in POR_CODIGO else None,
            "faturasus_pegaria": visto["grupo"] == "PEGARIA", "faturasus_diz": visto["mensagem"],
            "botao_faturasus": bool(codigos) and set(codigos) <= MOTIVOS_DO_BOTAO,
        })
    saida.sort(key=lambda a: (a["prazo"] or "999999", ORDEM_CLASSE.get(a["classe"], 9), -a["valor"]))
    return saida


@router.get("/candidates")
def listar_candidatas(organizacao: int | None = Query(default=None), cnes: str | None = Query(default=None),
                      incluir_gestor: bool = Query(default=False), _: Acesso = Depends(require_admin_plataforma),
                      db: Session = Depends(get_db)) -> dict[str, Any]:
    lista = candidatas(db, _cnes(db, organizacao, cnes.split(",") if cnes else None), incluir_gestor)
    por_prazo: dict[str, dict[str, float]] = defaultdict(lambda: {"aih": 0, "valor": 0.0})
    for a in lista:
        por_prazo[a["prazo"] or ""]["aih"] += 1
        por_prazo[a["prazo"] or ""]["valor"] += a["valor"]
    return {"referencia": referencia_padrao(), "aih": lista[:3000], "total": len(lista),
            "por_prazo": [{"prazo": p, "aih": int(v["aih"]), "valor": round(v["valor"], 2)} for p, v in sorted(por_prazo.items())]}


class NovoPiloto(BaseModel):
    organizacao: int | None = None
    cnes: list[str] | None = None
    aih: list[str] = Field(min_length=1, max_length=MAX_AIH)
    nome: str | None = Field(default=None, max_length=120)
    percentual: float = Field(default=15, ge=0, le=100)


def _resumo(t: RecoveryTracking) -> dict[str, Any]:
    itens = list(t.itens)
    voltaram = [i for i in itens if i.situacao == rec.RECUPERADA]
    indicado = sum(float(i.valor_rejeitado or 0) for i in itens)
    aprovado = sum(float(i.valor_aprovado if i.valor_aprovado is not None else i.valor_rejeitado or 0) for i in voltaram)
    por_mes: dict[str, dict[str, float]] = defaultdict(lambda: {"aih": 0, "valor": 0.0})
    for i in voltaram:
        por_mes[i.competencia_aprovacao or ""]["aih"] += 1
        por_mes[i.competencia_aprovacao or ""]["valor"] += float(i.valor_aprovado if i.valor_aprovado is not None else i.valor_rejeitado or 0)
    return {
        "id": t.id, "nome": t.nome, "organization_id": t.organization_id, "cnes": t.cnes, "inicio": t.inicio,
        "status": t.status, "percentual": float(t.percentual), "conferido_em": t.conferido_em.isoformat() if t.conferido_em else None,
        "indicadas": {"aih": len(itens), "valor": round(indicado, 2)},
        "voltaram": {"aih": len(voltaram), "valor": round(aprovado, 2)},
        "taxa": round(len(voltaram) / len(itens), 4) if itens else 0.0,
        "por_mes": [{"competencia": c, "aih": int(v["aih"]), "valor": round(v["valor"], 2)} for c, v in sorted(por_mes.items())],
        "honorarios": round(aprovado * float(t.percentual) / 100, 2),
    }


def _piloto(db: Session, piloto_id: int) -> RecoveryTracking:
    t = db.execute(select(RecoveryTracking).where(RecoveryTracking.id == piloto_id, RecoveryTracking.tipo == "PILOTO")
                   .options(selectinload(RecoveryTracking.itens))).scalar_one_or_none()
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Piloto não encontrado.")
    return t


@router.post("", status_code=status.HTTP_201_CREATED)
def criar(pedido: NovoPiloto, acesso: Acesso = Depends(require_admin_plataforma), db: Session = Depends(get_db)) -> dict[str, Any]:
    cnes = _cnes(db, pedido.organizacao, pedido.cnes)
    escolhidas = set(pedido.aih)
    disponiveis = {a["n_aih"]: a for a in candidatas(db, cnes, incluir_gestor=True) if a["n_aih"] in escolhidas}
    fora = sorted(escolhidas - set(disponiveis))
    if not disponiveis:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Nenhuma das AIH escolhidas ainda dá para reapresentar.")
    rejeicoes = {(r.n_aih, r.competencia): r for r in db.execute(
        select(SihRejection).where(SihRejection.n_aih.in_(list(disponiveis)))).scalars()}
    org = db.get(ManagementOrganization, pedido.organizacao) if pedido.organizacao else None
    t = RecoveryTracking(nome=(pedido.nome or f"Piloto · {org.sigla if org else 'hospitais selecionados'}").strip()[:120],
                         organization_id=pedido.organizacao, cnes=cnes, inicio=referencia_padrao(), tipo="PILOTO",
                         percentual=Decimal(str(pedido.percentual)), fixo_por_hospital=Decimal("0"),
                         status=rec.ATIVO, criado_por=acesso.principal.user_id)
    for n_aih, a in disponiveis.items():
        r = rejeicoes[(n_aih, a["competencia"])]
        t.itens.append(RecoveryItem(
            cnes=r.cnes, uf=r.uf, n_aih=n_aih, origem="PILOTO", competencia_rejeicao=r.competencia,
            competencia_aih=r.competencia_aih, procedimento=r.proc_realizado, dt_saida=r.dt_saida,
            valor_rejeitado=r.valor or 0, categoria=a["categoria"],
            motivos=[m["codigo"] for m in a["motivos"]], situacao=rec.ABERTA))
    db.add(t)
    db.flush()
    rec.conferir(db, t)
    db.commit()
    return {**_resumo(t), "fora_do_prazo": fora}


@router.get("")
def listar(_: Acesso = Depends(require_admin_plataforma), db: Session = Depends(get_db)) -> dict[str, Any]:
    pilotos = db.execute(select(RecoveryTracking).where(RecoveryTracking.tipo == "PILOTO")
                         .options(selectinload(RecoveryTracking.itens)).order_by(RecoveryTracking.id.desc())).scalars()
    return {"pilotos": [_resumo(t) for t in pilotos]}


@router.get("/{piloto_id}")
def detalhe(piloto_id: int, _: Acesso = Depends(require_admin_plataforma), db: Session = Depends(get_db)) -> dict[str, Any]:
    t = _piloto(db, piloto_id)
    nomes = _nomes(db, t.cnes)
    return {**_resumo(t), "itens": [{
        "n_aih": i.n_aih, "cnes": i.cnes, "hospital": nomes.get(i.cnes), "competencia_rejeicao": i.competencia_rejeicao,
        "valor_rejeitado": float(i.valor_rejeitado or 0), "motivos": i.motivos,
        "categoria_nome": POR_CODIGO[i.categoria].nome if i.categoria in POR_CODIGO else i.categoria,
        "situacao": i.situacao, "competencia_aprovacao": i.competencia_aprovacao,
        "valor_aprovado": float(i.valor_aprovado) if i.valor_aprovado is not None else None,
    } for i in sorted(t.itens, key=lambda i: (i.situacao != rec.RECUPERADA, -float(i.valor_rejeitado or 0)))]}


@router.post("/{piloto_id}/check")
def conferir_agora(piloto_id: int, _: Acesso = Depends(require_admin_plataforma), db: Session = Depends(get_db)) -> dict[str, Any]:
    t = _piloto(db, piloto_id)
    resultado = rec.conferir(db, t)
    db.commit()
    return {**_resumo(t), "recuperadas_agora": resultado["recuperadas"]}


@router.get("/{piloto_id}/package")
def pacote(piloto_id: int, _: Acesso = Depends(require_admin_plataforma), db: Session = Depends(get_db)) -> dict[str, Any]:
    """O pacote de correção só das AIH do piloto, no mesmo formato do pacote do relatório."""
    t = _piloto(db, piloto_id)
    corpo = montar_pacote(db, t.cnes, meses_carregados(db, t.cnes), referencia_padrao(),
                          so_aih={i.n_aih for i in t.itens if i.situacao != rec.RECUPERADA})
    return {"recorte": {"titulo": t.nome}, **corpo,
            "ressalva": "Pacote do piloto: só as AIH escolhidas. O resultado é conferido no DATASUS a cada mês novo."}
