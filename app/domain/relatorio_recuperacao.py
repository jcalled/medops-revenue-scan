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
from app.engine.categorias import POR_CODIGO, categorizar
from app.models import CnesBed, Opportunity, SiaApacMonth, SihApprovedAih, SihHospitalMonth, SihPrevention

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


def _apac(db: Session, cnes: list[str], meses: list[str]) -> dict[str, dict[str, Any]]:
    """APAC do SIA nos mesmos meses: produzido, aprovado e não aprovado — quase todo teto do gestor."""
    por_cnes: dict[str, dict[str, Any]] = {}
    for lote in _lotes(cnes):
        for a in db.execute(select(SiaApacMonth).where(SiaApacMonth.cnes.in_(lote), SiaApacMonth.competencia.in_(meses))).scalars():
            h = por_cnes.setdefault(a.cnes, {"produzido": 0.0, "aprovado": 0.0, "nao_aprovado": 0.0, "teto": 0.0,
                                             "ocorrencias": defaultdict(lambda: {"linhas": 0, "valor": 0.0, "nome": ""}),
                                             "procedimentos": defaultdict(float), "meses": []})
            h["produzido"] += float(a.valor_produzido)
            h["aprovado"] += float(a.valor_aprovado)
            h["nao_aprovado"] += float(a.valor_nao_aprovado)
            h["teto"] += float(a.valor_teto)
            h["meses"].append(a.competencia)
            for codigo, o in (a.ocorrencias or {}).items():
                h["ocorrencias"][codigo]["linhas"] += o.get("linhas", 0)
                h["ocorrencias"][codigo]["valor"] += o.get("valor", 0.0)
                h["ocorrencias"][codigo]["nome"] = o.get("nome", "")
            for p in a.procedimentos or []:
                h["procedimentos"][p["procedimento"]] += p["valor"]
    return {c: _fechar_apac(h) for c, h in por_cnes.items()}


def _fechar_apac(h: dict[str, Any]) -> dict[str, Any]:
    return {
        "produzido": round(h["produzido"], 2), "aprovado": round(h["aprovado"], 2),
        "nao_aprovado": round(h["nao_aprovado"], 2), "teto": round(h["teto"], 2),
        "ocorrencias": sorted(({"codigo": c, **{**o, "valor": round(o["valor"], 2)}} for c, o in h["ocorrencias"].items()),
                              key=lambda o: -o["valor"]),
        "procedimentos": [{"procedimento": p, "valor": round(v, 2)}
                          for p, v in sorted(h["procedimentos"].items(), key=lambda kv: -kv[1])[:5]],
        "meses": sorted(set(h["meses"])),
    }


def _somar_apac(itens: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not itens:
        return None
    total = {"produzido": 0.0, "aprovado": 0.0, "nao_aprovado": 0.0, "teto": 0.0,
             "ocorrencias": defaultdict(lambda: {"linhas": 0, "valor": 0.0, "nome": ""}),
             "procedimentos": defaultdict(float), "meses": []}
    for h in itens:
        for chave in ("produzido", "aprovado", "nao_aprovado", "teto"):
            total[chave] += h[chave]
        for o in h["ocorrencias"]:
            total["ocorrencias"][o["codigo"]]["linhas"] += o["linhas"]
            total["ocorrencias"][o["codigo"]]["valor"] += o["valor"]
            total["ocorrencias"][o["codigo"]]["nome"] = o["nome"]
        for p in h["procedimentos"]:
            total["procedimentos"][p["procedimento"]] += p["valor"]
        total["meses"] += h["meses"]
    return _fechar_apac(total)


# Como deixar de perder no mês seguinte, por tipo de rejeição. É o que evita a perda — a
# recuperação da AIH já rejeitada está no pacote de correção.
COMO_EVITAR = {
    "CAPACIDADE": "Antes de fechar o lote, somar as diárias do mês contra os leitos SUS do CNES × dias — o FaturaSUS "
                  "avisa. Se o hospital opera mais leitos SUS do que o CNES mostra, atualizar o CNES: a capacidade dos "
                  "próximos meses sobe. AIH rejeitada por capacidade é cancelada; aqui, só prevenir resolve.",
    "LEITO_CNES": "Cadastrar e habilitar no CNES os leitos de UTI e UCI que o hospital cobra, ou deixar de cobrar a "
                  "diária de leito sem habilitação. O volume abaixo sustenta o pedido de habilitação à secretaria.",
    "HABILITACAO_SERVICO": "Pedir a habilitação dos procedimentos que o hospital já realiza — o volume e o valor abaixo "
                           "sustentam o pedido — e manter serviços e classificações do CNES em dia, inclusive terceiros.",
    "PRAZO": "Controlar o prazo de cada AIH: apresentar até o 4º mês contado da alta. O relatório mostra o que vence no mês.",
    "REGRAS_SIGTAP": "Passar o lote no FaturaSUS antes do envio: quantidades, compatibilidades, OPM e permanência "
                     "pelas regras do SIGTAP da competência.",
    "PROFISSIONAL": "Atualizar o CNES do corpo clínico (vínculo e CBO) antes do fechamento; o FaturaSUS confere cada "
                    "profissional no arquivo do hospital.",
    "PACIENTE": "Conferir CNS do paciente e internações sobrepostas antes do envio.",
    "ADMINISTRATIVO": "Resolver com a secretaria, antes da competência, faixas de numeração de AIH, bloqueios e teto.",
    "OUTROS": "Pedir à secretaria o significado dos motivos sem descrição e a regra que os dispara: um código "
              "explicado costuma destravar muitas AIH de uma vez.",
}
APAC_COMO_EVITAR = ("Renegociar a programação (teto) com o gestor, mostrando a produção acima do teto por procedimento "
                    "— em geral oncologia. É a produção que o hospital já faz e não recebe.")


def _capacidade(db: Session, cnes: list[str], meses: list[str]) -> dict[str, dict[str, Any]]:
    """Leitos SUS do CNES × dias contra as diárias do mês (aprovadas e rejeitadas): a conta que o SIH faz."""
    leitos: dict[str, dict[str, int]] = defaultdict(lambda: {"sus": 0, "existentes": 0, "uti_sus": 0})
    ultima = {}
    for lote in _lotes(cnes):
        for c in db.execute(select(CnesBed).where(CnesBed.cnes.in_(lote))).scalars():
            ultima[c.cnes] = max(ultima.get(c.cnes, ""), c.competencia)
        for c in db.execute(select(CnesBed).where(CnesBed.cnes.in_(lote))).scalars():
            if c.competencia != ultima.get(c.cnes):
                continue
            if c.tipo_leito == "3":
                leitos[c.cnes]["uti_sus"] += c.qt_sus
            else:
                leitos[c.cnes]["sus"] += c.qt_sus
                leitos[c.cnes]["existentes"] += c.qt_existente
    diarias: dict[str, list[int]] = defaultdict(list)
    for lote in _lotes(cnes):
        for m in db.execute(select(SihHospitalMonth).where(SihHospitalMonth.cnes.in_(lote),
                                                           SihHospitalMonth.competencia.in_(meses))).scalars():
            # Leitos gerais contra diárias gerais: a UTI tem conta própria no SIH.
            diarias[m.cnes].append(max(0, m.diarias - m.diarias_uti))
    saida = {}
    for c in set(leitos) | set(diarias):
        l, d = leitos.get(c), diarias.get(c) or []
        media = round(sum(d) / len(d)) if d else 0
        limite = (l["sus"] * 30) if l else 0
        saida[c] = {"leitos_sus": l["sus"] if l else 0, "leitos_existentes": l["existentes"] if l else 0,
                    "leitos_uti_sus": l["uti_sus"] if l else 0, "limite_diarias_mes": limite,
                    "diarias_mes": media, "ocupacao": round(media / limite, 3) if limite else None,
                    "cnes_competencia": ultima.get(c)}
    return saida


def _semelhantes(db: Session, cnes: list[str]) -> dict[str, list[dict[str, Any]]]:
    """
    Onde o hospital perde mais que os semelhantes (mesmo porte, perfil e complexidade), pelo último scan: taxa
    do hospital sobre o apresentado contra a mediana deles, em vezes e em R$ por mês. O motor só registra o que
    passa dos semelhantes.
    """
    ultimo: dict[str, str] = {}
    linhas: list[Opportunity] = []
    for lote in _lotes(cnes):
        for o in db.execute(select(Opportunity).where(Opportunity.cnes.in_(lote), Opportunity.categoria != "")).scalars():
            if "taxa_mediana_semelhantes" not in (o.evidence or {}):
                continue
            linhas.append(o)
            ultimo[o.cnes] = max(ultimo.get(o.cnes, ""), o.periodo_fim)
    saida: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for o in linhas:
        if o.periodo_fim != ultimo[o.cnes]:
            continue
        ev = o.evidence
        meses = len(ev.get("competencias") or []) or 1
        taxa, mediana = ev.get("taxa_hospital") or 0.0, ev.get("taxa_mediana_semelhantes") or 0.0
        saida[o.cnes].append({
            "categoria": o.categoria, "nome": ev.get("titulo") or o.categoria,
            "taxa_hospital": taxa, "taxa_semelhantes": mediana,
            "vezes": round(taxa / mediana, 1) if mediana > 0 else None,
            "excesso": round(o.gap or 0.0, 2), "excesso_mes": round((o.gap or 0.0) / meses, 2),
            "n_semelhantes": ev.get("n_semelhantes") or 0,
        })
    for itens in saida.values():
        itens.sort(key=lambda s: -s["excesso"])
    return saida


def _cenarios(bloco: dict[str, Any], antes_do_envio: dict[str, dict[str, float]], meses: int, percentual: float) -> dict[str, Any]:
    """
    Com e sem o software. Sem: o que o hospital já recuperou sozinho — medido. Com: projeção, em dois cenários —
    conservador (o recuperado mais as AIH de chance alta) e completo (mais tudo o que ainda está no prazo). A
    prevenção é o que o FaturaSUS teria pegado antes do envio, por mês. Honorários só sobre o que passa do que o
    hospital já recupera sozinho.
    """
    base = bloco["rejeitadas"]["valor"] - bloco["JA_APROVADA"]["valor"]
    sem = bloco["RECUPERADA"]["valor"]
    conservador = sem + bloco["ALTA"]["valor"]
    completo = sem + bloco["A_RECUPERAR"]["valor"] + bloco["DEPENDE_GESTOR"]["valor"]
    pct = (lambda v: round(v / base, 4) if base > 0 else 0.0)
    pegaria = antes_do_envio.get("PEGARIA", {}).get("valor", 0.0)
    return {
        "rejeitado": round(base, 2),
        "sem_software": {"valor": round(sem, 2), "pct": pct(sem)},
        "com_software_conservador": {"valor": round(conservador, 2), "pct": pct(conservador),
                                     "honorarios": round((conservador - sem) * percentual / 100, 2)},
        "com_software_completo": {"valor": round(completo, 2), "pct": pct(completo),
                                  "honorarios": round((completo - sem) * percentual / 100, 2)},
        "prevencao_por_mes": round(pegaria / (meses or 1), 2),
        "meses": meses,
    }


def _novo_bloco() -> dict[str, Any]:
    return {"rejeitadas": _soma(), **{g: _soma() for g in GRUPOS}, "botao": _soma(), "vence_neste_mes": _soma(),
            "ALTA": _soma()}


def _fechar_bloco(b: dict[str, Any]) -> dict[str, Any]:
    return {k: _fechar(v) for k, v in b.items()}


def _taxa(bloco: dict[str, Any], recuperado_cobravel: float, percentual: float) -> dict[str, float]:
    return {
        "sobre_recuperado": round(recuperado_cobravel * percentual / 100, 2),
        "recuperado_cobravel": round(recuperado_cobravel, 2),
        "estimada_sobre_a_recuperar": round(bloco["A_RECUPERAR"]["valor"] * percentual / 100, 2),
        "estimada_com_gestor": round((bloco["A_RECUPERAR"]["valor"] + bloco["DEPENDE_GESTOR"]["valor"]) * percentual / 100, 2),
    }


def _prevenir(por_categoria: dict[str, dict[str, Any]], meses: int) -> list[dict[str, Any]]:
    itens = []
    for codigo, p in por_categoria.items():
        categoria = POR_CODIGO.get(codigo)
        itens.append({
            "categoria": codigo, "nome": categoria.nome if categoria else codigo,
            "rejeitado": _fechar(p["rejeitado"]), "nao_volta": _fechar(p["nao_volta"]),
            "media_mensal": round(p["rejeitado"]["valor"] / meses, 2),
            "como_evitar": COMO_EVITAR.get(codigo, COMO_EVITAR["OUTROS"]),
            "motivos": [c for c, _ in sorted(p["motivos"].items(), key=lambda kv: -kv[1])[:3]],
        })
    return sorted(itens, key=lambda p: -p["media_mensal"])


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
    # Prevenção: de tudo o que foi rejeitado no período, o que o FaturaSUS teria apontado antes do envio.
    geral_antes_do_envio: dict[str, dict[str, float]] = defaultdict(_soma)
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
            "antes_do_envio": defaultdict(_soma),
            "prevenir": defaultdict(lambda: {"rejeitado": _soma(), "nao_volta": _soma(), "motivos": defaultdict(float)}),
        })
        mes = h["meses"][l["competencia"]]
        codigos = {m["codigo"] for m in l["motivos"]}
        if grupo != "JA_APROVADA":
            visto = _apontamento(prevencao.get((l["n_aih"], l["competencia"])))["grupo"]
            _somar(h["antes_do_envio"][visto], valor)
            _somar(geral_antes_do_envio[visto], valor)
            alvo_prevenir = h["prevenir"][l["categoria"]]
            _somar(alvo_prevenir["rejeitado"], valor)
            if grupo == "PERDIDA":
                _somar(alvo_prevenir["nao_volta"], valor)
            for m in l["motivos"]:
                # Só os motivos do próprio tipo: a AIH pode ter outros, contados no tipo deles.
                if categorizar([m["codigo"]]).codigo == l["categoria"]:
                    alvo_prevenir["motivos"][m["codigo"]] += valor

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
            if grupo == "A_RECUPERAR" and classe == "ALTA":
                _somar(bloco["ALTA"], valor)
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
            "antes_do_envio": _simulacao(h["antes_do_envio"]),
            "cenarios": _cenarios(h["total"], h["antes_do_envio"], len(h["meses"]), percentual),
            "apac": None,
            "prevenir": _prevenir(h["prevenir"], len(h["meses"]) or 1),
        }

    apac = _apac(db, cnes, meses)
    lista = sorted((linha_hospital(h) for h in hospitais.values()),
                   key=lambda h: -(h["total"]["A_RECUPERAR"]["valor"] + h["total"]["DEPENDE_GESTOR"]["valor"]
                                   + h["total"]["RECUPERADA"]["valor"]))
    capacidade = _capacidade(db, cnes, meses)
    semelhantes = _semelhantes(db, cnes)
    for h in lista:
        h["semelhantes"] = semelhantes.get(h["cnes"], [])
        h["apac"] = apac.get(h["cnes"])
        h["capacidade"] = capacidade.get(h["cnes"])
        if h["apac"] and h["apac"]["teto"] > 0:
            n = len(h["apac"]["meses"]) or 1
            h["prevenir"].append({"categoria": "APAC_TETO", "nome": "APAC acima do teto (SIA)",
                                  "rejeitado": {"aih": 0, "valor": h["apac"]["teto"]},
                                  "nao_volta": {"aih": 0, "valor": 0.0}, "media_mensal": round(h["apac"]["teto"] / n, 2),
                                  "como_evitar": APAC_COMO_EVITAR, "motivos": []})
            h["prevenir"].sort(key=lambda p: -p["media_mensal"])
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
        "antes_do_envio": _simulacao(geral_antes_do_envio),
        "cenarios": _cenarios(geral, geral_antes_do_envio, len({m for h in hospitais.values() for m in h["meses"]}), percentual),
        "simulacao_nomes": SIMULACAO,
        "apac": _somar_apac(list(apac.values())),
        "hospitais": lista,
        "sem_rejeicao": sorted(set(cnes) - set(hospitais)),
        "gerado_em": date.today().isoformat(),
    }


def montar_pacote(db: Session, cnes: list[str], meses: list[str], referencia: str,
                  so_aih: set[str] | None = None) -> dict[str, Any]:
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
        if so_aih is not None and l["n_aih"] not in so_aih:
            continue
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
