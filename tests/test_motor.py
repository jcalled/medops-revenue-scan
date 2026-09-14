"""
Motor de oportunidades e escolha de semelhantes.

O que estes testes travam:
- a AIH com vários motivos cai na categoria mais acionável, e código novo em OUTROS;
- semelhantes começam na mesma UF, porte e natureza, e só alargam quando faltam;
- o hospital nunca é semelhante de si mesmo, e os mais parecidos vêm primeiro;
- oportunidade de rejeição é só a parte acima dos semelhantes;
- valor médio e permanência são comparados no mesmo procedimento;
- ociosidade exige leitos, queda exige série, alta complexidade não inventa valor;
- o score fica entre 0 e 100.
"""
import pytest

from app.engine.categorias import categorizar
from app.engine.estatistica import percentil, quantil
from app.engine.motor import RevenueOpportunityEngine, distancia
from app.engine.perfil import Perdas, Perfil, atualizar_mix, natureza, porte, regiao

MESES = ["202605", "202606", "202607"]
PROC = "0303010010"


def perfil(cnes, uf="CE", nat="pública", leitos=100, aih=900, valor=1_800_000.0, permanencia=4_500,
           procedimentos=None, perdas=None, meses=None, alta=90, diarias=None, habilitacoes=(), rejeitado=0.0):
    p = Perfil(cnes=cnes, uf=uf, natureza=nat, leitos_sus=leitos, leitos_sus_gerais=leitos, dias_periodo=92)
    p.aih_aprovadas, p.valor_aprovado, p.valor_rejeitado = aih, valor, rejeitado
    p.permanencia_dias, p.diarias, p.aih_alta_complexidade = permanencia, permanencia if diarias is None else diarias, alta
    p.procedimentos = procedimentos or {PROC: [aih, valor, permanencia]}
    p.perdas = perdas or {}
    p.meses = meses or {c: {"aih_aprovadas": aih / 3, "valor_aprovado": valor / 3, "aih_rejeitadas": 0,
                            "valor_rejeitado": 0.0} for c in MESES}
    p.habilitacoes = set(habilitacoes)
    atualizar_mix(p)
    return p


def motor(*perfis):
    return RevenueOpportunityEngine({p.cnes: p for p in perfis}, MESES)


def test_categoria_mais_acionavel():
    assert categorizar({"010003", "060082"}).codigo == "CAPACIDADE"
    assert categorizar({"060120", "060082"}).codigo == "HABILITACAO_SERVICO"
    assert categorizar({"999999"}).codigo == "OUTROS"
    assert categorizar(set()).codigo == "OUTROS"
    assert categorizar({"010003"}).codigo == "ADMINISTRATIVO"


def test_quantil_e_percentil():
    assert quantil([1, 2, 3, 4], 0.5) == 2.5 and quantil([], 0.5) is None and quantil([7], 0.25) == 7
    assert percentil(3, [1, 2, 3, 4]) == 62.5 and percentil(None, [1]) is None


def test_atributos_de_classificacao():
    assert (porte(0), porte(50), porte(51), porte(301)) == (
        "sem leitos no CNES", "até 50 leitos", "51 a 150 leitos", "mais de 300 leitos")
    assert (natureza("1023"), natureza("3069"), natureza(None)) == ("pública", "sem fins lucrativos", "não informada")
    assert (regiao("CE"), regiao("SP"), regiao("DF")) == ("Nordeste", "Sudeste", "Centro-Oeste")


def test_semelhantes_na_mesma_uf_porte_e_natureza():
    alvo = perfil("0000001")
    mesmos = [perfil(f"10000{i:02d}") for i in range(6)]
    empresariais = [perfil(f"20000{i:02d}", nat="empresarial") for i in range(3)]
    pernambuco = [perfil(f"30000{i:02d}", uf="PE") for i in range(5)]
    pares, criterio = motor(alvo, *mesmos, *empresariais, *pernambuco).escolher_pares(alvo)
    assert {c for c, _ in pares} == {p.cnes for p in mesmos}
    assert criterio["filtro"].startswith("mesma UF") and not criterio["pares_insuficientes"]


def test_alarga_para_a_regiao_quando_faltam():
    alvo = perfil("0000001")
    ceara = [perfil(f"10000{i:02d}") for i in range(2)]
    nordeste = [perfil(f"20000{i:02d}", uf=uf) for i, uf in enumerate(["PE", "PB", "RN", "BA"])]
    sul = [perfil(f"30000{i:02d}", uf="RS") for i in range(10)]
    pares, criterio = motor(alvo, *ceara, *nordeste, *sul).escolher_pares(alvo)
    assert len(pares) == 6 and not any(c.startswith("3") for c, _ in pares)
    assert criterio["filtro"].startswith("mesma região")


def test_faixa_de_alta_complexidade_separa_regional_de_distrital():
    regional = perfil("0000001", alta=190)                                   # 21%
    distritais = [perfil(f"10000{i:02d}", alta=0) for i in range(8)]         # sem alta complexidade
    regionais = [perfil(f"20000{i:02d}", alta=150, uf="PE") for i in range(5)]  # 17%, outra UF
    pares, criterio = motor(regional, *distritais, *regionais).escolher_pares(regional)
    assert {c for c, _ in pares} == {p.cnes for p in regionais}
    assert criterio["filtro"].startswith("mesma região") and "faixa" in criterio["filtro"]
    assert criterio["atributos"]["faixa_complexidade"] == "alta complexidade de 10% a 25%"


def test_mais_parecidos_primeiro_e_no_maximo_quinze():
    alvo = perfil("0000001", procedimentos={PROC: [900, 1_800_000.0, 4_500]})
    parecidos = [perfil(f"10000{i:02d}") for i in range(10)]
    diferentes = [perfil(f"20000{i:02d}", procedimentos={"0411010034": [900, 1_800_000.0, 4_500]}) for i in range(10)]
    pares, _ = motor(alvo, *parecidos, *diferentes).escolher_pares(alvo)
    assert len(pares) == 15 and "0000001" not in {c for c, _ in pares}
    assert {c for c, _ in pares[:10]} == {p.cnes for p in parecidos}
    assert distancia(alvo, parecidos[0]) < distancia(alvo, diferentes[0])


def _perdas(valor, aih=40, codigo="060082"):
    return {"CAPACIDADE": Perdas(aih=aih, valor=valor, motivos={codigo: aih})}


def test_rejeicao_so_a_parte_acima_dos_semelhantes():
    alvo = perfil("0000001", perdas=_perdas(300_000.0), rejeitado=300_000.0, valor=1_700_000.0)
    pares = [perfil(f"10000{i:02d}", perdas=_perdas(20_000.0), rejeitado=20_000.0, valor=1_980_000.0)
             for i in range(6)]
    [oportunidade] = [o for o in motor(alvo, *pares).analisar("0000001").oportunidades
                      if o["categoria"] == "CAPACIDADE"]
    assert oportunidade["status"] == "CONFIRMED" and oportunidade["opportunity_type"] == "AIH_REJECTION"
    assert oportunidade["observed_value"] == 300_000.0 and oportunidade["benchmark_value"] == 20_000.0
    assert oportunidade["estimated_financial_impact"] == 280_000.0 and oportunidade["confidence_score"] == 85
    assert oportunidade["evidence"]["motivos"] == [{"codigo": "060082", "aih": 40}]

    igual = perfil("0000002", perdas=_perdas(20_000.0), rejeitado=20_000.0, valor=1_980_000.0)
    assert not [o for o in motor(igual, *pares).analisar("0000002").oportunidades if o["categoria"]]


def test_valor_medio_abaixo_no_mesmo_procedimento():
    alvo = perfil("0000001", aih=1000, valor=1_000_000.0)
    pares = [perfil(f"10000{i:02d}", aih=1000, valor=1_300_000.0) for i in range(6)]
    [oportunidade] = [o for o in motor(alvo, *pares).analisar("0000001").oportunidades
                      if o["opportunity_type"] == "TICKET_GAP"]
    assert oportunidade["observed_value"] == 1000.0 and oportunidade["benchmark_value"] == 1300.0
    assert oportunidade["estimated_financial_impact"] == 300_000.0
    assert oportunidade["evidence"]["procedimentos"][0]["valor_medio_semelhantes"] == 1300.0

    procedimento_diferente = perfil("0000002", aih=1000, valor=1_000_000.0,
                                    procedimentos={"0411010034": [1000, 1_000_000.0, 4_500]})
    assert not [o for o in motor(procedimento_diferente, *pares).analisar("0000002").oportunidades
                if o["opportunity_type"] == "TICKET_GAP"]


def test_permanencia_acima_no_mesmo_procedimento():
    alvo = perfil("0000001", aih=900, permanencia=7_200, valor=1_800_000.0)   # 8 dias
    pares = [perfil(f"10000{i:02d}", aih=900, permanencia=4_500) for i in range(6)]  # 5 dias
    [oportunidade] = [o for o in motor(alvo, *pares).analisar("0000001").oportunidades
                      if o["opportunity_type"] == "HIGH_LENGTH_OF_STAY"]
    assert (oportunidade["observed_value"], oportunidade["benchmark_value"]) == (8.0, 5.0)
    assert oportunidade["evidence"]["dias_excedentes"] == 2700
    assert oportunidade["evidence"]["internacoes_que_caberiam"] == 338           # 2700 / 8
    assert oportunidade["estimated_financial_impact"] == pytest.approx(337.5 * 2000.0)


def test_ociosidade_exige_leitos():
    pares = [perfil(f"10000{i:02d}", leitos=100, diarias=7_360) for i in range(6)]    # 80%
    ocioso = perfil("0000001", leitos=100, diarias=1_840)                             # 20%
    [oportunidade] = [o for o in motor(ocioso, *pares).analisar("0000001").oportunidades
                      if o["opportunity_type"] == "CAPACITY_UNDERUTILIZATION"]
    assert oportunidade["observed_value"] == 0.2 and oportunidade["benchmark_value"] == 0.8
    pequeno = perfil("0000002", leitos=8, diarias=100)
    assert not [o for o in motor(pequeno, *pares).analisar("0000002").oportunidades
                if o["opportunity_type"] == "CAPACITY_UNDERUTILIZATION"]


def test_queda_de_producao():
    def meses(*aih):
        return {c: {"aih_aprovadas": a, "valor_aprovado": a * 2000.0, "aih_rejeitadas": 0, "valor_rejeitado": 0.0}
                for c, a in zip(MESES, aih)}
    caiu = perfil("0000001", meses=meses(400, 400, 200))
    [oportunidade] = [o for o in motor(caiu).analisar("0000001").oportunidades
                      if o["opportunity_type"] == "PRODUCTION_ANOMALY"]
    assert oportunidade["gap"] == 200 and oportunidade["estimated_financial_impact"] == 400_000.0
    estavel = perfil("0000002", meses=meses(400, 390, 380))
    assert not [o for o in motor(estavel).analisar("0000002").oportunidades
                if o["opportunity_type"] == "PRODUCTION_ANOMALY"]


def test_alta_complexidade_e_sinal_sem_valor():
    alvo = perfil("0000001", alta=10, habilitacoes={"0802"})
    pares = [perfil(f"10000{i:02d}", alta=270) for i in range(6)]
    [sinal] = [o for o in motor(alvo, *pares).analisar("0000001").oportunidades
               if o["opportunity_type"] == "HIGH_COMPLEXITY_OPPORTUNITY"]
    assert sinal["estimated_financial_impact"] is None and sinal["status"] == "ESTIMATED_OPPORTUNITY"


def test_sinal_nao_passa_de_metade_do_apresentado_e_pesa_menos():
    """Um filantrópico do Ceará saía com R$ 12,8 mi de "leitos pouco usados", mais que a produção."""
    pares = [perfil(f"10000{i:02d}", leitos=300, diarias=22_080) for i in range(6)]      # 80%
    ocioso = perfil("0000001", leitos=300, diarias=1_000)                                # ~4%, mesmo valor médio
    analise = motor(ocioso, *pares).analisar("0000001")
    assert [o["opportunity_type"] for o in analise.oportunidades] == ["CAPACITY_UNDERUTILIZATION"]
    [sinal] = analise.oportunidades
    assert sinal["estimated_financial_impact"] == 900_000.0                               # metade de R$ 1,8 mi
    assert sinal["evidence"]["impacto_limitado"] is True and sinal["evidence"]["impacto_calculado"] > 900_000.0
    # 900 mil × confiança 30% × peso 0,5 = 135 mil, sobre 25% de 1,8 mi = 30.
    assert analise.score == 30


def test_score_entre_zero_e_cem():
    alvo = perfil("0000001", perdas=_perdas(3_000_000.0), rejeitado=3_000_000.0, valor=500_000.0)
    pares = [perfil(f"10000{i:02d}") for i in range(6)]
    analise = motor(alvo, *pares).analisar("0000001")
    assert analise.score == 100 and analise.principal_categoria == "CAPACIDADE"
    tranquilo = motor(perfil("0000002"), *pares).analisar("0000002")
    assert tranquilo.score == 0 and tranquilo.principal_tipo is None
    sem_producao = perfil("0000003", aih=0, valor=0.0, permanencia=0, procedimentos={PROC: [0, 0.0, 0]})
    assert motor(sem_producao, *pares).analisar("0000003").score == 0
