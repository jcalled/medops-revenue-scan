"""
Kits por motivo de rejeição e a situação do trabalho de cada AIH.

O kit é o manual de correção de um código do SIH, igual para todos os
clientes. A classe dele só muda a classificação das AIH depois de CONFIRMADA;
até lá a AIH segue a classe do tipo de rejeição, como sempre — os números
validados não mudam por uma leitura ainda não confirmada.

Com mais de um motivo, a AIH só volta se todos forem resolvidos: quando algum
tem kit confirmado, vale o motivo mais difícil.

A situação (a fazer, em correção, corrigida, reapresentada, sem como) é o que o
faturamento declara. O que o SUS confirma é voltar aprovada no RD, e isso o kit
já mostra como "já recebida".
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.domain.resumo import _lotes
from app.engine.categorias import categorizar
from app.models import AihTreatment, AihTreatmentEvent, MotiveKit, SihErrorCode, SihRejection, SihRejectionReason

# Da mais fácil para a mais difícil de receber.
DIFICULDADE = ("ALTA", "MEDIA", "INCERTA", "INVESTIGAR", "GESTOR", "PRAZO_VENCIDO", "JA_RECEBIDA")

CLASSE_POR_CATEGORIA = {
    "PROFISSIONAL": "ALTA", "PACIENTE": "ALTA", "REGRAS_SIGTAP": "ALTA",
    "LEITO_CNES": "MEDIA", "HABILITACAO_SERVICO": "MEDIA",
    "CAPACIDADE": "INCERTA",
    "PRAZO": "PRAZO_VENCIDO",
    "ADMINISTRATIVO": "GESTOR",
    "OUTROS": "INVESTIGAR",
}
ONDE = {
    "CNES": "CNES",
    "SISAIH01": "Conta da AIH (SISAIH01)",
    "PRONTUARIO": "Prontuário",
    "SESA": "Secretaria de Saúde (gestor)",
    "NENHUM": "Nada a corrigir",
}
REVISOES = {"A_CONFIRMAR": "Proposta, a confirmar", "CONFIRMADO": "Confirmado"}
SITUACOES = {
    "A_FAZER": "A fazer",
    "EM_CORRECAO": "Em correção",
    "CORRIGIDA": "Corrigida, falta reapresentar",
    "REAPRESENTADA": "Reapresentada",
    "SEM_COMO": "Não tem como recuperar",
}
_COMPETENCIA = re.compile(r"^\d{6}$")


class TratativaInvalida(ValueError):
    pass


def classe_do_codigo(codigo: str, confirmados: dict[str, str]) -> str:
    return confirmados.get(codigo) or CLASSE_POR_CATEGORIA.get(categorizar([codigo]).codigo, "INVESTIGAR")


def classe_pelos_kits(codigos: list[str], confirmados: dict[str, str]) -> str | None:
    """A classe da AIH quando algum motivo dela tem kit confirmado; senão None."""
    if not any(c in confirmados for c in codigos):
        return None
    return max((classe_do_codigo(c, confirmados) for c in codigos), key=DIFICULDADE.index)


def classes_confirmadas(db: Session) -> dict[str, str]:
    return dict(db.execute(select(MotiveKit.codigo, MotiveKit.classe).where(MotiveKit.revisao == "CONFIRMADO")).all())


def kit_json(k: MotiveKit, uso: tuple[int, float] | None = None, descricao: str | None = None) -> dict[str, Any]:
    return {
        "codigo": k.codigo,
        "descricao_oficial": descricao,
        "titulo": k.titulo,
        "significado": k.significado,
        "classe": k.classe,
        "onde_corrigir": k.onde_corrigir,
        "onde_nome": ONDE.get(k.onde_corrigir, k.onde_corrigir),
        "passos": list(k.passos or []),
        "dados_do_hospital": list(k.dados_do_hospital or []),
        "evidencias": list(k.evidencias or []),
        "prevencao": k.prevencao,
        "fonte": k.fonte,
        "revisao": k.revisao,
        "revisao_nome": REVISOES.get(k.revisao, k.revisao),
        "atualizado_em": k.atualizado_em.isoformat() if k.atualizado_em else None,
        "rejeicoes": uso[0] if uso else 0,
        "valor": round(uso[1], 2) if uso else 0.0,
    }


def kits_usados(db: Session, codigos: set[str]) -> dict[str, dict[str, Any]]:
    if not codigos:
        return {}
    return {k.codigo: kit_json(k) for k in db.execute(
        select(MotiveKit).where(MotiveKit.codigo.in_(sorted(codigos)))).scalars()}


def uso_por_motivo(db: Session, ufs: list[str] | None = None) -> dict[str, tuple[int, float]]:
    """Rejeições e valor rejeitado por motivo nos dados carregados."""
    consulta = (
        select(SihRejectionReason.codigo_erro, func.count(), func.coalesce(func.sum(SihRejection.valor), 0))
        .join(SihRejection, and_(SihRejection.uf == SihRejectionReason.uf,
                                 SihRejection.competencia == SihRejectionReason.competencia,
                                 SihRejection.n_aih == SihRejectionReason.n_aih))
        .group_by(SihRejectionReason.codigo_erro)
    )
    if ufs:
        consulta = consulta.where(SihRejectionReason.uf.in_(ufs))
    return {codigo: (int(n), float(valor or 0)) for codigo, n, valor in db.execute(consulta)}


def catalogo(db: Session, ufs: list[str] | None = None, limite_sem_kit: int = 50) -> dict[str, Any]:
    uso = uso_por_motivo(db, ufs)
    descricoes = dict(db.execute(select(SihErrorCode.codigo, SihErrorCode.descricao)).all())
    todos = list(db.execute(select(MotiveKit)).scalars())
    com_kit = {k.codigo for k in todos}
    total = sum(valor for _, valor in uso.values())
    sem_kit = sorted(
        ({"codigo": c, "descricao": descricoes.get(c), "rejeicoes": n, "valor": round(v, 2)}
         for c, (n, v) in uso.items() if c not in com_kit),
        key=lambda s: (-s["valor"], s["codigo"]),
    )
    coberto = sum(v for c, (_, v) in uso.items() if c in com_kit)
    return {
        "kits": sorted((kit_json(k, uso.get(k.codigo), descricoes.get(k.codigo)) for k in todos),
                       key=lambda k: (-k["valor"], k["codigo"])),
        "sem_kit": sem_kit[:limite_sem_kit],
        "sem_kit_total": len(sem_kit),
        "sem_kit_valor": round(sum(s["valor"] for s in sem_kit), 2),
        "valor_rejeitado": round(total, 2),
        "cobertura_valor": round(coberto / total, 4) if total else None,
        "confirmados": sum(1 for k in todos if k.revisao == "CONFIRMADO"),
    }


def salvar_kit(db: Session, codigo: str, dados: dict[str, Any], usuario: int | None) -> MotiveKit:
    kit = db.get(MotiveKit, codigo) or MotiveKit(codigo=codigo)
    for campo, valor in dados.items():
        setattr(kit, campo, [v.strip() for v in valor if v.strip()] if isinstance(valor, list) else valor)
    kit.atualizado_por = usuario
    kit.atualizado_em = datetime.now(timezone.utc)
    db.add(kit)
    db.commit()
    return kit


def tratativas(db: Session, n_aihs: list[str]) -> dict[str, AihTreatment]:
    saida: dict[str, AihTreatment] = {}
    for lote in _lotes(sorted(set(n_aihs))):
        for t in db.execute(select(AihTreatment).where(AihTreatment.n_aih.in_(lote))).scalars():
            saida[t.n_aih] = t
    return saida


def tratativa_json(t: AihTreatment | None) -> dict[str, Any] | None:
    if t is None:
        return None
    return {
        "situacao": t.situacao,
        "situacao_nome": SITUACOES.get(t.situacao, t.situacao),
        "competencia_reapresentacao": t.competencia_reapresentacao,
        "justificativa": t.justificativa,
        "responsavel": t.responsavel,
        "atualizado_por": t.atualizado_por,
        "atualizado_em": t.atualizado_em.isoformat() if t.atualizado_em else None,
    }


def registrar_tratativa(db: Session, n_aih: str, cnes: str, *, situacao: str, competencia_reapresentacao: str | None,
                        justificativa: str | None, responsavel: str | None, usuario: int | None,
                        tenant_id: int | None) -> AihTreatment:
    if situacao not in SITUACOES:
        raise TratativaInvalida(f"Situação desconhecida. Use {', '.join(SITUACOES)}.")
    justificativa = (justificativa or "").strip() or None
    responsavel = (responsavel or "").strip() or None
    competencia_reapresentacao = (competencia_reapresentacao or "").strip() or None
    if competencia_reapresentacao and not _COMPETENCIA.match(competencia_reapresentacao):
        raise TratativaInvalida("O mês da reapresentação vai como AAAAMM.")
    if situacao == "REAPRESENTADA" and not competencia_reapresentacao:
        raise TratativaInvalida("Informe o mês (AAAAMM) em que a AIH foi reapresentada.")
    if situacao == "SEM_COMO" and not justificativa:
        raise TratativaInvalida("Diga por que não tem como recuperar: a justificativa fica no histórico.")

    t = db.get(AihTreatment, n_aih) or AihTreatment(n_aih=n_aih, cnes=cnes)
    t.situacao, t.competencia_reapresentacao = situacao, competencia_reapresentacao
    t.justificativa, t.responsavel = justificativa, responsavel
    t.tenant_id, t.atualizado_por = tenant_id, usuario
    t.atualizado_em = datetime.now(timezone.utc)
    db.add(t)
    db.add(AihTreatmentEvent(n_aih=n_aih, situacao=situacao, competencia_reapresentacao=competencia_reapresentacao,
                             justificativa=justificativa, responsavel=responsavel, tenant_id=tenant_id,
                             criado_por=usuario))
    db.commit()
    return t


def historico(db: Session, n_aih: str) -> list[dict[str, Any]]:
    return [
        {"situacao": e.situacao, "situacao_nome": SITUACOES.get(e.situacao, e.situacao),
         "competencia_reapresentacao": e.competencia_reapresentacao, "justificativa": e.justificativa,
         "responsavel": e.responsavel, "criado_por": e.criado_por,
         "criado_em": e.criado_em.isoformat() if e.criado_em else None}
        for e in db.execute(select(AihTreatmentEvent).where(AihTreatmentEvent.n_aih == n_aih)
                            .order_by(AihTreatmentEvent.id.desc())).scalars()
    ]
