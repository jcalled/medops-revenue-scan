"""
Prospecção de organizações: importação da planilha de pesquisa e sugestão de
CNES para as unidades que a organização cita só pelo nome.

A planilha (aba com a coluna "Organização") traz o que é público: ranking,
presença, unidades citadas, liderança, contato, site e fonte. Reimportar
atualiza isso e preserva o andamento comercial (etapa, contato, próxima ação).
"""
from __future__ import annotations

import re
import unicodedata
from io import BytesIO
from typing import Any

import openpyxl
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.ibge import CODIGO_UF
from app.models import Establishment, Prospect, ProspectEvent

ETAPAS = {
    "MAPEADA": "Mapeada",
    "CONTATO": "Primeiro contato",
    "REUNIAO": "Reunião",
    "DIAGNOSTICO": "Diagnóstico apresentado",
    "PROPOSTA": "Proposta enviada",
    "NEGOCIACAO": "Negociação",
    "FECHADA": "Contrato fechado",
    "PERDIDA": "Perdida",
}

# Cabeçalho da planilha (sem acento, minúsculo) → campo. Começo do texto basta:
# "Situação / escopo 2026" muda de ano.
_COLUNAS = (
    ("rank", "rank"), ("uf principal", "uf"), ("organizacao", "nome"), ("presenca", "presenca"),
    ("situacao", "situacao_escopo"), ("hospitais confirmados", "hospitais_confirmados"), ("rede", "rede"),
    ("principais unidades", "principais_unidades"), ("lideranca", "lideranca"), ("contato", "contato_publico"),
    ("site", "site"), ("fonte", "fonte"), ("score", "score"), ("prioridade", "prioridade"), ("fit", "fit"),
    ("confianca", "confianca"), ("proxima acao", "proxima_acao"), ("status", "status"), ("owner", "responsavel"),
)
PUBLICOS = ("uf", "rank", "score", "prioridade", "presenca", "situacao_escopo", "hospitais_confirmados", "rede",
            "principais_unidades", "lideranca", "contato_publico", "site", "fonte", "fit", "confianca")
_STATUS = {
    "nao contatado": "MAPEADA", "contatado": "CONTATO", "em contato": "CONTATO", "reuniao": "REUNIAO",
    "diagnostico": "DIAGNOSTICO", "proposta": "PROPOSTA", "negociacao": "NEGOCIACAO", "fechado": "FECHADA",
    "fechada": "FECHADA", "perdido": "PERDIDA", "perdida": "PERDIDA",
}
_INTEIROS = {"rank", "score", "hospitais_confirmados"}
_LIMITES = {"uf": 2, "prioridade": 4, "presenca": 255, "situacao_escopo": 255, "lideranca": 255, "site": 255,
            "fonte": 500, "fit": 255, "confianca": 20, "responsavel": 120, "nome": 255}


class PlanilhaInvalida(ValueError):
    pass


def normalizar(texto: str | None) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", sem_acento.lower()).strip()


def sigla_sugerida(nome: str) -> str | None:
    """'SPDM – Associação Paulista…' → 'SPDM'. Sem travessão não dá para adivinhar."""
    partes = re.split(r"\s+[–—-]\s+", nome, maxsplit=1)
    candidata = partes[0].strip() if len(partes) == 2 else ""
    return candidata if 2 <= len(candidata) <= 30 and len(candidata.split()) <= 3 else None


def _valor(campo: str, bruto: Any) -> Any:
    if bruto is None or (isinstance(bruto, str) and not bruto.strip()):
        return None
    if campo in _INTEIROS:
        try:
            return int(float(str(bruto).strip()))
        except ValueError:
            return None
    texto = str(bruto).strip()
    if campo == "uf":
        texto = texto.upper()
    return texto[:_LIMITES[campo]] if campo in _LIMITES else texto


def ler_planilha(conteudo: bytes) -> list[dict[str, Any]]:
    try:
        livro = openpyxl.load_workbook(BytesIO(conteudo), data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise PlanilhaInvalida("Não consegui abrir a planilha: envie o .xlsx.") from exc
    for aba in livro.worksheets:
        linhas = list(aba.iter_rows(values_only=True))
        for indice, linha in enumerate(linhas[:15]):
            cabecalho = [normalizar(str(c)) if c is not None else "" for c in linha]
            if "organizacao" not in cabecalho:
                continue
            campos: dict[int, str] = {}
            for posicao, titulo in enumerate(cabecalho):
                for prefixo, campo in _COLUNAS:
                    if titulo.startswith(prefixo) and campo not in campos.values():
                        campos[posicao] = campo
                        break
            saida = []
            for bruta in linhas[indice + 1:]:
                registro = {campo: _valor(campo, bruta[pos] if pos < len(bruta) else None) for pos, campo in campos.items()}
                if registro.get("nome"):
                    saida.append(registro)
            return saida
    raise PlanilhaInvalida("Nenhuma aba com a coluna 'Organização'.")


def importar(db: Session, registros: list[dict[str, Any]], usuario: int | None) -> dict[str, int]:
    existentes = {normalizar(p.nome): p for p in db.execute(select(Prospect)).scalars()}
    criados = atualizados = 0
    for r in registros:
        chave = normalizar(r["nome"])
        p = existentes.get(chave)
        if p is None:
            p = Prospect(nome=r["nome"], etapa=_STATUS.get(normalizar(r.get("status")), "MAPEADA"),
                         proxima_acao=r.get("proxima_acao"), responsavel=r.get("responsavel"))
            db.add(p)
            p.eventos.append(ProspectEvent(tipo="IMPORTACAO", texto="Importada da planilha de prospecção.",
                                           criado_por=usuario))
            existentes[chave] = p
            criados += 1
        else:
            atualizados += 1
            # Andamento comercial é de quem prospecta: só preenche se estiver vazio.
            p.proxima_acao = p.proxima_acao or r.get("proxima_acao")
            p.responsavel = p.responsavel or r.get("responsavel")
        for campo in PUBLICOS:
            if campo in r:
                setattr(p, campo, r[campo])
    db.commit()
    return {"criados": criados, "atualizados": atualizados}


_PALAVRAS_VAZIAS = {
    "hospital", "hosp", "hospitais", "unidade", "unidades", "publicas", "publicos", "servicos", "outros", "entre",
    "estadual", "municipal", "geral", "regional", "centro", "atual", "atuais", "rede", "portal", "exibidas",
}


def _unidades_citadas(texto: str | None) -> list[str]:
    partes = re.split(r"[;\n]|,(?![^()]*\))", texto or "")
    return [p.strip(" .") for p in partes if p.strip(" .")]


def _termos(citado: str) -> list[str]:
    # "no RJ: Mariska Ribeiro" → só o nome.
    citado = citado.split(":")[-1]
    return [t for t in normalizar(citado).split() if len(t) >= 4 and t not in _PALAVRAS_VAZIAS]


def ufs_do_prospect(p: Prospect) -> list[str]:
    ufs = [p.uf] if p.uf else []
    ufs += [u for u in re.findall(r"\b([A-Z]{2})\b", p.presenca or "") if u in CODIGO_UF]
    return list(dict.fromkeys(u for u in ufs if u))


def sugerir_unidades(db: Session, p: Prospect, vinculados: set[str], limite: int = 5) -> list[dict[str, Any]]:
    """
    Para cada unidade citada, os hospitais carregados das UFs da organização com
    todas as palavras do nome. É sugestão: o vínculo só vale depois de conferido.
    """
    ufs = ufs_do_prospect(p)
    if not ufs:
        return []
    estabelecimentos = [
        (e, set(normalizar(f"{e.nome_fantasia or ''} {e.razao_social or ''}").split()))
        for e in db.execute(select(Establishment).where(Establishment.uf.in_(ufs))).scalars()
    ]
    saida = []
    for citado in _unidades_citadas(p.principais_unidades):
        termos = _termos(citado)
        if not termos:
            continue
        candidatos = [e for e, palavras in estabelecimentos if all(t in palavras for t in termos)]
        candidatos.sort(key=lambda e: (e.cnes not in vinculados, len(e.nome_fantasia or "")))
        saida.append({
            "citado": citado,
            "candidatos": [{"cnes": e.cnes, "nome": e.nome_fantasia, "uf": e.uf, "vinculado": e.cnes in vinculados}
                           for e in candidatos[:limite]],
        })
    return saida


def contar_por_etapa(db: Session) -> dict[str, int]:
    contagem = dict(db.execute(select(Prospect.etapa, func.count(Prospect.id)).group_by(Prospect.etapa)).all())
    return {etapa: contagem.get(etapa, 0) for etapa in ETAPAS}
