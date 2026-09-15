"""
Kit de recuperação de qualquer recorte: o que dá e o que não dá para recuperar
das AIH rejeitadas, e a lista de trabalho do faturamento.

Cada AIH rejeitada (a última rejeição dela no período) cai numa classe:

- JA_RECEBIDA: voltou aprovada em algum processamento — aprovação registrada, sem comprovação de recebimento;
- PRAZO_VENCIDO: fora da janela estimada de seis meses após a alta para reapresentação;
- GESTOR: teto, faixa, bloqueio da secretaria — negociação, não correção;
- INVESTIGAR: motivo sem regra (060221, por exemplo) ou AIH sem data de alta;
- ALTA, MEDIA, INCERTA: recuperável dentro do prazo, pela chance de a correção
  passar. Alta é cadastro ou conta; média depende de a habilitação ou o leito
  existirem; incerta é capacidade instalada, que depende dos leitos em
  funcionamento e da regra da secretaria.

O prazo conta a partir do mês de referência (o mês em que o hospital ainda vai
apresentar): recuperar o que vence neste mês vem antes do resto.

"O que o hospital recupera sozinho" mede, no estado, quanto das rejeitadas de
cada tipo voltou aprovada nos meses seguintes sem ação nenhuma nossa: é o piso
que não se cobra.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.kits_motivo import (
    CLASSE_POR_CATEGORIA, classe_pelos_kits, classes_confirmadas, kits_usados, resultados, tratativa_json, tratativas,
)
from app.domain.prova import aih_rejeitadas
from app.domain.recuperacao import prazo_estimado
from app.domain.resumo import _lotes, _nomes
from app.engine.categorias import POR_CODIGO, categorizar
from app.models import SihApprovedAih, SihRejection, SihRejectionReason

CLASSES = {
    "ALTA": "Recuperável · chance alta",
    "MEDIA": "Recuperável · chance média",
    "INCERTA": "Recuperável · chance incerta",
    "INVESTIGAR": "A investigar",
    "GESTOR": "Bloqueio do gestor",
    "PRAZO_VENCIDO": "Fora da janela estimada de reapresentação",
    "JA_RECEBIDA": "Aprovação localizada no RD",
}
RECUPERAVEIS = ("ALTA", "MEDIA", "INCERTA")
NA_LISTA_DE_TRABALHO = RECUPERAVEIS + ("INVESTIGAR", "GESTOR")
_ORDEM = {c: i for i, c in enumerate(CLASSES)}

ONDE_CORRIGIR = {
    "PROFISSIONAL": "CNES do profissional (vínculo, CBO, carga horária) ou CNS/CBO digitado na AIH",
    "PACIENTE": "SISAIH01: CNS do paciente e datas da internação",
    "REGRAS_SIGTAP": "SISAIH01: procedimento, quantidade e compatibilidades, conforme o prontuário",
    "LEITO_CNES": "CNES: leitos de UTI/UCI e habilitação — só se o leito existe",
    "HABILITACAO_SERVICO": "CNES: habilitação ou serviço/classificação — só se o hospital tem de fato",
    "CAPACIDADE": "CNES (leitos SUS em funcionamento) e SESA (regra de capacidade); reapresentar em mês com folga",
    "PRAZO": "Confirmar apresentação anterior, data de alta e janela de reapresentação com o gestor",
    "ADMINISTRATIVO": "SESA: teto, faixa de numeração ou bloqueio",
    "OUTROS": "SESA: confirmar o que o motivo significa",
}
POR_QUE_A_CHANCE = {
    "ALTA": "Correção de cadastro ou da conta: o hospital costuma conseguir.",
    "MEDIA": "Só volta se a habilitação ou o leito existem e faltam no CNES.",
    "INCERTA": "Depende dos leitos em funcionamento e da regra de capacidade da secretaria.",
    "INVESTIGAR": "Motivo sem regra conhecida ou AIH sem data de alta: confirmar antes de trabalhar.",
    "GESTOR": "Decisão da secretaria: negociar, não corrigir a conta.",
}
AMOSTRA_MINIMA = 20


def referencia_padrao(hoje: date | None = None) -> str:
    """O mês em que o hospital ainda apresenta: o corrente."""
    hoje = hoje or date.today()
    return f"{hoje.year}{hoje.month:02d}"


def _meses_entre(de: str, ate: str) -> int:
    return (int(ate[:4]) * 12 + int(ate[4:])) - (int(de[:4]) * 12 + int(de[4:]))


def classificar(linha: dict[str, Any], referencia: str,
                confirmados: dict[str, str] | None = None) -> tuple[str, str | None]:
    if linha["situacao"] == "JA_RECEBIDA":
        return "JA_RECEBIDA", None
    # Kit confirmado de algum motivo manda; sem ele, vale o tipo de rejeição.
    codigos = [m["codigo"] for m in linha.get("motivos", [])]
    classe = (classe_pelos_kits(codigos, confirmados or {})
              or CLASSE_POR_CATEGORIA.get(linha["categoria"], "INVESTIGAR"))
    if classe == "JA_RECEBIDA":
        # Um manual de motivo não substitui a aprovação efetivamente localizada no RD.
        classe = "INVESTIGAR"
    if linha["categoria"] == "PRAZO":
        classe = "INVESTIGAR"  # Rever também kits antigos que usavam quatro meses.
    prazo = prazo_estimado(date.fromisoformat(linha["dt_saida"])) if linha.get("dt_saida") else None
    if classe in RECUPERAVEIS or linha["categoria"] == "PRAZO":
        if prazo is None:
            return "INVESTIGAR", None
        if prazo < referencia:
            return "PRAZO_VENCIDO", prazo
    return classe, prazo


def recupera_sozinho(db: Session, ufs: list[str], meses: list[str]) -> dict[str, dict[str, Any]]:
    """
    Das rejeitadas de cada tipo nos meses com tempo para voltar (todos menos o
    último), quantas aparecem aprovadas num processamento seguinte do período.
    """
    coorte = meses[:-1]
    if not ufs or not coorte:
        return {}
    rejeicoes = db.execute(
        select(SihRejection.n_aih, SihRejection.competencia)
        .where(SihRejection.uf.in_(ufs), SihRejection.competencia.in_(coorte))
    ).all()
    motivos: dict[tuple[str, str], set[str]] = defaultdict(set)
    for n_aih, competencia, codigo in db.execute(
        select(SihRejectionReason.n_aih, SihRejectionReason.competencia, SihRejectionReason.codigo_erro)
        .where(SihRejectionReason.uf.in_(ufs), SihRejectionReason.competencia.in_(coorte))
    ):
        motivos[(n_aih, competencia)].add(codigo)
    aprovadas: dict[str, set[str]] = defaultdict(set)
    for lote in _lotes(sorted({n for n, _ in rejeicoes})):
        for n_aih, competencia in db.execute(
            select(SihApprovedAih.n_aih, SihApprovedAih.competencia)
            .where(SihApprovedAih.n_aih.in_(lote), SihApprovedAih.competencia.in_(meses))
        ):
            aprovadas[n_aih].add(competencia)

    contagem: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for n_aih, competencia in rejeicoes:
        categoria = categorizar(motivos.get((n_aih, competencia), set())).codigo
        contagem[categoria][0] += 1
        contagem[categoria][1] += any(c > competencia for c in aprovadas.get(n_aih, ()))
    return {
        categoria: {
            "nome": POR_CODIGO[categoria].nome, "rejeitadas": total, "voltaram": voltaram,
            # Amostra pequena não vira taxa: melhor "sem medida" do que um número frágil.
            "taxa": round(voltaram / total, 4) if total >= AMOSTRA_MINIMA else None,
        }
        for categoria, (total, voltaram) in sorted(contagem.items(), key=lambda kv: -kv[1][0])
    }


def _soma() -> dict[str, float]:
    return {"aih": 0, "valor": 0.0}


def _somar(alvo: dict[str, float], valor: float) -> None:
    alvo["aih"] += 1
    alvo["valor"] += valor


def _fechar(d: dict[str, float], total: float | None = None) -> dict[str, float]:
    saida = {"aih": d["aih"], "valor": round(d["valor"], 2)}
    if total is not None:
        saida["pct_valor"] = round(d["valor"] / total, 4) if total else 0.0
    return saida


def montar_kit(db: Session, cnes: list[str], meses: list[str], ufs: list[str], referencia: str,
               *, lista: str = "trabalho", limite: int = 5000) -> dict[str, Any]:
    linhas = aih_rejeitadas(db, cnes, meses)
    nomes = _nomes(db, cnes)
    sozinho = recupera_sozinho(db, ufs, meses)
    confirmados = classes_confirmadas(db)

    classes = {c: _soma() for c in CLASSES}
    hospitais: dict[str, dict[str, Any]] = {}
    vencimento: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: {c: _soma() for c in RECUPERAVEIS})
    piso = 0.0
    itens = []
    for l in linhas:
        classe, prazo = classificar(l, referencia, confirmados)
        valor = l["valor"]
        _somar(classes[classe], valor)
        h = hospitais.setdefault(l["cnes"], {"cnes": l["cnes"], "nome": nomes.get(l["cnes"]), "total": _soma(),
                                             "classes": {c: _soma() for c in CLASSES}})
        _somar(h["total"], valor)
        _somar(h["classes"][classe], valor)
        if classe in RECUPERAVEIS:
            _somar(vencimento[prazo][classe], valor)
            taxa = (sozinho.get(l["categoria"]) or {}).get("taxa")
            piso += valor * (taxa or 0)
        if lista == "todas" or classe in NA_LISTA_DE_TRABALHO:
            itens.append({
                "cnes": l["cnes"], "hospital": nomes.get(l["cnes"]), "n_aih": l["n_aih"],
                "competencia": l["competencia"], "procedimento": l["procedimento"], "dt_saida": l["dt_saida"],
                "valor": valor, "motivos": l["motivos"], "categoria": l["categoria"],
                "categoria_nome": l["categoria_nome"], "classe": classe, "classe_nome": CLASSES[classe],
                "prazo_estimado": prazo,
                "meses_para_vencer": _meses_entre(referencia, prazo) if prazo and classe in RECUPERAVEIS else None,
                "onde": ONDE_CORRIGIR.get(l["categoria"], ONDE_CORRIGIR["OUTROS"]),
                "acao": POR_CODIGO[l["categoria"]].acao if l["categoria"] in POR_CODIGO else None,
                "por_que_a_chance": POR_QUE_A_CHANCE.get(classe),
                "classe_pelo_kit": classe_pelos_kits([m["codigo"] for m in l["motivos"]], confirmados) is not None,
            })

    # Ordem de trabalho: o que vence antes, a chance maior, o valor maior; investigar e gestor no fim, por valor.
    itens.sort(key=lambda i: (i["classe"] not in RECUPERAVEIS,
                              i["prazo_estimado"] or "" if i["classe"] in RECUPERAVEIS else "",
                              _ORDEM[i["classe"]], -i["valor"], i["n_aih"]))
    marcadas = tratativas(db, [i["n_aih"] for i in itens])
    retornos = resultados(db, list(marcadas.values()))
    situacoes: dict[str, int] = defaultdict(int)
    for i in itens:
        i["tratativa"] = tratativa_json(marcadas.get(i["n_aih"]), retornos.get(i["n_aih"]))
        if i["classe"] in NA_LISTA_DE_TRABALHO:
            situacoes[i["tratativa"]["situacao"] if i["tratativa"] else "SEM_SITUACAO"] += 1
    total = sum(c["valor"] for c in classes.values())
    recuperavel = {"aih": sum(classes[c]["aih"] for c in RECUPERAVEIS),
                   "valor": sum(classes[c]["valor"] for c in RECUPERAVEIS)}

    def linha_hospital(h: dict[str, Any]) -> dict[str, Any]:
        rec = {"aih": sum(h["classes"][c]["aih"] for c in RECUPERAVEIS),
               "valor": sum(h["classes"][c]["valor"] for c in RECUPERAVEIS)}
        return {"cnes": h["cnes"], "nome": h["nome"], "total": _fechar(h["total"]),
                "classes": {c: _fechar(v, h["total"]["valor"]) for c, v in h["classes"].items()},
                "recuperavel": _fechar(rec, h["total"]["valor"])}

    return {
        "referencia": referencia,
        "classes": {c: {"nome": CLASSES[c], **_fechar(v, total)} for c, v in classes.items()},
        "rejeitadas": {"aih": len(linhas), "valor": round(total, 2)},
        "recuperavel_no_prazo": _fechar(recuperavel, total),
        "vence_neste_mes": _fechar({
            "aih": sum(vencimento[referencia][c]["aih"] for c in RECUPERAVEIS) if referencia in vencimento else 0,
            "valor": sum(vencimento[referencia][c]["valor"] for c in RECUPERAVEIS) if referencia in vencimento else 0.0,
        }),
        "piso_sem_acao": round(piso, 2),
        "hospitais": sorted((linha_hospital(h) for h in hospitais.values()), key=lambda h: -h["recuperavel"]["valor"]),
        "vencimento": [
            {"competencia": mes, **{c: _fechar(v) for c, v in por_classe.items()},
             "total": _fechar({"aih": sum(v["aih"] for v in por_classe.values()),
                               "valor": sum(v["valor"] for v in por_classe.values())})}
            for mes, por_classe in sorted(vencimento.items())
        ],
        "recupera_sozinho": sozinho,
        "kits_motivo": kits_usados(db, {m["codigo"] for l in linhas for m in l["motivos"]}, ufs),
        "tratativas": dict(situacoes),
        "itens": itens[:limite],
        "itens_total": len(itens),
        "lista": lista,
    }

