"""
Perfil de cada hospital num período, montado do que foi carregado.

O motor não consulta o banco: recebe os perfis de todos os hospitais do período
(de todas as UFs carregadas) e compara. Assim o semelhante de um hospital do
Ceará pode estar em Pernambuco quando o estado não tem hospitais parecidos o
bastante.
"""
from __future__ import annotations

import calendar
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.ibge import CODIGO_UF
from app.domain.resumo import _lotes
from app.engine.categorias import categorizar
from app.models import (
    CnesBed, CnesEnablement, Establishment, SihApprovedAih, SihHospitalMonth, SihHospitalProcedureMonth,
    SihRejection, SihRejectionReason,
)

_REGIOES = {"1": "Norte", "2": "Nordeste", "3": "Sudeste", "4": "Sul", "5": "Centro-Oeste"}
TIPO_LEITO_COMPLEMENTAR = "3"


def regiao(uf: str) -> str:
    return _REGIOES.get(CODIGO_UF.get(uf or "", "")[:1], "não informada")


def porte(leitos_sus: int) -> str:
    if leitos_sus <= 0:
        return "sem leitos no CNES"
    if leitos_sus <= 50:
        return "até 50 leitos"
    if leitos_sus <= 150:
        return "51 a 150 leitos"
    if leitos_sus <= 300:
        return "151 a 300 leitos"
    return "mais de 300 leitos"


def natureza(codigo: str | None) -> str:
    """Natureza jurídica do CNES pelo primeiro dígito do código."""
    return {"1": "pública", "2": "empresarial", "3": "sem fins lucrativos"}.get((codigo or "")[:1], "não informada")


def faixa_complexidade(participacao: float | None) -> str:
    """Faixa da participação de AIH de alta complexidade — separa hospital regional de hospital distrital."""
    if participacao is None or participacao < 0.02:
        return "sem alta complexidade"
    if participacao < 0.10:
        return "alta complexidade até 10%"
    if participacao < 0.25:
        return "alta complexidade de 10% a 25%"
    return "alta complexidade acima de 25%"


def dias_no_periodo(competencias: list[str]) -> int:
    return sum(calendar.monthrange(int(c[:4]), int(c[4:]))[1] for c in competencias)


@dataclass
class Perdas:
    """AIH rejeitadas e não recuperadas de uma categoria."""

    aih: int = 0
    valor: float = 0.0
    motivos: Counter = field(default_factory=Counter)


@dataclass
class Perfil:
    cnes: str
    uf: str = ""
    natureza: str = "não informada"
    leitos_sus: int = 0
    leitos_sus_gerais: int = 0
    leitos_uti_sus: int = 0
    habilitacoes: set[str] = field(default_factory=set)
    dias_periodo: int = 0
    aih_aprovadas: int = 0
    aih_rejeitadas: int = 0
    valor_aprovado: float = 0.0
    valor_rejeitado: float = 0.0
    diarias: int = 0
    permanencia_dias: int = 0
    aih_alta_complexidade: int = 0
    # competência -> {aih_aprovadas, valor_aprovado, aih_rejeitadas, valor_rejeitado}
    meses: dict[str, dict[str, float]] = field(default_factory=dict)
    # procedimento -> [aih, valor, dias de permanência]
    procedimentos: dict[str, list[float]] = field(default_factory=dict)
    # categoria de motivo -> perdas
    perdas: dict[str, Perdas] = field(default_factory=dict)
    # participação de cada subgrupo do SIGTAP nas AIH, normalizada
    mix: dict[str, float] = field(default_factory=dict)
    norma_mix: float = 0.0

    @property
    def regiao(self) -> str:
        return regiao(self.uf)

    @property
    def porte(self) -> str:
        return porte(self.leitos_sus)

    @property
    def faixa_complexidade(self) -> str:
        return faixa_complexidade(self.alta_complexidade)

    @property
    def valor_apresentado(self) -> float:
        return self.valor_aprovado + self.valor_rejeitado

    @property
    def perda_valor(self) -> float:
        return sum(p.valor for p in self.perdas.values())

    @property
    def meses_com_producao(self) -> int:
        return max(1, len(self.meses))

    @property
    def aih_mes(self) -> float:
        return self.aih_aprovadas / self.meses_com_producao

    @property
    def ticket_medio(self) -> float | None:
        return self.valor_aprovado / self.aih_aprovadas if self.aih_aprovadas else None

    @property
    def permanencia_media(self) -> float | None:
        return self.permanencia_dias / self.aih_aprovadas if self.aih_aprovadas else None

    @property
    def alta_complexidade(self) -> float | None:
        return self.aih_alta_complexidade / self.aih_aprovadas if self.aih_aprovadas else None

    @property
    def ocupacao(self) -> float | None:
        capacidade = self.leitos_sus_gerais * self.dias_periodo
        return self.diarias / capacidade if capacidade else None

    @property
    def taxa_rejeicao_valor(self) -> float | None:
        return self.valor_rejeitado / self.valor_apresentado if self.valor_apresentado else None

    @property
    def perda_liquida_pct(self) -> float | None:
        return self.perda_valor / self.valor_apresentado if self.valor_apresentado else None


def atualizar_mix(perfil: Perfil) -> None:
    total = sum(v[0] for v in perfil.procedimentos.values())
    mix: dict[str, float] = defaultdict(float)
    if total:
        for proc, valores in perfil.procedimentos.items():
            mix[proc[:4]] += valores[0] / total
    perfil.mix = dict(mix)
    perfil.norma_mix = math.sqrt(sum(v * v for v in perfil.mix.values()))


def montar_perfis(db: Session, competencias: list[str]) -> dict[str, Perfil]:
    meses = sorted(competencias)
    dias = dias_no_periodo(meses)
    perfis: dict[str, Perfil] = {}

    for linha in db.execute(select(SihHospitalMonth).where(SihHospitalMonth.competencia.in_(meses))).scalars():
        p = perfis.setdefault(linha.cnes, Perfil(cnes=linha.cnes, dias_periodo=dias))
        p.uf = linha.uf
        p.aih_aprovadas += linha.aih_aprovadas
        p.aih_rejeitadas += linha.aih_rejeitadas
        p.valor_aprovado += float(linha.valor_aprovado or 0)
        p.valor_rejeitado += float(linha.valor_rejeitado or 0)
        p.diarias += linha.diarias
        p.permanencia_dias += linha.permanencia_dias
        mes = p.meses.setdefault(linha.competencia, {"aih_aprovadas": 0, "valor_aprovado": 0.0,
                                                     "aih_rejeitadas": 0, "valor_rejeitado": 0.0})
        mes["aih_aprovadas"] += linha.aih_aprovadas
        mes["valor_aprovado"] += float(linha.valor_aprovado or 0)
        mes["aih_rejeitadas"] += linha.aih_rejeitadas
        mes["valor_rejeitado"] += float(linha.valor_rejeitado or 0)

    procedimentos = db.execute(
        select(SihHospitalProcedureMonth.cnes, SihHospitalProcedureMonth.proc_realizado,
               SihHospitalProcedureMonth.complexidade, func.sum(SihHospitalProcedureMonth.aih),
               func.sum(SihHospitalProcedureMonth.valor), func.sum(SihHospitalProcedureMonth.permanencia_dias))
        .where(SihHospitalProcedureMonth.competencia.in_(meses))
        .group_by(SihHospitalProcedureMonth.cnes, SihHospitalProcedureMonth.proc_realizado,
                  SihHospitalProcedureMonth.complexidade)
    )
    for cnes, proc, complexidade, aih, valor, permanencia in procedimentos:
        p = perfis.get(cnes)
        if p is None or not proc:
            continue
        acumulado = p.procedimentos.setdefault(proc, [0, 0.0, 0])
        acumulado[0] += int(aih or 0)
        acumulado[1] += float(valor or 0)
        acumulado[2] += int(permanencia or 0)
        if complexidade == "03":
            p.aih_alta_complexidade += int(aih or 0)
    for p in perfis.values():
        atualizar_mix(p)

    for lote in _lotes(list(perfis)):
        for cnes, codigo in db.execute(
            select(Establishment.cnes, Establishment.natureza_juridica).where(Establishment.cnes.in_(lote))
        ):
            perfis[cnes].natureza = natureza(codigo)

    # Leitos e habilitações: a competência mais recente do CNES de cada hospital.
    ultimo_lt = dict(db.execute(select(CnesBed.cnes, func.max(CnesBed.competencia)).group_by(CnesBed.cnes)).all())
    for cnes, competencia, tipo, qt_sus in db.execute(
        select(CnesBed.cnes, CnesBed.competencia, CnesBed.tipo_leito, CnesBed.qt_sus)
    ):
        p = perfis.get(cnes)
        if p is None or competencia != ultimo_lt.get(cnes):
            continue
        p.leitos_sus += qt_sus
        if tipo == TIPO_LEITO_COMPLEMENTAR:
            p.leitos_uti_sus += qt_sus
        else:
            p.leitos_sus_gerais += qt_sus

    fim = meses[-1] if meses else ""
    ultimo_hb = dict(db.execute(
        select(CnesEnablement.cnes, func.max(CnesEnablement.competencia)).group_by(CnesEnablement.cnes)).all())
    for cnes, competencia, habilitacao, inicio, termino in db.execute(
        select(CnesEnablement.cnes, CnesEnablement.competencia, CnesEnablement.habilitacao,
               CnesEnablement.competencia_inicio, CnesEnablement.competencia_fim)
    ):
        p = perfis.get(cnes)
        vigente = (inicio or "000000") <= fim and (not termino or termino >= fim)
        if p is not None and competencia == ultimo_hb.get(cnes) and vigente:
            p.habilitacoes.add(habilitacao)

    _perdas(db, meses, perfis)
    return perfis


def _perdas(db: Session, meses: list[str], perfis: dict[str, Perfil]) -> None:
    """AIH rejeitadas não recuperadas, por categoria — a mesma regra do resumo."""
    ultima: dict[str, tuple[str, str, float]] = {}
    for n_aih, cnes, competencia, valor in db.execute(
        select(SihRejection.n_aih, SihRejection.cnes, SihRejection.competencia, SihRejection.valor)
        .where(SihRejection.competencia.in_(meses))
    ):
        if n_aih not in ultima or competencia >= ultima[n_aih][0]:
            ultima[n_aih] = (competencia, cnes, float(valor or 0))

    aprovadas: set[str] = set()
    for lote in _lotes(list(ultima)):
        aprovadas.update(db.execute(select(SihApprovedAih.n_aih).where(SihApprovedAih.n_aih.in_(lote))).scalars())

    motivos: dict[tuple[str, str], set[str]] = defaultdict(set)
    for n_aih, competencia, codigo in db.execute(
        select(SihRejectionReason.n_aih, SihRejectionReason.competencia, SihRejectionReason.codigo_erro)
        .where(SihRejectionReason.competencia.in_(meses))
    ):
        motivos[(n_aih, competencia)].add(codigo)

    for n_aih, (competencia, cnes, valor) in ultima.items():
        p = perfis.get(cnes)
        if p is None or n_aih in aprovadas:
            continue
        codigos = motivos.get((n_aih, competencia), set())
        perdas = p.perdas.setdefault(categorizar(codigos).codigo, Perdas())
        perdas.aih += 1
        perdas.valor += valor
        perdas.motivos.update(codigos)
