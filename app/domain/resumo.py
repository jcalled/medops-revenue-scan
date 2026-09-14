"""
Resumo de rejeição do SIH por hospital, organização ou UF.

Duas medidas, com nomes diferentes de propósito:

- **taxa bruta**: o que ficou fora em cada processamento. Uma AIH rejeitada em
  maio e de novo em junho conta nas duas.
- **perda líquida**: cada AIH rejeitada uma vez, e só se não aparece aprovada
  em nenhum processamento carregado. AIH rejeitada depois de paga —
  reapresentada em duplicidade — não é dinheiro perdido. É o que ainda não
  entrou até o último mês carregado.

Serve para qualquer recorte: hospitais de uma OSS, uma UF ou o Brasil
carregado inteiro.

Os dois são "receita rejeitada comprovada pelo SUS" — o registro é do SIH.
Quanto disso é recuperável é outra conta, estimada, e não sai daqui.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Establishment, ManagementOrganization, SihApprovedAih, SihErrorCode, SihHospitalMonth, SihRejection,
    SihRejectionReason,
)

RESSALVA = (
    "Dados públicos do DATASUS (SIH/SUS). A rejeição está registrada pelo SUS; quanto dela pode ser "
    "recuperado é estimativa até a validação com os dados do hospital."
)
_LOTE_IN = 500


def _lotes(itens: list[str], tamanho: int = _LOTE_IN) -> Iterator[list[str]]:
    for inicio in range(0, len(itens), tamanho):
        yield itens[inicio:inicio + tamanho]


@dataclass
class Totais:
    aih_aprovadas: int = 0
    aih_rejeitadas: int = 0
    valor_aprovado: float = 0.0
    valor_rejeitado: float = 0.0

    def somar(self, linha: SihHospitalMonth) -> None:
        self.aih_aprovadas += linha.aih_aprovadas
        self.aih_rejeitadas += linha.aih_rejeitadas
        self.valor_aprovado += float(linha.valor_aprovado or 0)
        self.valor_rejeitado += float(linha.valor_rejeitado or 0)

    @property
    def taxa_valor(self) -> float:
        total = self.valor_aprovado + self.valor_rejeitado
        return round(self.valor_rejeitado / total, 4) if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        total_aih = self.aih_aprovadas + self.aih_rejeitadas
        return {
            "aih_aprovadas": self.aih_aprovadas,
            "aih_rejeitadas": self.aih_rejeitadas,
            "valor_aprovado": round(self.valor_aprovado, 2),
            "valor_rejeitado": round(self.valor_rejeitado, 2),
            "taxa_aih": round(self.aih_rejeitadas / total_aih, 4) if total_aih else 0.0,
            "taxa_valor": self.taxa_valor,
        }


def competencias_carregadas(db: Session, *, cnes: list[str] | None = None, uf: str | None = None) -> list[str]:
    consulta = select(SihHospitalMonth.competencia).distinct()
    if uf:
        consulta = consulta.where(SihHospitalMonth.uf == uf)
    if cnes is not None:
        consulta = consulta.where(SihHospitalMonth.cnes.in_(cnes or ["-"]))
    return sorted(db.execute(consulta).scalars())


def _recorte(db: Session, competencias: list[str] | None, limite_meses: int | None, **filtro: Any) -> list[str]:
    disponiveis = competencias_carregadas(db, **filtro)
    meses = [c for c in disponiveis if not competencias or c in set(competencias)]
    return meses[-limite_meses:] if limite_meses else meses


def resumo(db: Session, *, cnes: list[str] | None = None, uf: str | None = None,
           competencias: list[str] | None = None, limite_meses: int | None = None,
           siglas: dict[str, str] | None = None) -> dict[str, Any]:
    """
    Totais, meses, hospitais, perda líquida e motivos.

    `cnes` recorta hospitais (uma OSS), `uf` recorta o estado; sem nenhum dos
    dois, é o Brasil carregado.
    """
    meses = _recorte(db, competencias, limite_meses, cnes=cnes, uf=uf)
    siglas = siglas or {}

    def filtrar(consulta, modelo):
        consulta = consulta.where(modelo.competencia.in_(meses or ["-"]))
        if uf:
            consulta = consulta.where(modelo.uf == uf)
        if cnes is not None:
            consulta = consulta.where(modelo.cnes.in_(cnes or ["-"]))
        return consulta

    linhas = db.execute(filtrar(select(SihHospitalMonth), SihHospitalMonth)).scalars().all()
    total, por_mes = Totais(), defaultdict(Totais)
    hospitais: dict[str, dict[str, Any]] = {}
    for linha in linhas:
        total.somar(linha)
        por_mes[linha.competencia].somar(linha)
        h = hospitais.setdefault(linha.cnes, {"total": Totais(), "meses": defaultdict(Totais)})
        h["total"].somar(linha)
        h["meses"][linha.competencia].somar(linha)

    perdas = _perda_liquida(db, filtrar(
        select(SihRejection.n_aih, SihRejection.cnes, SihRejection.competencia, SihRejection.valor), SihRejection))
    motivos = _motivos(db, filtrar(
        select(SihRejectionReason.cnes, SihRejectionReason.codigo_erro,
               func.count(func.distinct(SihRejectionReason.n_aih)))
        .group_by(SihRejectionReason.cnes, SihRejectionReason.codigo_erro), SihRejectionReason))

    nomes = _nomes(db, list(hospitais))
    lista = []
    for numero, h in hospitais.items():
        p = perdas["por_hospital"].get(numero, Counter())
        taxas = [t.taxa_valor for t in h["meses"].values()]
        lista.append({
            "cnes": numero,
            "sigla": siglas.get(numero),
            "nome": nomes.get(numero),
            "total": h["total"].to_dict(),
            "meses": {m: t.to_dict() for m, t in sorted(h["meses"].items())},
            "media_taxa_valor": round(sum(taxas) / len(taxas), 4) if taxas else 0.0,
            "rejeitado_mes": round(h["total"].valor_rejeitado / len(meses), 2) if meses else 0.0,
            "rejeitadas_unicas": p["unicas"],
            "voltaram_aprovadas": p["voltaram"],
            "perda_liquida_aih": p["perda_aih"],
            "perda_liquida_valor": round(p["perda_valor"], 2),
            "motivos": motivos.get(numero, [])[:5],
        })
    lista.sort(key=lambda x: -x["rejeitado_mes"])

    geral = perdas["geral"]
    return {
        "competencias": meses,
        "total": total.to_dict(),
        "meses": {m: t.to_dict() for m, t in sorted(por_mes.items())},
        "hospitais": lista,
        "rejeitadas_unicas": geral["unicas"],
        "voltaram_aprovadas": geral["voltaram"],
        "perda_liquida_aih": geral["perda_aih"],
        "perda_liquida_valor": round(geral["perda_valor"], 2),
        "classe_dado": "PUBLICO",
        "ressalva": RESSALVA,
    }


def _perda_liquida(db: Session, consulta) -> dict[str, Any]:
    ultima: dict[str, tuple[str, str, float]] = {}
    for n_aih, cnes, competencia, valor in db.execute(consulta):
        if n_aih not in ultima or competencia >= ultima[n_aih][0]:
            ultima[n_aih] = (competencia, cnes, float(valor or 0))

    # Aprovada em qualquer processamento: o número da AIH é a internação, e ela
    # é paga uma vez. No Ceará (mai–jul/2026), 128 AIH foram aprovadas e depois
    # rejeitadas na reapresentação — contá-las como perda inflaria o scan.
    aprovadas: set[str] = set()
    for lote in _lotes(list(ultima)):
        aprovadas.update(db.execute(select(SihApprovedAih.n_aih).where(SihApprovedAih.n_aih.in_(lote))).scalars())

    geral: Counter = Counter()
    por_hospital: dict[str, Counter] = defaultdict(Counter)
    for n_aih, (_competencia, cnes, valor) in ultima.items():
        voltou = n_aih in aprovadas
        for alvo in (geral, por_hospital[cnes]):
            alvo["unicas"] += 1
            if voltou:
                alvo["voltaram"] += 1
            else:
                alvo["perda_aih"] += 1
                alvo["perda_valor"] += valor
    return {"geral": geral, "por_hospital": por_hospital}


def _motivos(db: Session, consulta) -> dict[str, list[dict[str, Any]]]:
    linhas = db.execute(consulta).all()
    descricoes = dict(db.execute(
        select(SihErrorCode.codigo, SihErrorCode.descricao)
        .where(SihErrorCode.codigo.in_({codigo for _, codigo, _ in linhas} or {"-"}))
    ).all())
    saida: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for cnes, codigo, quantidade in sorted(linhas, key=lambda l: -l[2]):
        saida[cnes].append({"codigo": codigo, "descricao": descricoes.get(codigo), "aih": quantidade})
    return saida


def _nomes(db: Session, cnes: Iterable[str]) -> dict[str, str]:
    lista = list(cnes)
    nomes: dict[str, str] = {}
    for lote in _lotes(lista):
        for numero, fantasia, razao in db.execute(
            select(Establishment.cnes, Establishment.nome_fantasia, Establishment.razao_social)
            .where(Establishment.cnes.in_(lote))
        ):
            nomes[numero] = fantasia or razao
    return nomes


def resumo_organizacao(db: Session, org: ManagementOrganization, *, cnes_permitidos: set[str] | None = None,
                       competencias: list[str] | None = None, limite_meses: int | None = None) -> dict[str, Any]:
    unidades = [u for u in org.unidades if cnes_permitidos is None or u.cnes in cnes_permitidos]
    corpo = resumo(db, cnes=[u.cnes for u in unidades], competencias=competencias, limite_meses=limite_meses,
                   siglas={u.cnes: u.sigla for u in unidades if u.sigla})
    sem_producao = sorted({u.cnes for u in unidades} - {h["cnes"] for h in corpo["hospitais"]})
    return {
        "organizacao": {"id": org.id, "sigla": org.sigla, "nome": org.nome, "uf": org.uf, "fonte": org.fonte},
        "unidades": [{"cnes": u.cnes, "sigla": u.sigla, "situacao": u.situacao, "fonte": u.fonte,
                      "verificado_em": u.verificado_em.isoformat() if u.verificado_em else None} for u in unidades],
        # Hospital da organização sem AIH no recorte: não é "zero rejeição", é "sem dado".
        "sem_producao_no_periodo": sem_producao,
        **corpo,
    }
