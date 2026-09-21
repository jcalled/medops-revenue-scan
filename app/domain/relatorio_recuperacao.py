"""
Relatório de recuperação por hospital, mês a mês, para enviar à instituição.

Cada AIH rejeitada dos hospitais escolhidos (a última rejeição dela nos meses
carregados) cai em um grupo:

- RECUPERADA: aprovada no RD num processamento posterior à rejeição. O valor é o
  aprovado, e o mês é o da aprovação.
- A_RECUPERAR: no prazo, com chance pelo kit (alta, média ou incerta).
- DEPENDE_GESTOR: no prazo, mas depende da Secretaria ou de investigar o motivo.
- PERDIDA: capacidade (a regra do MS cancela a AIH) ou prazo vencido.
- JA_APROVADA: aparece aprovada em processamento anterior ou igual ao da
  rejeição (duplicidade, 040006): não é valor a recuperar.

A_RECUPERAR e DEPENDE_GESTOR marcam ainda se todos os motivos são dos que o
botão de correção do FaturaSUS resolve com o TXT do hospital.

O percentual (15% por padrão) incide sobre o recuperado — só o aprovado a partir
do início do contrato, quando informado — e aparece como estimativa sobre o que
falta recuperar. A fatura com linha de base fica no acompanhamento.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.kit import NA_LISTA_DE_TRABALHO, RECUPERAVEIS, classificar
from app.domain.kits_motivo import classes_confirmadas
from app.domain.prova import aih_rejeitadas
from app.domain.resumo import _lotes, _nomes
from app.domain.prevencao import GRUPOS as GRUPOS_FATURASUS
from app.models import SihApprovedAih, SihPrevention

GRUPOS = {
    "RECUPERADA": "Já recuperada",
    "A_RECUPERAR": "A recuperar",
    "DEPENDE_GESTOR": "Depende do gestor",
    "PERDIDA": "Não recuperável (regra do MS)",
    "JA_APROVADA": "Já aprovada antes",
}
# Motivos que o botão de correção do FaturaSUS resolve quando o hospital manda o TXT do SISAIH01.
MOTIVOS_DO_BOTAO = frozenset({"060017", "060197", "060072", "060055", "060109", "060065", "060150"})
MOTIVOS_NO_HOSPITAL = 5
# O que o FaturaSUS disse de cada AIH ainda recuperável, com o dado público (prevenção da carga).
SIMULACAO = {**GRUPOS_FATURASUS, "PEGARIA": "O FaturaSUS já aponta o erro", "SEM_AVALIACAO": "Ainda não avaliada"}


def _soma() -> dict[str, float]:
    return {"aih": 0, "valor": 0.0}


def _somar(alvo: dict[str, float], valor: float) -> None:
    alvo["aih"] += 1
    alvo["valor"] += valor


def _fechar(d: dict[str, float]) -> dict[str, float]:
    return {"aih": int(d["aih"]), "valor": round(d["valor"], 2)}


def _grupo(linha: dict[str, Any], classe: str) -> str:
    if linha["aprovacao_posterior"]:
        return "RECUPERADA"
    if linha["situacao"] == "JA_RECEBIDA":
        return "JA_APROVADA"
    if classe in RECUPERAVEIS:
        return "A_RECUPERAR"
    if classe in NA_LISTA_DE_TRABALHO:
        return "DEPENDE_GESTOR"
    return "PERDIDA"


def _aprovacoes(db: Session, n_aih: list[str]) -> dict[str, list[tuple[str, float | None]]]:
    saida: dict[str, list[tuple[str, float | None]]] = defaultdict(list)
    for lote in _lotes(n_aih):
        for n, competencia, valor in db.execute(
            select(SihApprovedAih.n_aih, SihApprovedAih.competencia, SihApprovedAih.valor)
            .where(SihApprovedAih.n_aih.in_(lote))
        ):
            saida[n].append((competencia, float(valor) if valor is not None else None))
    return saida


def _prevencao(db: Session, linhas: list[dict[str, Any]]) -> dict[tuple[str, str], SihPrevention]:
    saida: dict[tuple[str, str], SihPrevention] = {}
    chaves = {(l["n_aih"], l["competencia"]) for l in linhas}
    for lote in _lotes(sorted({n for n, _ in chaves})):
        for p in db.execute(select(SihPrevention).where(SihPrevention.n_aih.in_(lote))).scalars():
            if (p.n_aih, p.competencia) in chaves:
                saida[(p.n_aih, p.competencia)] = p
    return saida


def _apontamento(p: SihPrevention | None) -> dict[str, Any]:
    if p is None:
        return {"grupo": "SEM_AVALIACAO", "regras": [], "mensagem": None}
    regras = sorted({*(p.falhas or []), *(p.avisos or [])}) if p.pegaria else []
    mensagem = next((m.get("message") for m in p.mensagens or [] if m.get("code") in regras), None)
    return {"grupo": p.grupo, "regras": regras, "mensagem": mensagem}


def _novo_bloco() -> dict[str, Any]:
    return {"rejeitadas": _soma(), **{g: _soma() for g in GRUPOS}, "botao": _soma(), "vence_neste_mes": _soma()}


def _fechar_bloco(b: dict[str, Any]) -> dict[str, Any]:
    return {k: _fechar(v) for k, v in b.items()}


def _taxa(bloco: dict[str, Any], recuperado_cobravel: float, percentual: float) -> dict[str, float]:
    return {
        "sobre_recuperado": round(recuperado_cobravel * percentual / 100, 2),
        "recuperado_cobravel": round(recuperado_cobravel, 2),
        "estimada_sobre_a_recuperar": round(bloco["A_RECUPERAR"]["valor"] * percentual / 100, 2),
        "estimada_com_gestor": round((bloco["A_RECUPERAR"]["valor"] + bloco["DEPENDE_GESTOR"]["valor"]) * percentual / 100, 2),
    }


def _simulacao(somas: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    return [{"grupo": g, "nome": SIMULACAO[g], **_fechar(somas[g])} for g in SIMULACAO if somas.get(g, {}).get("aih")]


def montar_relatorio(db: Session, cnes: list[str], meses: list[str], referencia: str, *,
                     percentual: float = 15.0, inicio: str | None = None) -> dict[str, Any]:
    linhas = aih_rejeitadas(db, cnes, meses)
    nomes = _nomes(db, cnes)
    confirmados = classes_confirmadas(db)
    aprovacoes = _aprovacoes(db, [l["n_aih"] for l in linhas])
    prevencao = _prevencao(db, linhas)

    hospitais: dict[str, dict[str, Any]] = {}
    geral = _novo_bloco()
    geral_simulacao: dict[str, dict[str, float]] = defaultdict(_soma)
    geral_recuperado_mes: dict[str, dict[str, float]] = defaultdict(_soma)
    geral_cobravel = 0.0
    for l in linhas:
        classe, prazo = classificar(l, referencia, confirmados)
        grupo = _grupo(l, classe)
        valor = l["valor"]
        h = hospitais.setdefault(l["cnes"], {
            "cnes": l["cnes"], "nome": nomes.get(l["cnes"]), "total": _novo_bloco(),
            "meses": defaultdict(_novo_bloco), "recuperado_por_mes": defaultdict(_soma),
            "motivos": defaultdict(lambda: {**_soma(), "descricao": None}), "cobravel": 0.0,
            "simulacao": defaultdict(_soma),
        })
        mes = h["meses"][l["competencia"]]
        codigos = {m["codigo"] for m in l["motivos"]}

        if grupo == "RECUPERADA":
            depois = sorted(a for a in aprovacoes.get(l["n_aih"], []) if a[0] > l["competencia"])
            mes_aprovacao, aprovado = depois[0] if depois else (None, None)
            valor_grupo = aprovado if aprovado is not None else valor
            if mes_aprovacao:
                _somar(h["recuperado_por_mes"][mes_aprovacao], valor_grupo)
                _somar(geral_recuperado_mes[mes_aprovacao], valor_grupo)
            if inicio is None or (mes_aprovacao and mes_aprovacao >= inicio):
                h["cobravel"] += valor_grupo
                geral_cobravel += valor_grupo
        else:
            valor_grupo = valor

        for bloco in (h["total"], mes, geral):
            _somar(bloco["rejeitadas"], valor)
            _somar(bloco[grupo], valor_grupo)
            if grupo in ("A_RECUPERAR", "DEPENDE_GESTOR"):
                if codigos and codigos <= MOTIVOS_DO_BOTAO:
                    _somar(bloco["botao"], valor)
                if prazo == referencia:
                    _somar(bloco["vence_neste_mes"], valor)
        if grupo in ("A_RECUPERAR", "DEPENDE_GESTOR"):
            for m in l["motivos"]:
                alvo = h["motivos"][m["codigo"]]
                _somar(alvo, valor)
                alvo["descricao"] = m["descricao"]
            simulado = _apontamento(prevencao.get((l["n_aih"], l["competencia"])))["grupo"]
            _somar(h["simulacao"][simulado], valor)
            _somar(geral_simulacao[simulado], valor)

    def linha_hospital(h: dict[str, Any]) -> dict[str, Any]:
        motivos = sorted(h["motivos"].items(), key=lambda kv: -kv[1]["valor"])[:MOTIVOS_NO_HOSPITAL]
        return {
            "cnes": h["cnes"], "nome": h["nome"],
            "total": _fechar_bloco(h["total"]),
            "meses": [{"competencia": c, **_fechar_bloco(b)} for c, b in sorted(h["meses"].items())],
            "recuperado_por_mes": [{"competencia": c, **_fechar(v)} for c, v in sorted(h["recuperado_por_mes"].items())],
            "motivos": [{"codigo": c, "descricao": v["descricao"], **_fechar(v)} for c, v in motivos],
            "taxa": _taxa(h["total"], h["cobravel"], percentual),
            "simulacao": _simulacao(h["simulacao"]),
        }

    lista = sorted((linha_hospital(h) for h in hospitais.values()),
                   key=lambda h: -(h["total"]["A_RECUPERAR"]["valor"] + h["total"]["DEPENDE_GESTOR"]["valor"]
                                   + h["total"]["RECUPERADA"]["valor"]))
    return {
        "referencia": referencia,
        "meses": meses,
        "percentual": percentual,
        "inicio": inicio,
        "grupos": GRUPOS,
        "total": _fechar_bloco(geral),
        "recuperado_por_mes": [{"competencia": c, **_fechar(v)} for c, v in sorted(geral_recuperado_mes.items())],
        "taxa": _taxa(geral, geral_cobravel, percentual),
        "simulacao": _simulacao(geral_simulacao),
        "simulacao_nomes": SIMULACAO,
        "hospitais": lista,
        "sem_rejeicao": sorted(set(cnes) - set(hospitais)),
        "gerado_em": date.today().isoformat(),
    }


def montar_pacote(db: Session, cnes: list[str], meses: list[str], referencia: str) -> dict[str, Any]:
    """
    Pacote de correção para o hospital: por motivo, o que fazer, onde, com que
    documentos e qual regra — e as AIH ainda no prazo que dependem dele.

    Sai do dado público: diz o que mudar, mas o arquivo corrigido depende do TXT
    do SISAIH01 do hospital (é ele que o botão do FaturaSUS corrige).
    """
    from app.domain.kits_motivo import kits_usados

    linhas = aih_rejeitadas(db, cnes, meses)
    nomes = _nomes(db, cnes)
    confirmados = classes_confirmadas(db)
    abertas = []
    for l in linhas:
        classe, prazo = classificar(l, referencia, confirmados)
        grupo = _grupo(l, classe)
        if grupo in ("A_RECUPERAR", "DEPENDE_GESTOR"):
            abertas.append((l, classe, prazo, grupo))
    kits = kits_usados(db, {m["codigo"] for l, *_ in abertas for m in l["motivos"]})
    prevencao = _prevencao(db, [l for l, *_ in abertas])

    hospitais: dict[str, dict[str, Any]] = {}
    planilha = []
    for l, classe, prazo, grupo in abertas:
        codigos = [m["codigo"] for m in l["motivos"]]
        botao = bool(codigos) and set(codigos) <= MOTIVOS_DO_BOTAO
        h = hospitais.setdefault(l["cnes"], {"cnes": l["cnes"], "nome": nomes.get(l["cnes"]), "total": _soma(),
                                             "vence_neste_mes": _soma(), "motivos": {}})
        _somar(h["total"], l["valor"])
        if prazo == referencia:
            _somar(h["vence_neste_mes"], l["valor"])
        aih = {"n_aih": l["n_aih"], "competencia": l["competencia"], "dt_saida": l["dt_saida"], "prazo": prazo,
               "valor": l["valor"], "procedimento": l["procedimento"], "grupo": grupo, "botao_faturasus": botao,
               "motivos": codigos, "faturasus": _apontamento(prevencao.get((l["n_aih"], l["competencia"])))}
        for m in l["motivos"] or [{"codigo": "SEM_MOTIVO", "descricao": "Motivo não publicado no ER"}]:
            alvo = h["motivos"].setdefault(m["codigo"], {"codigo": m["codigo"], "descricao": m["descricao"],
                                                         "kit": kits.get(m["codigo"]), "total": _soma(), "aih": []})
            _somar(alvo["total"], l["valor"])
            alvo["aih"].append(aih)
            kit = kits.get(m["codigo"]) or {}
            planilha.append({
                "cnes": l["cnes"], "hospital": nomes.get(l["cnes"]), "n_aih": l["n_aih"], "competencia": l["competencia"],
                "dt_saida": l["dt_saida"], "prazo": prazo, "valor": l["valor"], "procedimento": l["procedimento"],
                "motivo": m["codigo"], "descricao": m["descricao"], "grupo": GRUPOS[grupo],
                "onde_corrigir": kit.get("onde_nome"), "o_que_fazer": " | ".join(kit.get("passos") or []),
                "regra": kit.get("fonte"), "botao_faturasus": "sim, com o TXT do hospital" if botao else "não",
                "faturasus_aponta": aih["faturasus"]["mensagem"] or SIMULACAO[aih["faturasus"]["grupo"]],
            })

    saida = []
    for h in hospitais.values():
        motivos = sorted(h["motivos"].values(), key=lambda m: -m["total"]["valor"])
        for m in motivos:
            m["total"] = _fechar(m["total"])
            m["aih"].sort(key=lambda a: (a["prazo"] or "999999", -a["valor"]))
        saida.append({"cnes": h["cnes"], "nome": h["nome"], "total": _fechar(h["total"]),
                      "vence_neste_mes": _fechar(h["vence_neste_mes"]), "motivos": motivos})
    saida.sort(key=lambda h: -h["total"]["valor"])
    planilha.sort(key=lambda p: (p["hospital"] or "", p["prazo"] or "999999", -p["valor"]))
    return {"referencia": referencia, "meses": meses, "hospitais": saida, "planilha": planilha,
            "gerado_em": date.today().isoformat()}
