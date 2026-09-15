"""Dossiê comercial público: fatos, potencial e limites, sem tratativas privadas."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.kit import CLASSES, RECUPERAVEIS, classificar
from app.adapters.evidence_archive import caminho_preservado
from app.domain.recuperacao import FONTE_PRAZO
from app.domain.prova import soma
from app.models import DataLoad, Establishment


def dossie(db: Session, prova: dict[str, Any], referencia: str) -> dict[str, Any]:
    linhas = []
    grupos = defaultdict(list)
    for original in prova["linhas"]:
        classe, prazo = classificar(original, referencia)
        linha = {**original, "classe": classe, "classe_nome": CLASSES[classe], "prazo_estimado": prazo}
        linhas.append(linha)
        grupos[classe].append(linha)
    potenciais = [l for l in linhas if l["classe"] in RECUPERAVEIS]
    meses = prova["periodo"]["competencias"]
    hospital = db.get(Establishment, prova["hospital"]["cnes"])
    uf = hospital.uf if hospital else None
    # Manifesto das cargas efetivamente disponíveis, inclusive RD posterior ao recorte.
    cargas = list(db.execute(select(DataLoad).where(
        DataLoad.uf == uf, DataLoad.fonte.in_(["SIH_RD", "SIH_RJ", "SIH_ER"]),
        DataLoad.status == "OK", DataLoad.competencia >= meses[0],
    ).order_by(DataLoad.iniciado_em)).scalars()) if uf else []
    manifestos = {}
    for c in cargas:
        original = caminho_preservado(c.checksum)
        manifestos[(c.fonte, c.competencia)] = {
            "fonte": c.fonte, "competencia": c.competencia, "arquivo": c.arquivo,
            "sha256": c.checksum, "carregado_em": c.iniciado_em.isoformat() if c.iniciado_em else None,
            "original_preservado": bool(original and original.is_file()),
        }
    faltantes = [{"fonte": f, "competencia": m} for m in meses
                 for f in ("SIH_RD", "SIH_RJ", "SIH_ER") if (f, m) not in manifestos]
    rd = sorted(m for f, m in manifestos if f == "SIH_RD" and m)
    return {
        "hospital": prova["hospital"], "periodo": prova["periodo"], "referencia": referencia,
        "gerado_em": datetime.now(timezone.utc).isoformat(), "classe_dado": "PUBLICO",
        "rejeicao_documentada": soma(linhas),
        "potencial_no_prazo": soma(potenciais),
        "aprovacao_localizada": soma(grupos["JA_RECEBIDA"]),
        "fora_janela": soma(grupos["PRAZO_VENCIDO"]),
        "a_validar": soma(grupos["INVESTIGAR"] + grupos["GESTOR"]),
        "classes": {c: soma(grupos[c]) for c in CLASSES},
        "prevencao": prova["prevencao"],
        "cobertura": {"uf": uf, "ultimo_rd": rd[-1] if rd else None,
                      "cargas_ausentes": faltantes, "arquivos": list(manifestos.values())},
        "prazo": {"meses": 6, "fonte": FONTE_PRAZO, "artigo": "Art. 401, § 2º",
                  "leitura": "Janela estimada para reapresentação de AIH já apresentada e rejeitada ou bloqueada. Contada da alta; confirmar calendário e enquadramento com o gestor. Apresentação inicial: regra distinta de quatro meses."},
        "limites": [
            "Os valores são os das rejeições, contadas uma vez por AIH pela última rejeição no período. Os cartões de situação repartem esse total; prevenção se sobrepõe a ele e não deve ser somada.",
            "Potencial no prazo é uma triagem por motivo e data, não crédito reconhecido: depende de prontuário, autorizações, regras da competência e validação do faturamento.",
            "Aprovação no RD demonstra produção aprovada, não recebimento bancário nem resultado atribuível à MedOps. O cartão usa o valor rejeitado dessas AIH para fechar a partição do total.",
            "Ausência de aprovação vale apenas para a base carregada. Dados públicos têm atraso; cargas ausentes reduzem a cobertura.",
            "Prevenção retrospectiva testa regras em rejeições conhecidas. Validação preditiva exige alertas anteriores ao envio, regras e referências versionadas e comparação posterior com aprovadas e rejeitadas, incluindo falsos positivos.",
            "Oportunidades por comparação com outros hospitais não são crédito recuperável. Farmácia e produção nunca enviada exigem dados internos via Connect; consumo não implica cobrança adicional no SUS.",
        ],
        "linhas": sorted(linhas, key=lambda l: (-l["valor"], l["n_aih"])),
    }
