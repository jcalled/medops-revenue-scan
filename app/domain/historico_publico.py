"""Antes/depois observado nos arquivos públicos, sem atribuir autoria da correção."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DataLoad, SihApprovedAih, SihRejection, SihRejectionReason

CAMPOS = {"valor": "Valor publicado", "procedimento": "Procedimento realizado",
          "competencia_aih": "Competência da AIH", "dt_internacao": "Data de internação", "dt_saida": "Data de alta"}


def historico_publico(db: Session, cnes: str, n_aih: str, meses: list[str]) -> dict[str, Any] | None:
    rejeicoes = list(db.execute(select(SihRejection).where(
        SihRejection.cnes == cnes, SihRejection.n_aih == n_aih, SihRejection.competencia.in_(meses),
    )).scalars())
    if not rejeicoes:
        return None
    uf = rejeicoes[0].uf
    aprovadas = list(db.execute(select(SihApprovedAih).where(
        SihApprovedAih.cnes == cnes, SihApprovedAih.n_aih == n_aih, SihApprovedAih.uf == uf,
        SihApprovedAih.competencia >= meses[0],
    )).scalars())
    motivos = defaultdict(list)
    for mes, codigo in db.execute(select(SihRejectionReason.competencia, SihRejectionReason.codigo_erro).where(
        SihRejectionReason.cnes == cnes, SihRejectionReason.n_aih == n_aih, SihRejectionReason.uf == uf,
        SihRejectionReason.competencia.in_(meses),
    )):
        motivos[mes].append(codigo)
    fontes = {}
    competencias = sorted({r.competencia for r in rejeicoes + aprovadas})
    for c in db.execute(select(DataLoad).where(
        DataLoad.uf == uf, DataLoad.status == "OK", DataLoad.competencia.in_(competencias),
        DataLoad.fonte.in_(["SIH_RD", "SIH_RJ", "SIH_ER"]),
    ).order_by(DataLoad.iniciado_em, DataLoad.id)).scalars():
        fontes[(c.fonte, c.competencia)] = {"arquivo": c.arquivo, "sha256": c.checksum}
    eventos = []
    for r in rejeicoes:
        eventos.append({"competencia": r.competencia, "situacao": "REJEITADA", "fonte": fontes.get(("SIH_RJ", r.competencia)),
                        "fonte_motivos": fontes.get(("SIH_ER", r.competencia)), "motivos": sorted(motivos[r.competencia]),
                        "campos": {"valor": float(r.valor) if r.valor is not None else None,
                                   "procedimento": r.proc_realizado, "competencia_aih": r.competencia_aih,
                                   "dt_internacao": r.dt_internacao.isoformat() if r.dt_internacao else None,
                                   "dt_saida": r.dt_saida.isoformat() if r.dt_saida else None}})
    for a in aprovadas:
        eventos.append({"competencia": a.competencia, "situacao": "APROVADA", "fonte": fontes.get(("SIH_RD", a.competencia)),
                        "fonte_motivos": None, "motivos": [],
                        "campos": {**(a.campos_publicos or {}), "valor": float(a.valor) if a.valor is not None else None}})
    eventos.sort(key=lambda e: (e["competencia"], e["situacao"] == "APROVADA"))
    anterior = None
    alteracoes = 0
    for evento in eventos:
        mudancas = []
        if anterior and evento["competencia"] > anterior["competencia"]:
            for campo, nome in CAMPOS.items():
                antes, depois = anterior["campos"].get(campo), evento["campos"].get(campo)
                if antes is not None and depois is not None and antes != depois:
                    mudancas.append({"campo": campo, "nome": nome, "antes": antes, "depois": depois,
                                     "competencia_antes": anterior["competencia"]})
        evento["mudancas"] = mudancas
        alteracoes += len(mudancas)
        anterior = evento
    return {
        "cnes": cnes, "n_aih": n_aih, "eventos": eventos, "alteracoes_observadas": alteracoes,
        "autoria_da_correcao": "NAO_DISPONIVEL_NOS_DADOS_PUBLICOS",
        "leitura": "Diferenças observadas entre processamentos, não histórico de edição do hospital. O SUS não informa aqui quem alterou, quando editou ou se a mudança causou a aprovação. Meses iguais não estabelecem ordem de alteração. Campos ausentes em cargas antigas não são tratados como mudança; recarregue o RD para ampliar a comparação.",
    }
