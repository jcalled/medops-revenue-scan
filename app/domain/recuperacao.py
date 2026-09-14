"""
Acompanhamento da recuperação: o que o modelo híbrido cobra.

Na abertura entram as AIH que o hospital pode corrigir e ainda não recebeu. A
cada mês carregado do SIH, a AIH marcada que aparece aprovada num processamento
posterior à rejeição vira RECUPERADA, com o mês e o valor aprovado no RD — o
dinheiro que entrou. A fatura do mês é o fixo por hospital mais o percentual
sobre esse valor, com as AIH como anexo.

- BASE: rejeitada antes do início e não aprovada antes dele.
- NOVA: rejeitada do início em diante; entra conforme os meses chegam.

Bloqueio do gestor e motivo sem regra não entram, como na prova AIH por AIH.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.domain.resumo import _lotes, _nomes
from app.engine.categorias import FORA_DA_RECUPERACAO, POR_CODIGO, categorizar
from app.models import (
    DataLoad, RecoveryItem, RecoveryTracking, SihApprovedAih, SihErrorCode, SihRejection, SihRejectionReason,
)

ABERTA, RECUPERADA = "EM_ABERTO", "RECUPERADA"
ATIVO, ENCERRADO = "ATIVO", "ENCERRADO"
# Motivo 040008: a AIH não pode ser apresentada mais de quatro meses depois da alta.
PRAZO_MESES = 4

RESSALVA_FATURA = (
    "Valor recuperado é o valor aprovado no RD do mês para as AIH marcadas neste acompanhamento, conferido nos "
    "arquivos públicos do DATASUS. Confira o número de cada AIH no SIH do hospital antes de emitir a nota. "
    "Percentual sobre recurso SUS de organização social depende do contrato de gestão."
)


def mais_meses(aaaamm: str, n: int) -> str:
    ano, mes = int(aaaamm[:4]), int(aaaamm[4:]) + n
    ano += (mes - 1) // 12
    return f"{ano}{(mes - 1) % 12 + 1:02d}"


def prazo_estimado(dt_saida: date | None) -> str | None:
    """Último mês de processamento em que a AIH ainda cabe: quatro meses depois da alta. É estimativa."""
    return mais_meses(f"{dt_saida.year}{dt_saida.month:02d}", PRAZO_MESES) if dt_saida else None


def ultimo_mes(db: Session, cnes: list[str]) -> str | None:
    meses = []
    for lote in _lotes(list(cnes)):
        meses.append(db.scalar(select(func.max(SihRejection.competencia)).where(SihRejection.cnes.in_(lote))))
        meses.append(db.scalar(select(func.max(SihApprovedAih.competencia)).where(SihApprovedAih.cnes.in_(lote))))
    return max((m for m in meses if m), default=None)


def conferir(db: Session, t: RecoveryTracking) -> dict[str, int]:
    """Marca as AIH que faltam e confere quais voltaram aprovadas. Pode rodar quantas vezes quiser."""
    existentes = {i.n_aih: i for i in t.itens}
    rejeicoes: dict[str, list[SihRejection]] = defaultdict(list)
    for lote in _lotes(list(t.cnes)):
        for r in db.execute(select(SihRejection).where(SihRejection.cnes.in_(lote))).scalars():
            rejeicoes[r.n_aih].append(r)

    aprovacoes: dict[str, list[tuple[str, float | None]]] = defaultdict(list)
    motivos: dict[tuple[str, str], set[str]] = defaultdict(set)
    for lote in _lotes(list(rejeicoes)):
        for n_aih, competencia, valor in db.execute(
            select(SihApprovedAih.n_aih, SihApprovedAih.competencia, SihApprovedAih.valor)
            .where(SihApprovedAih.n_aih.in_(lote))
        ):
            aprovacoes[n_aih].append((competencia, float(valor) if valor is not None else None))
        for n_aih, competencia, codigo in db.execute(
            select(SihRejectionReason.n_aih, SihRejectionReason.competencia, SihRejectionReason.codigo_erro)
            .where(SihRejectionReason.n_aih.in_(lote))
        ):
            motivos[(n_aih, competencia)].add(codigo)

    agora = datetime.now(timezone.utc)
    novas = recuperadas = 0
    for n_aih, lista in rejeicoes.items():
        lista.sort(key=lambda r: r.competencia)
        voltas = sorted(aprovacoes.get(n_aih, []), key=lambda a: a[0])
        item = existentes.get(n_aih)
        if item is None:
            antes = [r for r in lista if r.competencia < t.inicio]
            if antes:
                r, origem = antes[-1], "BASE"
                # Já tinha voltado aprovada antes do contrato: não é trabalho da recuperação.
                if any(c < t.inicio for c, _ in voltas):
                    continue
            else:
                r, origem = lista[0], "NOVA"
                # Aprovada antes (ou junto) da rejeição: não é perda.
                if any(c <= r.competencia for c, _ in voltas):
                    continue
            codigos = sorted(motivos.get((n_aih, r.competencia), set()))
            categoria = categorizar(codigos)
            if categoria.codigo in FORA_DA_RECUPERACAO:
                continue
            item = RecoveryItem(
                cnes=r.cnes, uf=r.uf, n_aih=n_aih, origem=origem, competencia_rejeicao=r.competencia,
                competencia_aih=r.competencia_aih, procedimento=r.proc_realizado, dt_saida=r.dt_saida,
                valor_rejeitado=r.valor or 0, categoria=categoria.codigo, motivos=codigos, situacao=ABERTA,
            )
            t.itens.append(item)
            existentes[n_aih] = item
            novas += 1

        if item.situacao == ABERTA:
            volta = next(((c, v) for c, v in voltas if c > item.competencia_rejeicao and c >= t.inicio), None)
            if volta:
                item.situacao, item.competencia_aprovacao, item.valor_aprovado = RECUPERADA, volta[0], volta[1]
                item.recuperado_em = agora
                recuperadas += 1

    t.conferido_em = agora
    db.flush()
    return {"novas": novas, "recuperadas": recuperadas}


def conferir_ativos(db: Session) -> int:
    """Depois de cada carga: confere todos os acompanhamentos ativos."""
    ativos = db.execute(
        select(RecoveryTracking).where(RecoveryTracking.status == ATIVO).options(selectinload(RecoveryTracking.itens))
    ).scalars().all()
    for t in ativos:
        conferir(db, t)
    db.commit()
    return len(ativos)


def _valor_rejeitado(i: RecoveryItem) -> float:
    return float(i.valor_rejeitado or 0)


def valor_cobrado(i: RecoveryItem) -> float:
    # Carga antiga sem valor no RD: usa o valor da rejeição e avisa na linha.
    return float(i.valor_aprovado) if i.valor_aprovado is not None else _valor_rejeitado(i)


def _soma(itens: list[RecoveryItem], valor=_valor_rejeitado) -> dict[str, float]:
    return {"aih": len(itens), "valor": round(sum(valor(i) for i in itens), 2)}


def situacao_do_prazo(i: RecoveryItem, proximo: str | None) -> str | None:
    prazo = prazo_estimado(i.dt_saida)
    if i.situacao != ABERTA or not prazo or not proximo:
        return None
    return "VENCIDO" if prazo < proximo else "VENCENDO" if prazo == proximo else None


def resumo(t: RecoveryTracking, ultimo: str | None) -> dict[str, Any]:
    itens = list(t.itens)
    abertas = [i for i in itens if i.situacao == ABERTA]
    recuperadas = [i for i in itens if i.situacao == RECUPERADA]
    proximo = mais_meses(ultimo, 1) if ultimo else None
    por_mes: dict[str, list[RecoveryItem]] = defaultdict(list)
    for i in recuperadas:
        por_mes[i.competencia_aprovacao or ""].append(i)
    return {
        "marcadas": _soma(itens),
        "base": _soma([i for i in itens if i.origem == "BASE"]),
        "novas": _soma([i for i in itens if i.origem == "NOVA"]),
        "em_aberto": _soma(abertas),
        "recuperadas": {**_soma(recuperadas, valor_cobrado), "valor_rejeitado": _soma(recuperadas)["valor"]},
        "taxa_recuperada": round(len(recuperadas) / len(itens), 4) if itens else 0.0,
        "por_mes": [{"competencia": c, **_soma(l, valor_cobrado)} for c, l in sorted(por_mes.items())],
        "prazo_vencido": _soma([i for i in abertas if situacao_do_prazo(i, proximo) == "VENCIDO"]),
        "vencendo": _soma([i for i in abertas if situacao_do_prazo(i, proximo) == "VENCENDO"]),
    }


def item_json(i: RecoveryItem, nomes: dict[str, str], descricoes: dict[str, str], proximo: str | None) -> dict[str, Any]:
    categoria = POR_CODIGO.get(i.categoria)
    return {
        "cnes": i.cnes,
        "hospital": nomes.get(i.cnes),
        "n_aih": i.n_aih,
        "origem": i.origem,
        "competencia_rejeicao": i.competencia_rejeicao,
        "competencia_aih": i.competencia_aih,
        "procedimento": i.procedimento,
        "dt_saida": i.dt_saida.isoformat() if i.dt_saida else None,
        "prazo_estimado": prazo_estimado(i.dt_saida),
        "prazo": situacao_do_prazo(i, proximo),
        "valor_rejeitado": _valor_rejeitado(i),
        "categoria": i.categoria,
        "categoria_nome": categoria.nome if categoria else i.categoria,
        "acao": categoria.acao if categoria else None,
        "motivos": [{"codigo": c, "descricao": descricoes.get(c)} for c in i.motivos or []],
        "situacao": i.situacao,
        "competencia_aprovacao": i.competencia_aprovacao,
        "valor_aprovado": float(i.valor_aprovado) if i.valor_aprovado is not None else None,
        "valor_estimado": i.situacao == RECUPERADA and i.valor_aprovado is None,
    }


def descricoes_de_motivos(db: Session) -> dict[str, str]:
    return dict(db.execute(select(SihErrorCode.codigo, SihErrorCode.descricao)).all())


def fatura(db: Session, t: RecoveryTracking, competencia: str) -> dict[str, Any]:
    """Fatura do mês de processamento: fixo por hospital + percentual do valor aprovado das AIH recuperadas."""
    percentual, fixo = float(t.percentual), float(t.fixo_por_hospital)
    recuperadas = sorted(
        (i for i in t.itens if i.situacao == RECUPERADA and i.competencia_aprovacao == competencia),
        key=lambda i: (i.cnes, -valor_cobrado(i), i.n_aih),
    )
    nomes = _nomes(db, t.cnes)
    descricoes = descricoes_de_motivos(db)
    ufs = sorted({i.uf for i in recuperadas})
    arquivos: dict[str, dict[str, str | None]] = {}
    for carga in db.execute(
        select(DataLoad).where(DataLoad.fonte == "SIH_RD", DataLoad.status == "OK", DataLoad.competencia == competencia,
                               DataLoad.uf.in_(ufs)).order_by(DataLoad.iniciado_em)
    ).scalars():
        arquivos[carga.uf or ""] = {"arquivo": carga.arquivo, "sha256": carga.checksum}

    por_hospital: dict[str, list[RecoveryItem]] = {c: [] for c in t.cnes}
    for i in recuperadas:
        por_hospital.setdefault(i.cnes, []).append(i)

    def conta(valor: float, hospitais: int) -> dict[str, float]:
        variavel = round(valor * percentual / 100, 2)
        return {"valor_recuperado": round(valor, 2), "fixo": round(fixo * hospitais, 2), "variavel": variavel,
                "total": round(fixo * hospitais + variavel, 2)}

    return {
        "acompanhamento": {"id": t.id, "nome": t.nome, "percentual": percentual, "fixo_por_hospital": fixo},
        "competencia": competencia,
        "hospitais": [{"cnes": c, "nome": nomes.get(c), "aih": len(itens),
                       **conta(sum(valor_cobrado(i) for i in itens), 1)} for c, itens in por_hospital.items()],
        "totais": {"aih": len(recuperadas), **conta(sum(valor_cobrado(i) for i in recuperadas), len(por_hospital))},
        "linhas": [{**item_json(i, nomes, descricoes, None), "valor_cobrado": valor_cobrado(i),
                    "arquivo_rd": arquivos.get(i.uf)} for i in recuperadas],
        "ressalva": RESSALVA_FATURA,
    }
