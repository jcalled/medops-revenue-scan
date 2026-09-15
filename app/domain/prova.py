"""
A prova: cada AIH rejeitada, com o motivo do SUS e se entra na recuperação — e
por quê.

É o que sustenta o número na reunião. Tudo o que o modelo híbrido cobra tem que
aparecer aqui AIH a AIH: a oportunidade confirmada de uma categoria nunca passa
da soma das AIH dela marcadas para recuperar, e o valor de um mês nunca passa
das AIH marcadas naquele mês.

Situação de cada AIH (a última rejeição dela no período):
- RECUPERAR: motivo que o hospital corrige (CNES, capacidade, prazo, regras do
  SIGTAP, profissional, dados do paciente) e não voltou aprovada em nenhum
  processamento carregado;
- FORA_DO_ALCANCE: bloqueio do gestor ou motivo sem correção definida;
- JA_RECEBIDA: voltou aprovada depois — há aprovação registrada, sem prova de recebimento.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.resumo import _lotes
from app.engine.categorias import FORA_DA_RECUPERACAO, POR_CODIGO, categorizar
from app.models import DataLoad, HospitalScore, SihApprovedAih, SihErrorCode, SihRejection, SihRejectionReason

SITUACOES = {
    "RECUPERAR": "Potencial de correção, a validar",
    "FORA_DO_ALCANCE": "Não entra",
    "JA_RECEBIDA": "Aprovação localizada no RD",
}


def competencias_entre(inicio: str, fim: str) -> list[str]:
    ano, mes = int(inicio[:4]), int(inicio[4:])
    saida = []
    while f"{ano}{mes:02d}" <= fim:
        saida.append(f"{ano}{mes:02d}")
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return saida


def _porque(situacao: str, categoria: str, motivos: list[dict[str, Any]]) -> str:
    principal = next((m for m in motivos if m["descricao"]), motivos[0] if motivos else None)
    if principal is None:
        texto_motivo = "motivo não informado no ER"
    elif principal["descricao"]:
        texto_motivo = f"{principal['codigo']} — {principal['descricao'].capitalize()}"
    else:
        texto_motivo = f"{principal['codigo']} (sem descrição na tabela oficial)"
    if situacao == "JA_RECEBIDA":
        return f"Rejeitada por {texto_motivo}, há aprovação no RD carregado. Aprovação não comprova recebimento; confira a cronologia."
    if categoria == "ADMINISTRATIVO":
        return (f"Bloqueio do gestor: {texto_motivo}. Depende de negociação com a secretaria (faixa de numeração, "
                "teto, auditoria) e não de correção da conta — fica fora da cobrança.")
    if categoria in FORA_DA_RECUPERACAO:
        return (f"Rejeitada por {texto_motivo}. Ainda sem regra de correção definida: revisar com o faturamento "
                "antes de contar como recuperável.")
    return f"Rejeitada por {texto_motivo}. {POR_CODIGO[categoria].acao}"


def aih_rejeitadas(db: Session, cnes: list[str], meses: list[str]) -> list[dict[str, Any]]:
    """Uma linha por AIH rejeitada dos hospitais no período, com situação, motivo e arquivo de origem."""
    ultimas: dict[str, SihRejection] = {}
    for lote in _lotes(cnes):
        for r in db.execute(
            select(SihRejection).where(SihRejection.cnes.in_(lote), SihRejection.competencia.in_(meses))
        ).scalars():
            atual = ultimas.get(r.n_aih)
            if atual is None or r.competencia >= atual.competencia:
                ultimas[r.n_aih] = r
    if not ultimas:
        return []

    aprovadas: set[str] = set()
    meses_aprovacao: dict[str, list[str]] = defaultdict(list)
    motivos: dict[tuple[str, str], set[str]] = defaultdict(set)
    for lote in _lotes(list(ultimas)):
        for n, mes in db.execute(select(SihApprovedAih.n_aih, SihApprovedAih.competencia)
                                 .where(SihApprovedAih.n_aih.in_(lote))):
            aprovadas.add(n)
            meses_aprovacao[n].append(mes)
        for n_aih, competencia, codigo in db.execute(
            select(SihRejectionReason.n_aih, SihRejectionReason.competencia, SihRejectionReason.codigo_erro)
            .where(SihRejectionReason.n_aih.in_(lote), SihRejectionReason.competencia.in_(meses))
        ):
            motivos[(n_aih, competencia)].add(codigo)
    descricoes = dict(db.execute(select(SihErrorCode.codigo, SihErrorCode.descricao)).all())

    # Arquivo do DATASUS de cada linha: a última carga OK da fonte, UF e competência.
    fontes: dict[tuple[str, str | None, str | None], dict[str, str | None]] = {}
    for carga in db.execute(
        select(DataLoad).where(DataLoad.fonte.in_(["SIH_RJ", "SIH_ER"]), DataLoad.status == "OK",
                               DataLoad.competencia.in_(meses)).order_by(DataLoad.iniciado_em)
    ).scalars():
        fontes[(carga.fonte, carga.uf, carga.competencia)] = {"arquivo": carga.arquivo, "sha256": carga.checksum}

    linhas = []
    for r in ultimas.values():
        codigos = sorted(motivos.get((r.n_aih, r.competencia), set()))
        categoria = categorizar(codigos)
        lista_motivos = [{"codigo": c, "descricao": descricoes.get(c)} for c in codigos]
        situacao = ("JA_RECEBIDA" if r.n_aih in aprovadas
                    else "FORA_DO_ALCANCE" if categoria.codigo in FORA_DA_RECUPERACAO else "RECUPERAR")
        linhas.append({
            "cnes": r.cnes,
            "n_aih": r.n_aih,
            "competencia": r.competencia,
            "competencia_aih": r.competencia_aih,
            "procedimento": r.proc_realizado,
            "valor": float(r.valor or 0),
            "dt_internacao": r.dt_internacao.isoformat() if r.dt_internacao else None,
            "dt_saida": r.dt_saida.isoformat() if r.dt_saida else None,
            "motivos": lista_motivos,
            "categoria": categoria.codigo,
            "categoria_nome": categoria.nome,
            "situacao": situacao,
            "situacao_nome": SITUACOES[situacao],
            "competencias_aprovacao": sorted(set(meses_aprovacao[r.n_aih])),
            "aprovacao_posterior": any(m > r.competencia for m in meses_aprovacao[r.n_aih]),
            "recebimento_comprovado": False,
            "porque": _porque(situacao, categoria.codigo, lista_motivos),
            "fonte": {"rejeicao": fontes.get(("SIH_RJ", r.uf, r.competencia)),
                      "motivo": fontes.get(("SIH_ER", r.uf, r.competencia))},
        })
    return linhas


def soma(linhas: list[dict[str, Any]]) -> dict[str, float]:
    return {"aih": len(linhas), "valor": round(sum(l["valor"] for l in linhas), 2)}


def confirmado_por_mes(db: Session, scores: list[HospitalScore]) -> dict[str, dict[str, float]]:
    """
    A oportunidade confirmada de cada hospital repartida pelos meses de processamento.

    Cada mês recebe a parte proporcional às AIH a recuperar processadas nele. Como
    a confirmada não passa do total dessas AIH, o valor do mês não passa das AIH
    marcadas naquele mês — dá para abrir a lista e somar.
    """
    por_periodo: dict[tuple[str, str], list[HospitalScore]] = defaultdict(list)
    for s in scores:
        por_periodo[(s.periodo_inicio, s.periodo_fim)].append(s)

    saida: dict[str, dict[str, float]] = {}
    for (inicio, fim), grupo in por_periodo.items():
        meses = competencias_entre(inicio, fim)
        recuperar: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for linha in aih_rejeitadas(db, [s.cnes for s in grupo], meses):
            if linha["situacao"] == "RECUPERAR":
                recuperar[linha["cnes"]][linha["competencia"]] += linha["valor"]
        for s in grupo:
            total = sum(recuperar[s.cnes].values())
            confirmada = min(float(s.impacto_confirmado or 0), total)
            saida[s.cnes] = {m: round(confirmada * recuperar[s.cnes].get(m, 0.0) / total, 2) if total else 0.0
                             for m in meses}
    return saida
