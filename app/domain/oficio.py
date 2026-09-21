"""
Ofício do hospital à secretaria de saúde, montado com os números do DATASUS:

- pedido de habilitação (e de serviço/classificação no CNES) dos procedimentos que
  o hospital já realiza e o SIH rejeita por falta dela;
- cadastro ou habilitação dos leitos de UTI e UCI cobrados e rejeitados;
- pergunta formal sobre os motivos de rejeição sem descrição na tabela oficial;
- revisão da programação (teto) da APAC, com a produção acima do teto.

Conta toda a rejeição do período, inclusive a que voltou depois: o ofício trata do
problema que se repete, não só do que ainda dá para recuperar.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.domain.prova import aih_rejeitadas
from app.domain.resumo import _lotes, _nomes
from app.models import SiaApacMonth, SihErrorCode

HABILITACAO = frozenset({"060120", "050098"})
SERVICO = frozenset({"060072", "060055"})
LEITOS = {
    "060022": "UTI II neonatal", "060028": "UTI II adulto", "060185": "UCI neonatal convencional (UCINCo)",
    "060186": "UCI neonatal canguru (UCINCa)", "050008": "leito da especialidade",
}
PROCEDIMENTOS_NO_OFICIO = 15


def nomes_de_procedimentos(db: Session, codigos: set[str]) -> dict[str, str]:
    """Nome do procedimento no SIGTAP do núcleo (mesmo banco, schema public). Sem acesso, fica só o código."""
    if not codigos:
        return {}
    try:
        with db.begin_nested():
            linhas = db.execute(text(
                "SELECT codigo, descricao, competencia FROM public.ref_sigtap_procedimento WHERE codigo = ANY(:c)"
            ), {"c": sorted(codigos)}).all()
    except Exception:  # noqa: BLE001 — SQLite dos testes ou banco sem o SIGTAP do núcleo
        return {}
    saida: dict[str, tuple[str, str]] = {}
    for codigo, descricao, competencia in linhas:
        if codigo not in saida or competencia > saida[codigo][1]:
            saida[codigo] = (descricao, competencia)
    return {c: d for c, (d, _) in saida.items()}


def _soma() -> dict[str, float]:
    return {"aih": 0, "valor": 0.0}


def montar_oficio(db: Session, cnes: list[str], meses: list[str]) -> dict[str, Any]:
    linhas = aih_rejeitadas(db, cnes, meses)
    nomes = _nomes(db, cnes)
    sem_descricao_oficial = {c for (c,) in db.execute(select(SihErrorCode.codigo).where(
        (SihErrorCode.descricao.is_(None)) | (SihErrorCode.descricao == "")))}
    descritos = set(db.execute(select(SihErrorCode.codigo)).scalars())

    hospitais: dict[str, dict[str, Any]] = {}
    for l in linhas:
        h = hospitais.setdefault(l["cnes"], {
            "habilitacao": defaultdict(lambda: {**_soma(), "motivos": set()}), "leitos": defaultdict(_soma),
            "sem_descricao": defaultdict(_soma), "meses": set()})
        h["meses"].add(l["competencia"])
        codigos = {m["codigo"] for m in l["motivos"]}
        proc = l["procedimento"] or "—"
        # Habilitação e serviço juntos: a mesma AIH costuma ter os dois motivos e conta uma vez só.
        if codigos & (HABILITACAO | SERVICO):
            alvo = h["habilitacao"][proc]
            alvo["aih"] += 1
            alvo["valor"] += l["valor"]
            alvo["motivos"] |= codigos & (HABILITACAO | SERVICO)
        for codigo in codigos & set(LEITOS):
            h["leitos"][codigo]["aih"] += 1
            h["leitos"][codigo]["valor"] += l["valor"]
        # Sem a tabela oficial carregada, não há como saber o que falta descrever: não pergunta nada.
        for codigo in codigos if descritos else ():
            if codigo in sem_descricao_oficial or codigo not in descritos:
                h["sem_descricao"][codigo]["aih"] += 1
                h["sem_descricao"][codigo]["valor"] += l["valor"]

    apac: dict[str, dict[str, Any]] = {}
    for lote in _lotes(cnes):
        for a in db.execute(select(SiaApacMonth).where(SiaApacMonth.cnes.in_(lote), SiaApacMonth.competencia.in_(meses))).scalars():
            alvo = apac.setdefault(a.cnes, {"teto": 0.0, "produzido": 0.0, "procedimentos": defaultdict(float), "meses": set()})
            alvo["teto"] += float(a.valor_teto)
            alvo["produzido"] += float(a.valor_produzido)
            alvo["meses"].add(a.competencia)
            for p in a.procedimentos or []:
                alvo["procedimentos"][p["procedimento"]] += p["valor"]

    todos_procs = {p for h in hospitais.values() for p in h["habilitacao"]}
    todos_procs |= {p for a in apac.values() for p in a["procedimentos"]}
    nomes_proc = nomes_de_procedimentos(db, todos_procs)

    def lista(grupo: dict[str, dict[str, float]], n_meses: int) -> list[dict[str, Any]]:
        itens = sorted(grupo.items(), key=lambda kv: -kv[1]["valor"])[:PROCEDIMENTOS_NO_OFICIO]
        return [{"procedimento": p, "nome": nomes_proc.get(p), "aih": v["aih"], "valor": round(v["valor"], 2),
                 "por_mes": round(v["valor"] / n_meses, 2), "motivos": sorted(v["motivos"])} for p, v in itens]

    saida = []
    for c in cnes:
        h, a = hospitais.get(c), apac.get(c)
        if not h and not (a and a["teto"] > 0):
            continue
        n = len(h["meses"]) if h else 1
        item = {"cnes": c, "nome": nomes.get(c), "meses": sorted(h["meses"]) if h else [],
                "habilitacao": lista(h["habilitacao"], n) if h else [],
                "leitos": ([{"motivo": m, "leito": LEITOS[m], "aih": v["aih"], "valor": round(v["valor"], 2),
                             "por_mes": round(v["valor"] / n, 2)} for m, v in sorted(h["leitos"].items(), key=lambda kv: -kv[1]["valor"])]
                           if h else []),
                "sem_descricao": ([{"codigo": m, "aih": v["aih"], "valor": round(v["valor"], 2)}
                                   for m, v in sorted(h["sem_descricao"].items(), key=lambda kv: -kv[1]["valor"])] if h else []),
                "apac": None}
        if a and a["teto"] > 0:
            na = len(a["meses"]) or 1
            item["apac"] = {"teto": round(a["teto"], 2), "por_mes": round(a["teto"] / na, 2), "produzido": round(a["produzido"], 2),
                            "procedimentos": [{"procedimento": p, "nome": nomes_proc.get(p), "valor": round(v, 2)}
                                              for p, v in sorted(a["procedimentos"].items(), key=lambda kv: -kv[1])[:8]]}
        if item["habilitacao"] or item["leitos"] or item["sem_descricao"] or item["apac"]:
            saida.append(item)
    saida.sort(key=lambda i: -(sum(x["valor"] for g in ("habilitacao", "leitos") for x in i[g])
                               + sum(x["valor"] for x in i["sem_descricao"]) + (i["apac"]["teto"] if i["apac"] else 0)))
    return {"meses": meses, "hospitais": saida}
