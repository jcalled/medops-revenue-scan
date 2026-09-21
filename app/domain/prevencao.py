"""
Prevenção: das AIH que o SUS rejeitou, quais o FaturaSUS teria apontado antes do envio.

O motor mora no núcleo; aqui ficam o resultado por AIH e a leitura dele. Pegar é
a regra que cuida do motivo da rejeição disparar — não qualquer achado na conta.
E o que o arquivo público não deixa medir (profissional, CNS do paciente) não
entra como "não pegou": fica no seu grupo, para a conta ser honesta.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.domain.resumo import _lotes
from app.models import SihPrevention, SihRejection, SihRejectionReason

GRUPOS = {
    "PEGARIA": "Seria apontado antes do envio",
    "CONFERIVEL_NAO_PEGOU": "Conferível, ainda não apontado",
    "PRECISA_ARQUIVO": "Precisa do arquivo do hospital",
    "SEM_REGRA": "Sem regra no FaturaSUS",
    "GESTOR": "Bloqueio do gestor",
    "SEM_MOTIVO": "Sem motivo no ER",
}
LOTE = 500


class Motor(Protocol):
    def avaliar(self, aihs: list[dict[str, Any]]) -> dict[str, Any]: ...


def grupo_da_aih(motivos: list[dict[str, Any]]) -> str:
    """Da mais favorável à menos: um motivo pego basta; conferível sem regra disparada vem antes do não medível."""
    if any(m.get("pegaria") for m in motivos):
        return "PEGARIA"
    grupos = {m.get("grupo") for m in motivos}
    if "CONFERIVEL" in grupos:
        return "CONFERIVEL_NAO_PEGOU"
    if "PRECISA_ARQUIVO_DO_HOSPITAL" in grupos:
        return "PRECISA_ARQUIVO"
    if "SEM_REGRA" in grupos:
        return "SEM_REGRA"
    if "BLOQUEIO_DO_GESTOR" in grupos:
        return "GESTOR"
    return "SEM_MOTIVO"


def avaliar_competencia(db: Session, uf: str, competencia: str, motor: Motor) -> dict[str, int]:
    """Manda as AIH rejeitadas da UF e competência ao FaturaSUS, em lotes, e guarda o resultado (substitui o anterior)."""
    rejeicoes = db.execute(
        select(SihRejection).where(SihRejection.uf == uf, SihRejection.competencia == competencia)
    ).scalars().all()
    # Carga anterior à prevenção não guardou os campos: recarregar o mês resolve.
    com_campos = [r for r in rejeicoes if r.campos]
    motivos: dict[str, set[str]] = defaultdict(set)
    for n_aih, codigo in db.execute(
        select(SihRejectionReason.n_aih, SihRejectionReason.codigo_erro)
        .where(SihRejectionReason.uf == uf, SihRejectionReason.competencia == competencia)
    ):
        motivos[n_aih].add(codigo)

    db.execute(delete(SihPrevention).where(SihPrevention.uf == uf, SihPrevention.competencia == competencia))
    agora = datetime.now(timezone.utc)
    avaliadas = pegaria = 0
    for inicio in range(0, len(com_campos), LOTE):
        lote = com_campos[inicio:inicio + LOTE]
        resposta = motor.avaliar([
            {"n_aih": r.n_aih, "motivos": sorted(motivos.get(r.n_aih, set())), "campos": r.campos,
             "diarias_antes": r.diarias_antes or 0, "diarias_uti_antes": r.diarias_uti_antes or 0}
            for r in lote
        ])
        por_aih = {str(x.get("n_aih")): x for x in resposta["resultados"]}
        linhas = []
        for r in lote:
            resultado = por_aih.get(r.n_aih)
            if resultado is None:
                continue
            lista = resultado.get("motivos") or []
            grupo = grupo_da_aih(lista)
            linhas.append({
                "uf": uf, "competencia": competencia, "cnes": r.cnes, "n_aih": r.n_aih, "grupo": grupo,
                "pegaria": grupo == "PEGARIA", "motivos": lista, "falhas": resultado.get("falhas") or [],
                "avisos": resultado.get("avisos") or [], "mensagens": resultado.get("mensagens") or [],
                "referencias": resposta.get("referencias") or {}, "avaliado_em": agora,
            })
            avaliadas += 1
            pegaria += grupo == "PEGARIA"
        if linhas:
            db.execute(insert(SihPrevention), linhas)
    db.commit()
    return {"avaliadas": avaliadas, "pegaria": pegaria, "sem_campos": len(rejeicoes) - len(com_campos)}


def _json(p: SihPrevention) -> dict[str, Any]:
    return {
        "grupo": p.grupo,
        "grupo_nome": GRUPOS.get(p.grupo, p.grupo),
        "pegaria": p.pegaria,
        "regras": sorted({r for m in p.motivos or [] if m.get("pegaria") for r in m.get("regras") or []}),
        "motivos": p.motivos or [],
        "falhas": p.falhas or [],
        "avisos": p.avisos or [],
        "mensagens": p.mensagens or [],
        "referencias": p.referencias or {},
        "avaliado_em": p.avaliado_em.isoformat() if p.avaliado_em else None,
        "metodo": "RETROSPECTIVO",
    }


def prevencao_por_aih(db: Session, chaves: list[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Resultado por (AIH, competência de processamento)."""
    quero = set(chaves)
    saida: dict[tuple[str, str], dict[str, Any]] = {}
    competencias = sorted({c for _, c in quero})
    for lote in _lotes(sorted({n for n, _ in quero})):
        for p in db.execute(
            select(SihPrevention).where(SihPrevention.n_aih.in_(lote), SihPrevention.competencia.in_(competencias))
        ).scalars():
            if (p.n_aih, p.competencia) in quero:
                saida[(p.n_aih, p.competencia)] = _json(p)
    return saida


def resumo_prevencao(db: Session, cnes: list[str], meses: list[str]) -> dict[str, Any] | None:
    """
    Das AIH rejeitadas dos hospitais no período (a última rejeição de cada uma),
    quanto o FaturaSUS teria pegado antes do envio. Conta a rejeição evitada, mesmo
    que a AIH tenha voltado aprovada depois: evitar é não passar pelo retrabalho.
    """
    ultimas: dict[str, tuple[str, float]] = {}
    for lote in _lotes(list(cnes)):
        for n_aih, competencia, valor in db.execute(
            select(SihRejection.n_aih, SihRejection.competencia, SihRejection.valor)
            .where(SihRejection.cnes.in_(lote), SihRejection.competencia.in_(meses))
        ):
            if n_aih not in ultimas or competencia >= ultimas[n_aih][0]:
                ultimas[n_aih] = (competencia, float(valor or 0))
    if not ultimas:
        return None
    resultados = prevencao_por_aih(db, [(n, c) for n, (c, _) in ultimas.items()])
    if not resultados:
        return None

    grupos = {g: {"aih": 0, "valor": 0.0} for g in GRUPOS}
    nao_avaliadas = {"aih": 0, "valor": 0.0}
    todos_motivos = {"aih": 0, "valor": 0.0}
    regras: Counter[str] = Counter()
    for n_aih, (competencia, valor) in ultimas.items():
        r = resultados.get((n_aih, competencia))
        alvo = grupos[r["grupo"]] if r else nao_avaliadas
        alvo["aih"] += 1
        alvo["valor"] += valor
        if r and r["pegaria"]:
            regras.update(r["regras"])
            if r["motivos"] and all(m.get("pegaria") for m in r["motivos"]):
                todos_motivos["aih"] += 1
                todos_motivos["valor"] += valor

    def arredondar(d: dict[str, float]) -> dict[str, float]:
        return {"aih": d["aih"], "valor": round(d["valor"], 2)}

    pegou, nao_pegou = grupos["PEGARIA"], grupos["CONFERIVEL_NAO_PEGOU"]
    conferivel = {"aih": pegou["aih"] + nao_pegou["aih"], "valor": pegou["valor"] + nao_pegou["valor"]}
    return {
        "metodo": "RETROSPECTIVO",
        "validacao_prospectiva": False,
        "leitura": "Reexecução de regras sobre AIH já rejeitadas. Detectar ao menos um motivo não garante evitar todos os impedimentos nem receber o valor da AIH. Não mede falsos positivos em contas aprovadas.",
        "grupos": {g: {"nome": GRUPOS[g], **arredondar(v)} for g, v in grupos.items()},
        "pegaria": arredondar(pegou),
        "todos_motivos_detectados": arredondar(todos_motivos),
        "conferivel": arredondar(conferivel),
        "taxa_conferivel": round(pegou["aih"] / conferivel["aih"], 4) if conferivel["aih"] else None,
        "taxa_conferivel_valor": round(pegou["valor"] / conferivel["valor"], 4) if conferivel["valor"] else None,
        "rejeitadas": {"aih": len(ultimas), "valor": round(sum(v for _, v in ultimas.values()), 2)},
        "nao_avaliadas": arredondar(nao_avaliadas),
        "regras_que_pegaram": [{"regra": r, "aih": n} for r, n in regras.most_common(6)],
    }
