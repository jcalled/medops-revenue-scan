"""
SIA/SUS: produção ambulatorial (arquivo PA), só a parte de APAC.

No PA a APAC vem com PA_DOCORIG P (procedimento principal) ou S (secundário).
PA_INDICA diz se foi aprovada (5 total, 6 parcial, 0 não aprovada) e o par
PA_CODOCO + PA_FLQT diz o porquê, pela tabela CODOCO.CNV do TabWin (TAB_SIA do
DATASUS; Informe Técnico SIASUS 2019-07). O não aprovado que chega ao público é
quase todo teto: produção acima do orçamento programado pelo gestor — não é erro
de preenchimento, não volta por correção; volta por negociação do teto. A APAC
barrada na consistência não chega ao PA: só o relatório de críticas do hospital
mostra.

UF grande sai partida: PASP2607a.dbc, PASP2607b.dbc…
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

PASTA = "/dissemin/publicos/SIASUS/200801_/Dados"
FONTE = "SIA_PA"
_NOME = re.compile(r"^PA([A-Z]{2})(\d{2})(\d{2})([a-z]?)\.DBC$", re.IGNORECASE)
DOCUMENTOS_APAC = frozenset({"P", "S"})
PROCEDIMENTOS_GUARDADOS = 10

# CODOCO.CNV (TabWin/SIA): ocorrência = PA_CODOCO + PA_FLQT.
OCORRENCIAS: dict[str, tuple[str, str]] = {
    "1K": ("APROVADA", "Aprovado totalmente"),
    "1R": ("APROVADA", "Aprovado, teto financeiro"),
    "1S": ("APROVADA", "Aprovado, teto financeiro da competência atual"),
    "2L": ("PARCIAL", "Ultrapassou o teto físico"),
    "3M": ("PARCIAL", "Ultrapassou o teto financeiro"),
    "3T": ("PARCIAL", "Teto financeiro da competência atual"),
    "4P": ("NAO_APROVADA", "Procedimento sem orçamento"),
    "4Q": ("NAO_APROVADA", "Procedimento sem valor unitário"),
    "4N": ("NAO_APROVADA", "Ultrapassou o teto físico"),
    "5O": ("NAO_APROVADA", "Ultrapassou o teto financeiro"),
}
# O que é teto ou orçamento do gestor: não se corrige na APAC, negocia-se.
TETO = frozenset({"2L", "3M", "3T", "4P", "4N", "5O"})


def arquivos_da_competencia(nomes: Iterable[str], uf: str, competencia: str) -> list[str]:
    """Os arquivos PA de uma UF e competência (AAAAMM), inclusive as partes a, b, c…"""
    alvo = (uf.upper(), competencia[2:4], competencia[4:6])
    return sorted(n for n in nomes if (m := _NOME.match(n)) and (m.group(1).upper(), m.group(2), m.group(3)) == alvo)


def competencias_da_listagem(nomes: Iterable[str], uf: str) -> list[str]:
    saida = set()
    for n in nomes:
        m = _NOME.match(n)
        if m and m.group(1).upper() == uf.upper():
            saida.add(f"20{m.group(2)}{m.group(3)}")
    return sorted(saida)


def _numero(valor: Any) -> float:
    try:
        return float(valor or 0)
    except (TypeError, ValueError):
        return 0.0


def agregar(linhas: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Por CNES: APAC produzida, aprovada e o que não foi aprovado, por ocorrência e por procedimento."""
    por_cnes: dict[str, dict[str, Any]] = {}
    for linha in linhas:
        if str(linha.get("PA_DOCORIG") or "").strip().upper() not in DOCUMENTOS_APAC:
            continue
        cnes = str(linha.get("PA_CODUNI") or "").strip().zfill(7)
        if not cnes.strip("0"):
            continue
        produzido, aprovado = _numero(linha.get("PA_VALPRO")), _numero(linha.get("PA_VALAPR"))
        h = por_cnes.setdefault(cnes, {"linhas": 0, "valor_produzido": 0.0, "valor_aprovado": 0.0,
                                       "valor_nao_aprovado": 0.0, "valor_teto": 0.0,
                                       "ocorrencias": defaultdict(lambda: {"linhas": 0, "valor": 0.0}),
                                       "procedimentos": defaultdict(float)})
        h["linhas"] += 1
        h["valor_produzido"] += produzido
        h["valor_aprovado"] += aprovado
        glosado = produzido - aprovado
        if glosado <= 0.005:
            continue
        ocorrencia = f"{str(linha.get('PA_CODOCO') or '').strip()}{str(linha.get('PA_FLQT') or '').strip().upper()}"
        h["valor_nao_aprovado"] += glosado
        if ocorrencia in TETO:
            h["valor_teto"] += glosado
        h["ocorrencias"][ocorrencia]["linhas"] += 1
        h["ocorrencias"][ocorrencia]["valor"] += glosado
        h["procedimentos"][str(linha.get("PA_PROC_ID") or "").strip()] += glosado

    for h in por_cnes.values():
        h["ocorrencias"] = {k: {"linhas": v["linhas"], "valor": round(v["valor"], 2),
                                "nome": OCORRENCIAS.get(k, ("", f"Ocorrência {k}"))[1]}
                            for k, v in h["ocorrencias"].items()}
        h["procedimentos"] = [{"procedimento": p, "valor": round(v, 2)}
                              for p, v in sorted(h["procedimentos"].items(), key=lambda kv: -kv[1])[:PROCEDIMENTOS_GUARDADOS]]
        for chave in ("valor_produzido", "valor_aprovado", "valor_nao_aprovado", "valor_teto"):
            h[chave] = round(h[chave], 2)
    return por_cnes
