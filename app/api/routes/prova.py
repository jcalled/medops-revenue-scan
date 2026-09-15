"""
A prova de um hospital, AIH por AIH: a lista que o faturamento da organização
confere no próprio SIH. A regra de cada linha está em app/domain/prova.py.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Acesso, require_revenue_scan
from app.api.routes.organizacoes import _escopo
from app.api.routes.scan import _hospital_no_escopo, _ultimos_scores
from app.db import get_db
from app.domain.prevencao import prevencao_por_aih, resumo_prevencao
from app.domain.prova import SITUACOES, aih_rejeitadas, competencias_entre, confirmado_por_mes, soma
from app.domain.resumo import _nomes
from app.engine.categorias import POR_CODIGO
from app.engine.motor import CONFIRMADA
from app.models import Opportunity

router = APIRouter(prefix="/api/revenue-scan", tags=["prova"])

_CNES = re.compile(r"^\d{1,7}$")


@router.get("/hospitals/{cnes}/evidencias")
def evidencias_publicas(
    cnes: str,
    referencia: str | None = Query(default=None, pattern=r"^\d{4}(0[1-9]|1[0-2])$"),
    acesso: Acesso = Depends(require_revenue_scan),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    from app.domain.evidencias import dossie
    from app.domain.kit import referencia_padrao

    prova = listar_aih_rejeitadas(cnes=cnes, competencia=None, situacao=None, acesso=acesso, db=db)
    return dossie(db, prova, referencia or referencia_padrao())


@router.get("/hospitals/{cnes}/aih-rejeitadas")
def listar_aih_rejeitadas(
    cnes: str,
    competencia: str | None = Query(default=None, description="Mês de processamento AAAAMM"),
    situacao: str | None = Query(default=None, description=", ".join(SITUACOES)),
    acesso: Acesso = Depends(require_revenue_scan),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not _CNES.match(cnes):
        raise HTTPException(status_code=404, detail="Hospital não encontrado")
    numero = cnes.zfill(7)
    if not _hospital_no_escopo(db, acesso, numero):
        raise HTTPException(status_code=404, detail="Hospital não encontrado")
    if situacao and situacao not in SITUACOES:
        raise HTTPException(status_code=422, detail=f"Situação desconhecida. Use {', '.join(SITUACOES)}.")
    _, _, limite = _escopo(acesso)
    score = _ultimos_scores(db, [numero], limite).get(numero)
    if score is None:
        raise HTTPException(status_code=404, detail="Hospital sem scan calculado no período do contrato")
    meses = competencias_entre(score.periodo_inicio, score.periodo_fim)
    if competencia and competencia not in meses:
        raise HTTPException(status_code=422, detail=f"Competência fora do período analisado ({meses[0]} a {meses[-1]}).")

    linhas = aih_rejeitadas(db, [numero], meses)
    por_categoria: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for linha in linhas:
        if linha["situacao"] == "RECUPERAR":
            por_categoria[linha["categoria"]].append(linha)
    # Quanto de cada categoria ficou acima dos semelhantes: é o que vai para o híbrido.
    acima = dict(db.execute(
        select(Opportunity.categoria, Opportunity.estimated_financial_impact)
        .filter_by(cnes=numero, periodo_inicio=score.periodo_inicio, periodo_fim=score.periodo_fim, status=CONFIRMADA)
    ).all())

    filtradas = [l for l in linhas if (not competencia or l["competencia"] == competencia)
                 and (not situacao or l["situacao"] == situacao)]
    filtradas.sort(key=lambda l: (-l["valor"], l["n_aih"]))
    # O que o FaturaSUS diz de cada AIH, quando a prevenção já rodou para o mês.
    prevencao = prevencao_por_aih(db, [(l["n_aih"], l["competencia"]) for l in filtradas])
    for linha in filtradas:
        linha["prevencao"] = prevencao.get((linha["n_aih"], linha["competencia"]))

    return {
        "hospital": {"cnes": numero, "nome": _nomes(db, [numero]).get(numero)},
        "periodo": {"inicio": score.periodo_inicio, "fim": score.periodo_fim, "competencias": meses},
        "totais": {**{s: soma([l for l in linhas if l["situacao"] == s]) for s in SITUACOES}, "todas": soma(linhas)},
        "recuperar_por_categoria": sorted(
            ({"categoria": c, "nome": POR_CODIGO[c].nome, **soma(itens),
              "acima_dos_semelhantes": round(float(acima.get(c) or 0), 2)} for c, itens in por_categoria.items()),
            key=lambda x: -x["valor"]),
        "oportunidade_confirmada": float(score.impacto_confirmado or 0),
        "confirmado_por_mes": confirmado_por_mes(db, [score]).get(numero, {}),
        "prevencao": resumo_prevencao(db, [numero], meses),
        "leitura": (
            "Soma das AIH que entram na recuperação é o teto: valor bruto de rejeições potencialmente corrigíveis sem aprovação localizada, ainda sujeito a prazo e validação. "
            "A oportunidade confirmada, usada no modelo híbrido, é só a parte dessa perda acima do que hospitais "
            "semelhantes perdem — a conta conservadora."
        ),
        "linhas": filtradas,
        "classe_dado": "PUBLICO",
        "fonte": "DATASUS SIH/SUS: arquivos RJ (AIH rejeitadas), ER (motivos) e RD (aprovadas) de cada processamento.",
    }
