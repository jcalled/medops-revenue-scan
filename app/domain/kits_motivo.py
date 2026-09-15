"""
Kits por motivo de rejeição, a situação do trabalho de cada AIH e o quanto o
FaturaSUS vale em cada motivo.

O kit é o manual de correção de um código do SIH, igual para todos os
clientes. A classe dele só muda a classificação das AIH depois de CONFIRMADA;
até lá a AIH segue a classe do tipo de rejeição, como sempre — os números
validados não mudam por uma leitura ainda não confirmada.

Com mais de um motivo, a AIH só volta se todos forem resolvidos: quando algum
tem kit confirmado, vale o motivo mais difícil.

A situação (a fazer, em correção, corrigida, reapresentada, sem como) é o que o
faturamento declara. O que o SUS decide aparece nos meses seguintes: a AIH
reapresentada volta aprovada no RD ou rejeitada de novo no RJ.

FaturaSUS por motivo, duas medidas:
- antes do envio: das rejeições reais, em quantas a regra do motivo teria
  disparado (a prevenção de cada UF);
- depois da correção: das AIH que o faturamento marcou como "passou no
  FaturaSUS" e reapresentou, quantas o SUS aprovou. É o que diz quanto confiar
  no "passou" daquele motivo.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session

from app.domain.resumo import _lotes
from app.engine.categorias import categorizar
from app.models import (
    AihTreatment, AihTreatmentEvent, MotiveKit, MotivePreventionStat, SihApprovedAih, SihErrorCode, SihHospitalMonth,
    SihPrevention, SihRejection, SihRejectionReason,
)

# Da mais fácil para a mais difícil de receber.
DIFICULDADE = ("ALTA", "MEDIA", "INCERTA", "INVESTIGAR", "GESTOR", "PRAZO_VENCIDO", "JA_RECEBIDA")

CLASSE_POR_CATEGORIA = {
    "PROFISSIONAL": "ALTA", "PACIENTE": "ALTA", "REGRAS_SIGTAP": "ALTA",
    "LEITO_CNES": "MEDIA", "HABILITACAO_SERVICO": "MEDIA",
    "CAPACIDADE": "INCERTA",
    "PRAZO": "INVESTIGAR",
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
FATURASUS = {"PASSOU": "Passou no FaturaSUS", "NAO_PASSOU": "Não passou no FaturaSUS"}
RESULTADOS = {
    "APROVADA": "Aprovada pelo SUS",
    "REJEITADA_DE_NOVO": "Rejeitada de novo",
    "SEM_RETORNO": "Não apareceu no mês carregado",
    "AGUARDANDO_DADOS": "Aguardando os dados do mês",
}
GRUPOS_FATURASUS = {
    "CONFERIVEL": "O FaturaSUS confere com o dado público",
    "PRECISA_ARQUIVO_DO_HOSPITAL": "O FaturaSUS só confere com o arquivo do hospital",
    "BLOQUEIO_DO_GESTOR": "Não está na conta: o FaturaSUS não confere",
    "SEM_REGRA": "Sem regra no FaturaSUS",
}
LEITURA_GRUPO = {
    "CONFERIVEL": "O 'passou' vale na medida da taxa: quanto maior, mais confiável.",
    "PRECISA_ARQUIVO_DO_HOSPITAL": "Só vale passar com o SISAIH01 do hospital; o dado público não deixa medir.",
    "BLOQUEIO_DO_GESTOR": "Passar no FaturaSUS não diz nada: isto se resolve com a secretaria.",
    "SEM_REGRA": "Passar no FaturaSUS não diz nada: não há regra para este motivo.",
}
# Menos que isto com resultado do SUS, a taxa de acerto aparece marcada como amostra pequena.
AMOSTRA_CONFIABILIDADE = 10
_COMPETENCIA = re.compile(r"^\d{6}$")


class TratativaInvalida(ValueError):
    pass


# ── Classe pelos kits ──────────────────────────────────────────────────────

def classe_do_codigo(codigo: str, confirmados: dict[str, str]) -> str:
    return confirmados.get(codigo) or CLASSE_POR_CATEGORIA.get(categorizar([codigo]).codigo, "INVESTIGAR")


def classe_pelos_kits(codigos: list[str], confirmados: dict[str, str]) -> str | None:
    """A classe da AIH quando algum motivo dela tem kit confirmado; senão None."""
    if not any(c in confirmados for c in codigos):
        return None
    return max((classe_do_codigo(c, confirmados) for c in codigos), key=DIFICULDADE.index)


def classes_confirmadas(db: Session) -> dict[str, str]:
    return dict(db.execute(select(MotiveKit.codigo, MotiveKit.classe).where(MotiveKit.revisao == "CONFIRMADO")).all())


# ── FaturaSUS antes do envio: taxa medida na prevenção ─────────────────────

def atualizar_estatisticas_prevencao(db: Session, uf: str) -> int:
    """Refaz a taxa do FaturaSUS por motivo de uma UF a partir da prevenção gravada. Devolve quantos motivos."""
    por: dict[str, dict[str, Any]] = {}
    for (motivos,) in db.execute(select(SihPrevention.motivos).where(SihPrevention.uf == uf)):
        for m in motivos or []:
            p = por.setdefault(m["codigo"], {"grupo": None, "avaliadas": 0, "pegaria": 0, "regras": set()})
            p["grupo"] = p["grupo"] or m.get("grupo")
            p["avaliadas"] += 1
            p["pegaria"] += bool(m.get("pegaria"))
            p["regras"].update(m.get("regras") or [])
    db.execute(delete(MotivePreventionStat).where(MotivePreventionStat.uf == uf))
    agora = datetime.now(timezone.utc)
    db.add_all(MotivePreventionStat(uf=uf, codigo=codigo, grupo=p["grupo"], avaliadas=p["avaliadas"],
                                    pegaria=p["pegaria"], regras=sorted(p["regras"]), atualizado_em=agora)
               for codigo, p in por.items())
    db.commit()
    return len(por)


def estatisticas_prevencao(db: Session, ufs: list[str] | None = None) -> dict[str, dict[str, Any]]:
    consulta = select(MotivePreventionStat)
    if ufs:
        consulta = consulta.where(MotivePreventionStat.uf.in_(ufs))
    por: dict[str, dict[str, Any]] = {}
    for s in db.execute(consulta).scalars():
        p = por.setdefault(s.codigo, {"grupos": defaultdict(int), "avaliadas": 0, "pegaria": 0, "regras": set()})
        p["grupos"][s.grupo] += s.avaliadas
        p["avaliadas"] += s.avaliadas
        p["pegaria"] += s.pegaria
        p["regras"].update(s.regras or [])
    saida = {}
    for codigo, p in por.items():
        grupo = max(p["grupos"], key=p["grupos"].get)
        saida[codigo] = {
            "grupo": grupo,
            "grupo_nome": GRUPOS_FATURASUS.get(grupo, grupo),
            "leitura": LEITURA_GRUPO.get(grupo),
            "avaliadas": p["avaliadas"],
            "pegaria": p["pegaria"],
            "taxa": round(p["pegaria"] / p["avaliadas"], 4) if p["avaliadas"] else None,
            "regras_que_pegaram": sorted(p["regras"]),
        }
    return saida


# ── Depois da reapresentação: o que o SUS decidiu ──────────────────────────

def resultados(db: Session, tratamentos: list[AihTreatment]) -> dict[str, dict[str, Any]]:
    """
    Para cada AIH reapresentada: aprovada no RD a partir do mês da
    reapresentação, rejeitada de novo no RJ, sem retorno num mês já carregado
    ou aguardando os dados daquele mês. Leva os motivos da rejeição que foi
    trabalhada (a última antes da reapresentação).
    """
    reapresentadas = [t for t in tratamentos if t.situacao == "REAPRESENTADA" and t.competencia_reapresentacao]
    if not reapresentadas:
        return {}
    n_aihs = sorted({t.n_aih for t in reapresentadas})
    aprovacoes: dict[str, list[tuple[str, float]]] = defaultdict(list)
    rejeicoes: dict[str, list[tuple[str, str]]] = defaultdict(list)
    motivos: dict[tuple[str, str], set[str]] = defaultdict(set)
    for lote in _lotes(n_aihs):
        for n_aih, competencia, valor in db.execute(
            select(SihApprovedAih.n_aih, SihApprovedAih.competencia, SihApprovedAih.valor).where(SihApprovedAih.n_aih.in_(lote))
        ):
            aprovacoes[n_aih].append((competencia, float(valor or 0)))
        for n_aih, competencia, uf in db.execute(
            select(SihRejection.n_aih, SihRejection.competencia, SihRejection.uf).where(SihRejection.n_aih.in_(lote))
        ):
            rejeicoes[n_aih].append((competencia, uf))
        for n_aih, competencia, codigo in db.execute(
            select(SihRejectionReason.n_aih, SihRejectionReason.competencia, SihRejectionReason.codigo_erro)
            .where(SihRejectionReason.n_aih.in_(lote))
        ):
            motivos[(n_aih, competencia)].add(codigo)
    ultimo_por_uf = dict(db.execute(
        select(SihHospitalMonth.uf, func.max(SihHospitalMonth.competencia)).group_by(SihHospitalMonth.uf)).all())

    saida = {}
    for t in reapresentadas:
        mes = t.competencia_reapresentacao
        antes = sorted(r for r in rejeicoes[t.n_aih] if r[0] < mes)
        depois = sorted(r for r in rejeicoes[t.n_aih] if r[0] >= mes)
        aprovada = sorted(a for a in aprovacoes[t.n_aih] if a[0] >= mes)
        uf = (antes or depois or [(None, None)])[-1][1]
        corpo: dict[str, Any] = {"competencia": None, "valor": 0.0, "motivos": []}
        if aprovada:
            situacao = "APROVADA"
            corpo.update(competencia=aprovada[0][0], valor=round(aprovada[0][1], 2))
        elif depois:
            situacao = "REJEITADA_DE_NOVO"
            corpo.update(competencia=depois[-1][0], motivos=sorted(motivos.get((t.n_aih, depois[-1][0]), set())))
        elif uf and ultimo_por_uf.get(uf, "") >= mes:
            situacao = "SEM_RETORNO"
        else:
            situacao = "AGUARDANDO_DADOS"
        saida[t.n_aih] = {
            "situacao": situacao, "nome": RESULTADOS[situacao], **corpo,
            "motivos_originais": sorted(motivos.get((t.n_aih, antes[-1][0]), set())) if antes else [],
        }
    return saida


def confiabilidade_faturasus(db: Session) -> dict[str, dict[str, Any]]:
    """Por motivo da rejeição trabalhada: das AIH que passaram no FaturaSUS e foram reapresentadas, quantas o SUS aprovou."""
    tratamentos = list(db.execute(
        select(AihTreatment).where(AihTreatment.faturasus.is_not(None), AihTreatment.situacao == "REAPRESENTADA")
    ).scalars())
    retornos = resultados(db, tratamentos)
    por: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "passou": 0, "passou_aprovadas": 0, "passou_rejeitadas": 0,
        "nao_passou": 0, "nao_passou_aprovadas": 0, "nao_passou_rejeitadas": 0, "aguardando": 0,
    })
    for t in tratamentos:
        r = retornos.get(t.n_aih)
        if r is None:
            continue
        chave = "passou" if t.faturasus == "PASSOU" else "nao_passou"
        for codigo in r["motivos_originais"]:
            p = por[codigo]
            p[chave] += 1
            if r["situacao"] == "APROVADA":
                p[f"{chave}_aprovadas"] += 1
            elif r["situacao"] == "REJEITADA_DE_NOVO":
                p[f"{chave}_rejeitadas"] += 1
            else:
                p["aguardando"] += 1
    for p in por.values():
        p["decididas"] = p["passou_aprovadas"] + p["passou_rejeitadas"]
        p["taxa_acerto"] = round(p["passou_aprovadas"] / p["decididas"], 4) if p["decididas"] else None
        p["amostra_pequena"] = p["decididas"] < AMOSTRA_CONFIABILIDADE
    return dict(por)


# ── Kits ───────────────────────────────────────────────────────────────────

def kit_json(k: MotiveKit, uso: tuple[int, float] | None = None, descricao: str | None = None,
             faturasus: dict[str, Any] | None = None, confiabilidade: dict[str, Any] | None = None) -> dict[str, Any]:
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
        "faturasus": faturasus,
        "confiabilidade": confiabilidade,
    }


def kits_usados(db: Session, codigos: set[str], ufs: list[str] | None = None) -> dict[str, dict[str, Any]]:
    if not codigos:
        return {}
    kits = list(db.execute(select(MotiveKit).where(MotiveKit.codigo.in_(sorted(codigos)))).scalars())
    if not kits:
        return {}
    estatisticas = estatisticas_prevencao(db, ufs)
    confiabilidade = confiabilidade_faturasus(db)
    return {k.codigo: kit_json(k, faturasus=estatisticas.get(k.codigo), confiabilidade=confiabilidade.get(k.codigo))
            for k in kits}


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
    estatisticas = estatisticas_prevencao(db, ufs)
    confiabilidade = confiabilidade_faturasus(db)
    todos = list(db.execute(select(MotiveKit)).scalars())
    com_kit = {k.codigo for k in todos}
    total = sum(valor for _, valor in uso.values())
    sem_kit = sorted(
        ({"codigo": c, "descricao": descricoes.get(c), "rejeicoes": n, "valor": round(v, 2),
          "faturasus": estatisticas.get(c)}
         for c, (n, v) in uso.items() if c not in com_kit),
        key=lambda s: (-s["valor"], s["codigo"]),
    )
    coberto = sum(v for c, (_, v) in uso.items() if c in com_kit)
    return {
        "kits": sorted((kit_json(k, uso.get(k.codigo), descricoes.get(k.codigo), estatisticas.get(k.codigo),
                                 confiabilidade.get(k.codigo)) for k in todos),
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


# ── Situação de cada AIH ───────────────────────────────────────────────────

def tratativas(db: Session, n_aihs: list[str]) -> dict[str, AihTreatment]:
    saida: dict[str, AihTreatment] = {}
    for lote in _lotes(sorted(set(n_aihs))):
        for t in db.execute(select(AihTreatment).where(AihTreatment.n_aih.in_(lote))).scalars():
            saida[t.n_aih] = t
    return saida


def tratativa_json(t: AihTreatment | None, resultado: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if t is None:
        return None
    return {
        "situacao": t.situacao,
        "situacao_nome": SITUACOES.get(t.situacao, t.situacao),
        "competencia_reapresentacao": t.competencia_reapresentacao,
        "justificativa": t.justificativa,
        "responsavel": t.responsavel,
        "faturasus": t.faturasus,
        "faturasus_nome": FATURASUS.get(t.faturasus) if t.faturasus else None,
        "resultado": resultado,
        "atualizado_por": t.atualizado_por,
        "atualizado_em": t.atualizado_em.isoformat() if t.atualizado_em else None,
    }


def registrar_tratativa(db: Session, n_aih: str, cnes: str, *, situacao: str, competencia_reapresentacao: str | None,
                        justificativa: str | None, responsavel: str | None, faturasus: str | None = None,
                        usuario: int | None, tenant_id: int | None) -> AihTreatment:
    if situacao not in SITUACOES:
        raise TratativaInvalida(f"Situação desconhecida. Use {', '.join(SITUACOES)}.")
    justificativa = (justificativa or "").strip() or None
    responsavel = (responsavel or "").strip() or None
    competencia_reapresentacao = (competencia_reapresentacao or "").strip() or None
    faturasus = (faturasus or "").strip() or None
    if faturasus and faturasus not in FATURASUS:
        raise TratativaInvalida(f"FaturaSUS: use {', '.join(FATURASUS)} ou deixe vazio.")
    if competencia_reapresentacao and not _COMPETENCIA.match(competencia_reapresentacao):
        raise TratativaInvalida("O mês da reapresentação vai como AAAAMM.")
    if situacao == "REAPRESENTADA" and not competencia_reapresentacao:
        raise TratativaInvalida("Informe o mês (AAAAMM) em que a AIH foi reapresentada.")
    if situacao == "SEM_COMO" and not justificativa:
        raise TratativaInvalida("Diga por que não tem como recuperar: a justificativa fica no histórico.")
    if competencia_reapresentacao:
        primeira = db.scalar(select(func.min(SihRejection.competencia)).where(SihRejection.n_aih == n_aih))
        if primeira and competencia_reapresentacao <= primeira:
            raise TratativaInvalida(
                f"A reapresentação vem depois da rejeição: a AIH foi rejeitada no processamento {primeira}.")

    t = db.get(AihTreatment, n_aih) or AihTreatment(n_aih=n_aih, cnes=cnes)
    t.situacao, t.competencia_reapresentacao = situacao, competencia_reapresentacao
    t.justificativa, t.responsavel, t.faturasus = justificativa, responsavel, faturasus
    t.tenant_id, t.atualizado_por = tenant_id, usuario
    t.atualizado_em = datetime.now(timezone.utc)
    db.add(t)
    db.add(AihTreatmentEvent(n_aih=n_aih, situacao=situacao, competencia_reapresentacao=competencia_reapresentacao,
                             justificativa=justificativa, responsavel=responsavel, faturasus=faturasus,
                             tenant_id=tenant_id, criado_por=usuario))
    db.commit()
    return t


def historico(db: Session, n_aih: str) -> list[dict[str, Any]]:
    return [
        {"situacao": e.situacao, "situacao_nome": SITUACOES.get(e.situacao, e.situacao),
         "competencia_reapresentacao": e.competencia_reapresentacao, "justificativa": e.justificativa,
         "responsavel": e.responsavel, "faturasus": e.faturasus, "criado_por": e.criado_por,
         "criado_em": e.criado_em.isoformat() if e.criado_em else None}
        for e in db.execute(select(AihTreatmentEvent).where(AihTreatmentEvent.n_aih == n_aih)
                            .order_by(AihTreatmentEvent.id.desc())).scalars()
    ]
