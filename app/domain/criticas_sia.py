"""
Leitura do relatório de críticas do SIA que a secretaria devolve ao hospital.

Não há layout oficial único: cada gestor manda planilha, CSV ou relatório em
texto. O leitor procura, linha a linha, o número da APAC (13 dígitos), o
procedimento (10 dígitos, começa com 0), o valor (R$ 1.234,56) e o texto do erro;
com cabeçalho reconhecido, usa as colunas. O DATASUS não publica a tabela de
críticas do SIA, então o grupo sai da descrição do erro — e a tela diz isso.

O que é teto ou orçamento não se corrige na APAC: negocia-se com o gestor. O
resto é erro de preenchimento ou de cadastro, e volta reapresentando em até seis
meses do atendimento (Portaria SAES/MS 1.110/2021).
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections import defaultdict
from typing import Any

GRUPOS: dict[str, dict[str, Any]] = {
    "TETO": {
        "nome": "Teto ou orçamento do gestor",
        "corrigivel": False,
        "o_que_fazer": "Não se corrige na APAC: renegociar a programação (teto físico ou financeiro) com a secretaria.",
        "palavras": ("teto", "orcament", "limite financ", "limite fisic", "programacao", "ppi"),
    },
    "PACIENTE": {
        "nome": "Dados do paciente",
        "corrigivel": True,
        "o_que_fazer": "Corrigir CNS, nome, nascimento, sexo ou endereço do paciente na APAC e reapresentar.",
        "palavras": ("cns do paciente", "cartao", "cpf", "nascimento", "sexo", "idade", "paciente", "usuario", "cep", "municipio de resid"),
    },
    "PROFISSIONAL": {
        "nome": "Profissional, CBO ou vínculo",
        "corrigivel": True,
        "o_que_fazer": "Conferir no CNES o vínculo e o CBO do profissional; corrigir a APAC ou o CNES da competência e reapresentar.",
        "palavras": ("cbo", "profissional", "medico", "vinculo", "cns do prof", "executante", "solicitante"),
    },
    "COMPATIBILIDADE": {
        "nome": "Procedimento, CID ou quantidade",
        "corrigivel": True,
        "o_que_fazer": "Conferir no SIGTAP a compatibilidade do procedimento com o CID, a quantidade máxima e os secundários; corrigir e reapresentar.",
        "palavras": ("compativ", "incompat", "cid", "quantidade", "excede", "procedimento principal", "procedimento secundario",
                     "sigtap", "permitid", "atributo", "complexidade", "instrumento de registro"),
    },
    "AUTORIZACAO": {
        "nome": "Autorização, validade ou duplicidade",
        "corrigivel": True,
        "o_que_fazer": "Conferir o número da APAC, a validade e se já foi apresentada; pedir nova autorização ou corrigir a competência e reapresentar.",
        "palavras": ("autoriza", "validade", "vencid", "duplic", "ja apresentad", "numero da apac", "faixa", "continuidade"),
    },
    "HABILITACAO": {
        "nome": "Habilitação ou serviço no CNES",
        "corrigivel": False,
        "o_que_fazer": "Conferir habilitação e serviço/classificação no CNES; sem habilitação vigente na competência, pedir à secretaria.",
        "palavras": ("habilita", "servico", "classificacao", "cnes", "estabelecimento nao"),
    },
    "OUTROS": {
        "nome": "Outros erros",
        "corrigivel": False,
        "o_que_fazer": "Pedir à secretaria o significado do erro e a regra que o dispara.",
        "palavras": (),
    },
}
# Ordem da conferência: teto primeiro (não se corrige), depois o que é mais específico.
_ORDEM = ("TETO", "PROFISSIONAL", "PACIENTE", "AUTORIZACAO", "COMPATIBILIDADE", "HABILITACAO")

_APAC = re.compile(r"(?<!\d)(\d{13})(?!\d)")
_PROC = re.compile(r"(?<!\d)(0\d{9})(?!\d)")
_VALOR = re.compile(r"(?<![\d,.])(\d{1,3}(?:\.\d{3})*,\d{2})(?![\d,])")
MAX_ITENS = 3000

# Coluna pelo que o nome contém, na ordem: o código do erro antes da descrição, que também diz "erro".
_COLUNAS = (
    ("codigo", lambda c: "cod" in c and ("erro" in c or "critica" in c)),
    ("erro", lambda c: any(p in c for p in ("erro", "critica", "descri", "motivo", "ocorrenc", "mensagem"))),
    ("apac", lambda c: "apac" in c or "autoriza" in c),
    ("procedimento", lambda c: "proced" in c or c == "proc"),
    ("valor", lambda c: "valor" in c or c.startswith("vl")),
    ("competencia", lambda c: "compet" in c or "cmpt" in c),
)


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(c))


def _casa(palavra: str, texto: str) -> bool:
    # Início de palavra sempre ("idade" não casa com "validade"); sigla curta, palavra inteira ("cid" ≠ "desconhecida").
    fim = r"\b" if len(palavra) <= 4 else ""
    return re.search(rf"\b{re.escape(palavra)}{fim}", texto) is not None


def classificar(descricao: str) -> str:
    texto = _sem_acento(descricao or "")
    for grupo in _ORDEM:
        if any(_casa(p, texto) for p in GRUPOS[grupo]["palavras"]):
            return grupo
    return "OUTROS"


def _valor(texto: str) -> float:
    try:
        return float(texto.replace(".", "").replace(",", "."))
    except (ValueError, AttributeError):
        return 0.0


def _linhas_tabela(conteudo: bytes, nome: str) -> list[list[str]]:
    if nome.lower().endswith(".xlsx"):
        from openpyxl import load_workbook

        planilha = load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
        linhas: list[list[str]] = []
        for aba in planilha.worksheets:
            for linha in aba.iter_rows(values_only=True):
                linhas.append(["" if c is None else (f"{c:.2f}".replace(".", ",") if isinstance(c, float) else str(c)) for c in linha])
        return linhas
    texto = conteudo.decode("utf-8-sig", errors="replace") if b"\xc3" in conteudo[:20000] else conteudo.decode("latin-1")
    primeira = next((l for l in texto.splitlines() if l.strip()), "")
    separador = max((";", "\t", ",", "|"), key=primeira.count)
    if primeira.count(separador) >= 2:
        return [l for l in csv.reader(io.StringIO(texto), delimiter=separador)]
    return [[l] for l in texto.splitlines()]


def _colunas(cabecalho: list[str]) -> dict[str, int]:
    achado: dict[str, int] = {}
    normal = [_sem_acento(str(c)).strip().strip(":") for c in cabecalho]
    for campo, casa in _COLUNAS:
        for i, c in enumerate(normal):
            if c and i not in achado.values() and casa(c):
                achado[campo] = i
                break
    return achado if "apac" in achado and ("erro" in achado or "codigo" in achado) else {}


def ler(conteudo: bytes, nome: str) -> dict[str, Any]:
    """APAC criticadas, agrupadas pelo tipo de erro, com o que fazer e o que volta por correção."""
    linhas = _linhas_tabela(conteudo, nome)
    colunas: dict[str, int] = {}
    inicio = 0
    for i, linha in enumerate(linhas[:30]):
        colunas = _colunas(linha)
        if colunas:
            inicio = i + 1
            break

    itens: list[dict[str, Any]] = []
    for linha in linhas[inicio:]:
        if colunas:
            def pega(campo: str) -> str:
                i = colunas.get(campo)
                return str(linha[i]).strip() if i is not None and i < len(linha) else ""
            apac = re.sub(r"\D", "", pega("apac"))
            if len(apac) != 13:
                continue
            erro = " ".join(x for x in (pega("codigo"), pega("erro")) if x)
            itens.append({"apac": apac, "procedimento": re.sub(r"\D", "", pega("procedimento"))[:10] or None,
                          "erro": erro[:300], "valor": _valor(pega("valor")), "competencia": re.sub(r"\D", "", pega("competencia"))[:6] or None})
        else:
            texto = " ".join(str(c) for c in linha)
            achou = _APAC.search(texto)
            if not achou:
                continue
            resto = texto.replace(achou.group(1), " ")
            proc = _PROC.search(resto)
            if proc:
                resto = resto.replace(proc.group(1), " ")
            valores = _VALOR.findall(resto)
            for v in valores:
                resto = resto.replace(v, " ")
            erro = re.sub(r"\s+", " ", resto).strip(" -;|:")
            itens.append({"apac": achou.group(1), "procedimento": proc.group(1) if proc else None, "erro": erro[:300],
                          "valor": _valor(valores[-1]) if valores else 0.0, "competencia": None})

    grupos: dict[str, dict[str, Any]] = {g: {"linhas": 0, "apac": set(), "valor": 0.0} for g in GRUPOS}
    erros: dict[str, dict[str, Any]] = defaultdict(lambda: {"linhas": 0, "valor": 0.0, "grupo": "OUTROS"})
    for item in itens:
        item["grupo"] = classificar(item["erro"])
        g = grupos[item["grupo"]]
        g["linhas"] += 1
        g["apac"].add(item["apac"])
        g["valor"] += item["valor"]
        chave = item["erro"] or "Sem descrição"
        erros[chave]["linhas"] += 1
        erros[chave]["valor"] += item["valor"]
        erros[chave]["grupo"] = item["grupo"]

    corrigiveis = [i for i in itens if GRUPOS[i["grupo"]]["corrigivel"]]
    return {
        "formato": "PLANILHA" if colunas else "TEXTO",
        "linhas_lidas": len(itens),
        "apac": len({i["apac"] for i in itens}),
        "valor_total": round(sum(i["valor"] for i in itens), 2),
        "valor_informado": any(i["valor"] for i in itens),
        "corrigivel": {"apac": len({i["apac"] for i in corrigiveis}), "valor": round(sum(i["valor"] for i in corrigiveis), 2)},
        "grupos": [{"grupo": g, "nome": GRUPOS[g]["nome"], "corrigivel": GRUPOS[g]["corrigivel"],
                    "o_que_fazer": GRUPOS[g]["o_que_fazer"], "linhas": v["linhas"], "apac": len(v["apac"]),
                    "valor": round(v["valor"], 2)}
                   for g, v in sorted(grupos.items(), key=lambda kv: (-kv[1]["valor"], -kv[1]["linhas"])) if v["linhas"]],
        "erros": [{"erro": e, "grupo": v["grupo"], "linhas": v["linhas"], "valor": round(v["valor"], 2)}
                  for e, v in sorted(erros.items(), key=lambda kv: (-kv[1]["valor"], -kv[1]["linhas"]))[:30]],
        "itens": sorted(itens, key=lambda i: (not GRUPOS[i["grupo"]]["corrigivel"], -i["valor"]))[:MAX_ITENS],
        "leitura": "Grupo definido pela descrição do erro: o DATASUS não publica a tabela de críticas do SIA. Confira os casos marcados como outros.",
    }
