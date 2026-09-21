"""
Acompanhamento da recuperação: o que o modelo híbrido cobra.

Na abertura entram as AIH que o hospital pode corrigir e ainda não recebeu. A
cada mês carregado do SIH, a AIH marcada que aparece aprovada num processamento
posterior à rejeição vira RECUPERADA, com o mês e o valor aprovado no RD — o
valor bruto de produção, sem prova de recebimento. A simulação de fatura do mês é o fixo por hospital mais o percentual
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
# Reapresentação de AIH apresentada e rejeitada dentro dos quatro meses: até o 6º mês
# contado do mês da alta, inclusive (alta em janeiro: apresenta até abril, reapresenta
# em maio ou junho) — MTO SIH jan/2017, item 4; Portaria SAES/MS 1.110/2021, consolidada
# na PRC SAES/MS 1/2022. O último mês é alta + 5. A apresentação inicial vai até alta + 3.
PRAZO_MESES = 5
PRAZO_APRESENTACAO_MESES = 3
FONTE_PRAZO = "https://bvsms.saude.gov.br/bvs/saudelegis/saes/2021/prt1110_18_11_2021.html"

RESSALVA_FATURA = (
    "Simulação comercial sobre produção aprovada: não comprova recebimento nem atribuição à MedOps. "
    "O valor é o aprovado no RD do mês para as AIH marcadas neste acompanhamento, conferido nos "
    "arquivos públicos do DATASUS. O percentual incide só sobre o que passa da linha de base — o que o hospital "
    "já recuperava sozinho por mês antes do contrato. Confira o número de cada AIH no SIH do hospital antes de "
    "emitir a nota. "
    "Percentual sobre recurso SUS de organização social depende do contrato de gestão."
)


def mais_meses(aaaamm: str, n: int) -> str:
    ano, mes = int(aaaamm[:4]), int(aaaamm[4:]) + n
    ano += (mes - 1) // 12
    return f"{ano}{(mes - 1) % 12 + 1:02d}"


def prazo_estimado(dt_saida: date | None) -> str | None:
    """Último mês em que a AIH rejeitada ainda pode ser reapresentada: o 6º contado do mês da alta. Confirmar calendário do gestor."""
    return mais_meses(f"{dt_saida.year}{dt_saida.month:02d}", PRAZO_MESES) if dt_saida else None


def ultimo_mes(db: Session, cnes: list[str]) -> str | None:
    meses = []
    for lote in _lotes(list(cnes)):
        meses.append(db.scalar(select(func.max(SihRejection.competencia)).where(SihRejection.cnes.in_(lote))))
        meses.append(db.scalar(select(func.max(SihApprovedAih.competencia)).where(SihApprovedAih.cnes.in_(lote))))
    return max((m for m in meses if m), default=None)


def meses_carregados(db: Session, cnes: list[str]) -> list[str]:
    meses: set[str] = set()
    for lote in _lotes(list(cnes)):
        meses.update(db.execute(select(SihRejection.competencia).where(SihRejection.cnes.in_(lote)).distinct()).scalars())
        meses.update(db.execute(select(SihApprovedAih.competencia).where(SihApprovedAih.cnes.in_(lote)).distinct()).scalars())
    return sorted(meses)


# Mês só entra na linha de base com dois meses carregados antes dele: sem isso a
# reapresentação de AIH rejeitada antes da carga não aparece e a média sai baixa.
HISTORICO_MINIMO = 2
MESES_LINHA_DE_BASE = 6


def calcular_linha_de_base(db: Session, cnes: list[str], inicio: str) -> tuple[dict[str, float], list[str]]:
    """
    O que cada hospital já recuperava sozinho por mês antes do contrato.

    Nos meses de processamento anteriores ao início (até seis, com histórico
    carregado antes deles), soma o valor aprovado das AIH que tinham sido
    rejeitadas por motivo corrigível num processamento anterior, e divide pelos
    meses. É o que se desconta do recuperado antes de aplicar o percentual.
    """
    meses = meses_carregados(db, cnes)
    elegiveis = [m for i, m in enumerate(meses) if i >= HISTORICO_MINIMO and m < inicio][-MESES_LINHA_DE_BASE:]
    base = {c: 0.0 for c in cnes}
    if not elegiveis:
        return base, []
    janela = set(elegiveis)
    rejeicoes, aprovacoes, motivos = _historico(db, cnes)
    for n_aih, lista in rejeicoes.items():
        corrigiveis = [r for r in lista
                       if categorizar(motivos.get((n_aih, r.competencia), set())).codigo not in FORA_DA_RECUPERACAO]
        if not corrigiveis:
            continue
        primeira = corrigiveis[0]
        volta = next(((c, v) for c, v in aprovacoes.get(n_aih, []) if c > primeira.competencia), None)
        if volta and volta[0] in janela:
            valor = volta[1] if volta[1] is not None else 0.0
            base[primeira.cnes] = base.get(primeira.cnes, 0.0) + valor
    return {c: round(v / len(elegiveis), 2) for c, v in base.items()}, elegiveis


def _historico(db: Session, cnes: list[str]) -> tuple[
    dict[str, list[SihRejection]], dict[str, list[tuple[str, float | None]]], dict[tuple[str, str], set[str]]
]:
    """Rejeições dos hospitais, aprovações dessas AIH (mês e valor) e motivos, em ordem de mês."""
    rejeicoes: dict[str, list[SihRejection]] = defaultdict(list)
    for lote in _lotes(list(cnes)):
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
    for lista in rejeicoes.values():
        lista.sort(key=lambda r: r.competencia)
    for voltas in aprovacoes.values():
        voltas.sort(key=lambda a: a[0])
    return rejeicoes, aprovacoes, motivos


def conferir(db: Session, t: RecoveryTracking) -> dict[str, int]:
    """Marca as AIH que faltam e confere quais voltaram aprovadas. Pode rodar quantas vezes quiser."""
    existentes = {i.n_aih: i for i in t.itens}
    rejeicoes, aprovacoes, motivos = _historico(db, t.cnes)

    agora = datetime.now(timezone.utc)
    novas = recuperadas = 0
    for n_aih, lista in rejeicoes.items():
        voltas = aprovacoes.get(n_aih, [])
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
    # Sem valor no RD, não inventar uma base financeira com o valor rejeitado.
    return float(i.valor_aprovado) if i.valor_aprovado is not None else 0.0


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
        "linha_de_base_mensal": round(sum(float(v) for v in (t.linha_de_base or {}).values()), 2),
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
    base = {c: float(v) for c, v in (t.linha_de_base or {}).items()}

    # Percentual só sobre o que passa da linha de base, hospital por hospital:
    # um hospital abaixo da base não come o excedente de outro.
    hospitais = []
    for c, itens in por_hospital.items():
        recuperado = sum(valor_cobrado(i) for i in itens)
        linha = base.get(c, 0.0)
        excedente = max(0.0, recuperado - linha)
        variavel = round(excedente * percentual / 100, 2)
        hospitais.append({
            "cnes": c, "nome": nomes.get(c), "aih": len(itens), "valor_recuperado": round(recuperado, 2),
            "linha_de_base": round(linha, 2), "excedente": round(excedente, 2), "fixo": round(fixo, 2),
            "variavel": variavel, "total": round(fixo + variavel, 2),
        })
    variavel_total = round(sum(h["variavel"] for h in hospitais), 2)
    fixo_total = round(fixo * len(hospitais), 2)

    return {
        "acompanhamento": {"id": t.id, "nome": t.nome, "percentual": percentual, "fixo_por_hospital": fixo},
        "competencia": competencia,
        "linha_de_base": {"origem": t.linha_de_base_origem, "meses": list(t.linha_de_base_meses or []),
                          "mensal": round(sum(base.values()), 2)},
        "hospitais": hospitais,
        "totais": {
            "aih": len(recuperadas),
            "valor_recuperado": round(sum(h["valor_recuperado"] for h in hospitais), 2),
            "linha_de_base": round(sum(h["linha_de_base"] for h in hospitais), 2),
            "excedente": round(sum(h["excedente"] for h in hospitais), 2),
            "fixo": fixo_total,
            "variavel": variavel_total,
            "total": round(fixo_total + variavel_total, 2),
        },
        "linhas": [{**item_json(i, nomes, descricoes, None), "valor_cobrado": valor_cobrado(i),
                    "arquivo_rd": arquivos.get(i.uf)} for i in recuperadas],
        "natureza": "SIMULACAO_SOBRE_APROVACAO",
        "recebimento_comprovado": False,
        "atribuicao_medops_comprovada": False,
        "aih_sem_valor_aprovado": sum(i.valor_aprovado is None for i in recuperadas),
        "ressalva": RESSALVA_FATURA,
    }
