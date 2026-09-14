"""
RevenueOpportunityEngine: hospital contra hospitais semelhantes.

Cada oportunidade diz o valor observado, o valor dos semelhantes, a diferença,
o impacto estimado, a confiança, a evidência e a ação. O impacto é sempre a
parte ACIMA do padrão dos semelhantes, nunca o total: um hospital que perde 2%
em capacidade quando os parecidos perdem 1,5% tem 0,5% de oportunidade, não 2%.

Dois status na saída do motor:
- CONFIRMED: rejeição registrada pelo SUS no arquivo ER. A perda é fato; o
  impacto continua sendo a parte acima dos semelhantes.
- ESTIMATED_OPPORTUNITY: sinal calculado (valor médio, permanência, ocupação,
  produção). Precisa dos dados do hospital para virar caso.

Os limiares abaixo existem para não gerar oportunidade de centavos. Estão em
reais DO PERÍODO analisado (em geral três meses).
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.engine.categorias import CATEGORIAS
from app.engine.estatistica import percentil, quantil
from app.engine.perfil import Perfil

CONFIRMADA = "CONFIRMED"
ESTIMADA = "ESTIMATED_OPPORTUNITY"

ROTULOS_TIPO = {
    "AIH_REJECTION": "Rejeição de AIH acima dos semelhantes",
    "CNES_ENABLEMENT_RISK": "Rejeição por cadastro do CNES",
    "PROFESSIONAL_CBO_RISK": "Rejeição por profissional sem vínculo no CNES",
    "TICKET_GAP": "Valor médio por AIH abaixo dos semelhantes",
    "HIGH_LENGTH_OF_STAY": "Permanência acima dos semelhantes",
    "CAPACITY_UNDERUTILIZATION": "Leitos SUS pouco usados",
    "PRODUCTION_ANOMALY": "Queda de produção",
    "HIGH_COMPLEXITY_OPPORTUNITY": "Alta complexidade abaixo dos semelhantes",
}

MIN_PARES, MAX_PARES = 5, 15
# Procedimento feito menos que isto pelos semelhantes não serve de régua.
MIN_AIH_PROCEDIMENTO_PARES = 5
LIMIAR_REJEICAO = 5_000.0
LIMIAR_TICKET_PCT, LIMIAR_TICKET_VALOR = 0.05, 20_000.0
LIMIAR_PERMANENCIA_PCT, LIMIAR_PERMANENCIA_DIAS = 0.15, 100
LIMIAR_OCIOSIDADE, MIN_LEITOS_OCIOSIDADE = 0.6, 10
LIMIAR_QUEDA, MIN_AIH_MES_QUEDA = 0.75, 50
# Oportunidade ponderada pela confiança igual a 25% do apresentado vale score 100.
ESCALA_SCORE = 0.25

_Filtro = Callable[[Perfil, Perfil], bool]


def _faixa(a: Perfil, b: Perfil) -> bool:
    return b.faixa_complexidade == a.faixa_complexidade


# Do mais estrito ao mais largo. A faixa de alta complexidade entra primeiro:
# porte só por leitos juntava hospital regional com hospital distrital, e no
# Ceará o HRVJ (21% de alta complexidade, R$ 2.949 por AIH) saía comparado com
# hospitais sem alta complexidade e R$ 775 por AIH. O terceiro elemento diz se
# a etapa usa a faixa; sem faixa são as etapas do último recurso — e as do
# detector de alta complexidade, que precisa comparar faixas diferentes.
ETAPAS_PARES: tuple[tuple[str, _Filtro, bool], ...] = (
    ("mesma UF, porte, natureza jurídica e faixa de alta complexidade",
     lambda a, b: b.uf == a.uf and b.porte == a.porte and b.natureza == a.natureza and _faixa(a, b), True),
    ("mesma região, porte, natureza jurídica e faixa de alta complexidade",
     lambda a, b: b.regiao == a.regiao and b.porte == a.porte and b.natureza == a.natureza and _faixa(a, b), True),
    ("mesma região, porte e faixa de alta complexidade",
     lambda a, b: b.regiao == a.regiao and b.porte == a.porte and _faixa(a, b), True),
    ("Brasil, mesmo porte e faixa de alta complexidade", lambda a, b: b.porte == a.porte and _faixa(a, b), True),
    ("Brasil, mesma faixa de alta complexidade", _faixa, True),
    ("mesma UF, porte e natureza jurídica",
     lambda a, b: b.uf == a.uf and b.porte == a.porte and b.natureza == a.natureza, False),
    ("mesma região, porte e natureza jurídica",
     lambda a, b: b.regiao == a.regiao and b.porte == a.porte and b.natureza == a.natureza, False),
    ("Brasil, mesmo porte", lambda a, b: b.porte == a.porte, False),
    ("Brasil, qualquer porte", lambda a, b: True, False),
)

METRICAS: tuple[tuple[str, Callable[[Perfil], float | None]], ...] = (
    ("taxa_rejeicao_valor", lambda p: p.taxa_rejeicao_valor),
    ("perda_liquida_pct", lambda p: p.perda_liquida_pct),
    ("ticket_medio", lambda p: p.ticket_medio),
    ("permanencia_media", lambda p: p.permanencia_media),
    ("alta_complexidade", lambda p: p.alta_complexidade),
    ("ocupacao", lambda p: p.ocupacao),
    ("aih_mes", lambda p: p.aih_mes),
)


def distancia(a: Perfil, b: Perfil) -> float:
    """Quanto dois hospitais diferem: mix de procedimentos, volume e alta complexidade."""
    if a.norma_mix and b.norma_mix:
        produto = sum(v * b.mix.get(k, 0.0) for k, v in a.mix.items())
        mix = 1 - produto / (a.norma_mix * b.norma_mix)
    else:
        mix = 1.0
    volume = min(abs(math.log2(max(a.aih_mes, 1) / max(b.aih_mes, 1))), 3) / 3
    complexidade = abs((a.alta_complexidade or 0) - (b.alta_complexidade or 0))
    return round(mix + 0.5 * volume + complexidade, 4)


def _r(valor: float | None, casas: int = 4) -> float | None:
    return round(valor, casas) if valor is not None else None


@dataclass
class Analise:
    cnes: str
    pares: list[tuple[str, float]]
    criterio: dict[str, Any]
    indicadores: list[dict[str, Any]]
    oportunidades: list[dict[str, Any]]
    score: int
    impacto_estimado: float
    principal_tipo: str | None
    principal_categoria: str | None


class RevenueOpportunityEngine:
    def __init__(self, perfis: dict[str, Perfil], competencias: list[str]):
        self.perfis = perfis
        self.competencias = sorted(competencias)

    # ── Semelhantes ────────────────────────────────────────────────────────────

    def escolher_pares(self, alvo: Perfil, *, usar_faixa: bool = True) -> tuple[list[tuple[str, float]], dict[str, Any]]:
        """
        Do filtro mais estrito ao mais largo, o primeiro com hospitais bastantes;
        dentro dele, os mais parecidos no mix, volume e complexidade.
        """
        candidatos = [p for c, p in self.perfis.items() if c != alvo.cnes and p.aih_aprovadas > 0]
        etapas = [(d, f) for d, f, com_faixa in ETAPAS_PARES if usar_faixa or not com_faixa]
        escolhidos: list[Perfil] = []
        descricao = etapas[-1][0]
        for descricao, cabe in etapas:
            escolhidos = [p for p in candidatos if cabe(alvo, p)]
            if len(escolhidos) >= MIN_PARES:
                break
        pares = sorted(((p.cnes, distancia(alvo, p)) for p in escolhidos), key=lambda x: (x[1], x[0]))[:MAX_PARES]
        criterio = {
            "filtro": descricao,
            "n_pares": len(pares),
            "pares_insuficientes": len(pares) < MIN_PARES,
            "atributos": {
                "uf": alvo.uf, "regiao": alvo.regiao, "porte": alvo.porte, "natureza": alvo.natureza,
                "faixa_complexidade": alvo.faixa_complexidade,
                "leitos_sus": alvo.leitos_sus, "leitos_uti_sus": alvo.leitos_uti_sus,
                "habilitacoes": len(alvo.habilitacoes), "aih_mes": round(alvo.aih_mes, 1),
                "alta_complexidade": _r(alvo.alta_complexidade),
            },
        }
        return pares, criterio

    # ── Análise ────────────────────────────────────────────────────────────────

    def analisar(self, cnes: str) -> Analise:
        alvo = self.perfis[cnes]
        pares, criterio = self.escolher_pares(alvo)
        semelhantes = [self.perfis[c] for c, _ in pares]
        # Para alta complexidade a comparação é justamente entre faixas diferentes.
        amplos = [self.perfis[c] for c, _ in self.escolher_pares(alvo, usar_faixa=False)[0]]
        oportunidades = [
            *self._rejeicoes(alvo, semelhantes),
            *(o for o in (
                self._valor_medio(alvo, semelhantes),
                self._permanencia(alvo, semelhantes),
                self._ociosidade(alvo, semelhantes),
                self._queda_de_producao(alvo),
                self._alta_complexidade(alvo, amplos),
            ) if o),
        ]
        oportunidades.sort(key=lambda o: -(o["estimated_financial_impact"] or 0))
        score, impacto, principal = self._score(alvo, oportunidades)
        return Analise(
            cnes=cnes, pares=pares, criterio=criterio, indicadores=self.indicadores(alvo, semelhantes),
            oportunidades=oportunidades, score=score, impacto_estimado=round(impacto, 2),
            principal_tipo=principal["opportunity_type"] if principal else None,
            principal_categoria=(principal["categoria"] or None) if principal else None,
        )

    def indicadores(self, alvo: Perfil, pares: list[Perfil]) -> list[dict[str, Any]]:
        saida = []
        for nome, medir in METRICAS:
            valor = medir(alvo)
            valores = [v for v in (medir(p) for p in pares) if v is not None]
            saida.append({
                "metrica": nome, "valor": _r(valor), "mediana": _r(quantil(valores, 0.5)),
                "p25": _r(quantil(valores, 0.25)), "p75": _r(quantil(valores, 0.75)),
                "percentil": percentil(valor, valores), "n_pares": len(valores),
            })
        return saida

    # ── Detectores ─────────────────────────────────────────────────────────────

    def _base(self, tipo: str, status: str, **campos: Any) -> dict[str, Any]:
        return {"opportunity_type": tipo, "categoria": "", "status": status, "unidade": "R$", **campos}

    def _rejeicoes(self, alvo: Perfil, pares: list[Perfil]) -> list[dict[str, Any]]:
        if not alvo.valor_apresentado:
            return []
        saida = []
        for categoria in CATEGORIAS:
            perdas = alvo.perdas.get(categoria.codigo)
            if not perdas or perdas.valor <= 0:
                continue
            taxas = [
                (p.perdas[categoria.codigo].valor if categoria.codigo in p.perdas else 0.0) / p.valor_apresentado
                for p in pares if p.valor_apresentado
            ]
            taxa_pares = quantil(taxas, 0.5) or 0.0
            esperado = taxa_pares * alvo.valor_apresentado
            gap = perdas.valor - esperado
            if gap < LIMIAR_REJEICAO:
                continue
            amostra = 1.0 if perdas.aih >= 30 else 0.85 if perdas.aih >= 10 else 0.6
            confianca = round(categoria.confianca * amostra * (1.0 if len(pares) >= MIN_PARES else 0.7))
            saida.append(self._base(
                categoria.opportunity_type, CONFIRMADA, categoria=categoria.codigo,
                observed_value=round(perdas.valor, 2), benchmark_value=round(esperado, 2), gap=round(gap, 2),
                estimated_financial_impact=round(gap, 2), confidence_score=confianca,
                evidence={
                    "titulo": categoria.nome,
                    "aih_nao_recuperadas": perdas.aih,
                    "valor_nao_recuperado": round(perdas.valor, 2),
                    "taxa_hospital": round(perdas.valor / alvo.valor_apresentado, 4),
                    "taxa_mediana_semelhantes": round(taxa_pares, 4),
                    "n_semelhantes": len(pares),
                    "motivos": [{"codigo": c, "aih": n} for c, n in Counter(perdas.motivos).most_common(5)],
                    "competencias": self.competencias,
                    "leitura": (
                        "A rejeição está registrada pelo SUS e a AIH não voltou aprovada. O impacto é a parte que "
                        "passa do que hospitais semelhantes perdem na mesma categoria."
                    ),
                },
                recommended_action=categoria.acao,
            ))
        return saida

    def _estatisticas_por_procedimento(self, pares: list[Perfil]) -> dict[str, list[float]]:
        soma: dict[str, list[float]] = {}
        for p in pares:
            for proc, (aih, valor, permanencia) in p.procedimentos.items():
                acumulado = soma.setdefault(proc, [0, 0.0, 0])
                acumulado[0] += aih
                acumulado[1] += valor
                acumulado[2] += permanencia
        return soma

    def _valor_medio(self, alvo: Perfil, pares: list[Perfil]) -> dict[str, Any] | None:
        estatisticas = self._estatisticas_por_procedimento(pares)
        esperado = observado = 0.0
        aih_comparadas = 0
        contribuicoes = []
        for proc, (aih, valor, _) in alvo.procedimentos.items():
            s = estatisticas.get(proc)
            if not aih or not s or s[0] < MIN_AIH_PROCEDIMENTO_PARES:
                continue
            media_pares = s[1] / s[0]
            esperado += aih * media_pares
            observado += valor
            aih_comparadas += aih
            contribuicoes.append((aih * media_pares - valor, proc, aih, valor / aih, media_pares))
        gap = esperado - observado
        if not aih_comparadas or gap < LIMIAR_TICKET_VALOR or gap / esperado < LIMIAR_TICKET_PCT:
            return None
        contribuicoes.sort(reverse=True)
        return self._base(
            "TICKET_GAP", ESTIMADA, unidade="R$/AIH",
            observed_value=round(observado / aih_comparadas, 2), benchmark_value=round(esperado / aih_comparadas, 2),
            gap=round(gap / aih_comparadas, 2), estimated_financial_impact=round(gap, 2),
            confidence_score=40 if len(pares) >= MIN_PARES else 28,
            evidence={
                "titulo": ROTULOS_TIPO["TICKET_GAP"],
                "aih_comparadas": aih_comparadas,
                "cobertura": round(aih_comparadas / alvo.aih_aprovadas, 3) if alvo.aih_aprovadas else 0,
                "n_semelhantes": len(pares),
                "procedimentos": [
                    {"procedimento": proc, "aih": aih, "valor_medio_hospital": round(vh, 2),
                     "valor_medio_semelhantes": round(vp, 2)}
                    for g, proc, aih, vh, vp in contribuicoes[:5] if g > 0
                ],
                "leitura": (
                    "Nos mesmos procedimentos, o valor aprovado por AIH fica abaixo do dos semelhantes. Pode ser "
                    "procedimento secundário, OPM, diária de UTI ou acompanhante sem lançamento — ou pacientes com "
                    "perfil diferente."
                ),
            },
            recommended_action=(
                "Conferir nos procedimentos listados se secundários, OPM, diárias de UTI e acompanhante estão sendo "
                "lançados na AIH."
            ),
        )

    def _permanencia(self, alvo: Perfil, pares: list[Perfil]) -> dict[str, Any] | None:
        estatisticas = self._estatisticas_por_procedimento(pares)
        esperado = observado = 0.0
        aih_comparadas = 0
        for proc, (aih, _, permanencia) in alvo.procedimentos.items():
            s = estatisticas.get(proc)
            if not aih or not s or s[0] < MIN_AIH_PROCEDIMENTO_PARES:
                continue
            esperado += aih * s[2] / s[0]
            observado += permanencia
            aih_comparadas += aih
        excesso = observado - esperado
        if not aih_comparadas or esperado <= 0 or excesso < LIMIAR_PERMANENCIA_DIAS \
                or excesso / esperado < LIMIAR_PERMANENCIA_PCT:
            return None
        media = observado / aih_comparadas
        internacoes = excesso / media if media else 0.0
        return self._base(
            "HIGH_LENGTH_OF_STAY", ESTIMADA, unidade="dias",
            observed_value=round(media, 2), benchmark_value=round(esperado / aih_comparadas, 2),
            gap=round(excesso / aih_comparadas, 2),
            estimated_financial_impact=round(internacoes * (alvo.ticket_medio or 0), 2),
            confidence_score=35 if len(pares) >= MIN_PARES else 25,
            evidence={
                "titulo": ROTULOS_TIPO["HIGH_LENGTH_OF_STAY"],
                "dias_excedentes": round(excesso),
                "internacoes_que_caberiam": round(internacoes),
                "aih_comparadas": aih_comparadas,
                "n_semelhantes": len(pares),
                "leitura": (
                    f"Com a permanência dos semelhantes nos mesmos procedimentos, os leitos atenderiam cerca de "
                    f"{round(internacoes)} internações a mais no período."
                ),
            },
            recommended_action=(
                "Revisar a gestão de leitos e de alta nos procedimentos de maior permanência: espera por exame, "
                "cirurgia e alta."
            ),
        )

    def _ociosidade(self, alvo: Perfil, pares: list[Perfil]) -> dict[str, Any] | None:
        if alvo.leitos_sus_gerais < MIN_LEITOS_OCIOSIDADE or alvo.ocupacao is None:
            return None
        ocupacoes = [p.ocupacao for p in pares if p.ocupacao is not None]
        mediana = quantil(ocupacoes, 0.5) if len(ocupacoes) >= 3 else None
        if not mediana or alvo.ocupacao >= LIMIAR_OCIOSIDADE * mediana:
            return None
        diarias_a_mais = (mediana - alvo.ocupacao) * alvo.leitos_sus_gerais * alvo.dias_periodo
        permanencia = alvo.permanencia_media or quantil([p.permanencia_media for p in pares], 0.5)
        internacoes = diarias_a_mais / permanencia if permanencia else 0.0
        return self._base(
            "CAPACITY_UNDERUTILIZATION", ESTIMADA, unidade="%",
            observed_value=_r(alvo.ocupacao), benchmark_value=_r(mediana), gap=_r(mediana - alvo.ocupacao),
            estimated_financial_impact=round(internacoes * (alvo.ticket_medio or 0), 2), confidence_score=30,
            evidence={
                "titulo": ROTULOS_TIPO["CAPACITY_UNDERUTILIZATION"],
                "leitos_sus_gerais": alvo.leitos_sus_gerais,
                "diarias_aprovadas": alvo.diarias,
                "internacoes_possiveis": round(internacoes),
                "n_semelhantes": len(pares),
                "leitura": (
                    "Leitos SUS cadastrados no CNES com uso bem abaixo dos semelhantes. Pode ser leito bloqueado ou "
                    "desativado que continua no cadastro, ou demanda não captada."
                ),
            },
            recommended_action=(
                "Conferir no CNES os leitos SUS sem uso e a regulação de internações; ajustar o cadastro ou "
                "ampliar a produção."
            ),
        )

    def _queda_de_producao(self, alvo: Perfil) -> dict[str, Any] | None:
        if len(self.competencias) < 3 or any(c not in alvo.meses for c in self.competencias):
            return None
        serie = {c: alvo.meses[c]["aih_aprovadas"] for c in self.competencias}
        anteriores = [serie[c] for c in self.competencias[:-1]]
        media = sum(anteriores) / len(anteriores)
        ultimo = serie[self.competencias[-1]]
        if media < MIN_AIH_MES_QUEDA or ultimo >= LIMIAR_QUEDA * media:
            return None
        return self._base(
            "PRODUCTION_ANOMALY", ESTIMADA, unidade="AIH",
            observed_value=float(ultimo), benchmark_value=round(media, 1), gap=round(media - ultimo, 1),
            estimated_financial_impact=round((media - ultimo) * (alvo.ticket_medio or 0), 2), confidence_score=30,
            evidence={
                "titulo": ROTULOS_TIPO["PRODUCTION_ANOMALY"],
                "serie_aih_aprovadas": serie,
                "leitura": (
                    "A produção aprovada do último mês ficou bem abaixo da média dos anteriores. Pode ser AIH ainda "
                    "não apresentada, bloqueada, ou queda real de atendimento."
                ),
            },
            recommended_action="Conferir AIH do mês ainda não apresentadas ou bloqueadas e o motivo da queda.",
        )

    def _alta_complexidade(self, alvo: Perfil, pares: list[Perfil]) -> dict[str, Any] | None:
        if not alvo.habilitacoes or alvo.alta_complexidade is None:
            return None
        participacoes = [p.alta_complexidade for p in pares if p.alta_complexidade is not None]
        mediana = quantil(participacoes, 0.5) if len(participacoes) >= 3 else None
        if not mediana or mediana < 0.05 or alvo.alta_complexidade >= 0.5 * mediana:
            return None
        return self._base(
            "HIGH_COMPLEXITY_OPPORTUNITY", ESTIMADA, unidade="%",
            observed_value=_r(alvo.alta_complexidade), benchmark_value=_r(mediana),
            gap=_r(mediana - alvo.alta_complexidade), estimated_financial_impact=None, confidence_score=25,
            evidence={
                "titulo": ROTULOS_TIPO["HIGH_COMPLEXITY_OPPORTUNITY"],
                "habilitacoes_vigentes": len(alvo.habilitacoes),
                "n_semelhantes": len(pares),
                "leitura": (
                    "O hospital tem habilitações no CNES e faz bem menos alta complexidade que os semelhantes. É um "
                    "sinal para investigar, sem valor estimado."
                ),
            },
            recommended_action="Conferir se a alta complexidade habilitada está sendo produzida e faturada como tal.",
        )

    # ── Score ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _ponderado(oportunidade: dict[str, Any]) -> float:
        return float(oportunidade["estimated_financial_impact"] or 0) * oportunidade["confidence_score"] / 100

    def _score(self, alvo: Perfil, oportunidades: list[dict[str, Any]]) -> tuple[int, float, dict[str, Any] | None]:
        """
        Revenue Opportunity Score, 0 a 100: oportunidade ponderada pela confiança
        sobre o valor apresentado, com 25% do apresentado valendo 100.
        """
        impacto = sum(float(o["estimated_financial_impact"] or 0) for o in oportunidades)
        ponderado = sum(self._ponderado(o) for o in oportunidades)
        score = min(100, round(100 * ponderado / (ESCALA_SCORE * alvo.valor_apresentado))) \
            if alvo.valor_apresentado else 0
        principal = max(oportunidades, key=self._ponderado, default=None)
        if principal is not None and self._ponderado(principal) <= 0:
            principal = None
        return score, impacto, principal
