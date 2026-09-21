"""
Kit de recuperação de qualquer recorte.

O que estes testes travam:
- cada AIH cai numa classe só: já recebida, não reapresentável, prazo vencido, gestor, investigar ou recuperável;
- regras do MS: capacidade é cancelada e não volta; 040008 é definitiva; a reapresentação vai até o 6º mês
  contado do mês da alta (alta + 5), a partir do mês de referência;
- o que não volta sai em "perdas", com a regra e a fonte;
- recuperável sem data de alta vai para investigar, não para a lista como se tivesse prazo;
- por hospital, por mês de vencimento e a lista de trabalho na ordem prazo → chance → valor;
- "recupera sozinho" só vira taxa com amostra, e dá o piso sem ação;
- o recorte respeita o escopo do contrato.
"""
from datetime import date

import httpx
import pytest
from sqlalchemy import update

from app.domain.kit import classificar, referencia_padrao
from app.jobs.recalcular import recalcular
from app.models import SihRejection, SihRejectionReason
from tests.conftest import contrato, token
from tests.test_scan_api import HRC, HRVJ, popular


def _rejeitar(db, cnes, competencia, n_aih, valor, codigo, saida):
    db.add(SihRejection(uf="CE", competencia=competencia, cnes=cnes, n_aih=n_aih, valor=valor, dt_saida=saida))
    db.add(SihRejectionReason(uf="CE", competencia=competencia, cnes=cnes, n_aih=n_aih, codigo_erro=codigo))


def _dados(fabrica_sessao):
    with fabrica_sessao() as db:
        popular(db)  # 30 AIH do HRVJ por capacidade em mai/26 (R$ 15 mil cada); a ...000 voltou em jun/26
        db.execute(update(SihRejection).values(dt_saida=date(2026, 6, 10)))  # capacidade: não reapresentável
        _rejeitar(db, HRVJ, "202606", "P1", 1000.0, "060109", date(2026, 6, 1))    # profissional: alta, vence nov
        _rejeitar(db, HRVJ, "202606", "H1", 2000.0, "060120", date(2026, 5, 20))   # habilitação: média, vence out
        _rejeitar(db, HRVJ, "202606", "V1", 3000.0, "060082", date(2026, 1, 10))   # capacidade: não reapresentável
        _rejeitar(db, HRVJ, "202606", "G1", 4000.0, "010003", date(2026, 6, 1))    # gestor
        _rejeitar(db, HRVJ, "202606", "O1", 500.0, "999999", date(2026, 6, 1))     # sem regra
        _rejeitar(db, HRVJ, "202606", "Z1", 600.0, "040008", date(2026, 1, 1))     # prazo: definitiva
        _rejeitar(db, HRVJ, "202606", "S1", 800.0, "060109", None)                 # recuperável sem data
        _rejeitar(db, HRC, "202607", "B1", 700.0, "060109", date(2026, 7, 1))      # outro hospital, vence dez
        db.commit()
        recalcular(db, ufs=["CE"])


def _get(http, caminho, cabecalho=None):
    resposta = http.get(caminho, headers=cabecalho or {"Authorization": f"Bearer {token()}"})
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def test_classes_por_hospital_vencimento_e_lista(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    kit = _get(http, "/api/revenue-scan/kit?uf=CE&referencia=202610")

    classes = {c: (v["aih"], v["valor"]) for c, v in kit["classes"].items()}
    assert classes == {
        "ALTA": (2, 1700.0), "MEDIA": (1, 2000.0), "INCERTA": (0, 0.0), "INVESTIGAR": (2, 1300.0),
        "GESTOR": (1, 4000.0), "NAO_REAPRESENTAVEL": (30, 438000.0), "PRAZO_VENCIDO": (1, 600.0),
        "JA_RECEBIDA": (1, 15000.0),
    }
    assert kit["rejeitadas"] == {"aih": 38, "valor": 462600.0}
    assert kit["recuperavel_no_prazo"]["aih"] == 3 and kit["recuperavel_no_prazo"]["valor"] == 3700.0
    assert kit["vence_neste_mes"] == {"aih": 1, "valor": 2000.0}

    # O que não volta, com a regra e a fonte.
    assert [(p["codigo"], p["aih"], p["valor"]) for p in kit["perdas"]] == [
        ("CAPACIDADE", 30, 438000.0), ("PRAZO_APRESENTACAO", 1, 600.0)]
    assert "item 59.1" in kit["perdas"][0]["fonte"] and "cancelada" in kit["perdas"][0]["regra"]

    assert [h["cnes"] for h in kit["hospitais"]] == [HRVJ, HRC]
    assert kit["hospitais"][0]["recuperavel"] == {"aih": 2, "valor": 3000.0, "pct_valor": round(3000 / 461900, 4)}
    assert [(v["competencia"], v["total"]["aih"]) for v in kit["vencimento"]] == [("202610", 1), ("202611", 1), ("202612", 1)]

    # Vence em out (média), nov, dez; depois investigar e gestor por valor.
    ordem = [i["n_aih"] for i in kit["itens"]]
    assert ordem == ["H1", "P1", "B1", "S1", "O1", "G1"]
    assert kit["itens_total"] == 6 and "V1" not in ordem and "2326000000001" not in ordem
    primeiro = kit["itens"][0]
    assert primeiro["classe"] == "MEDIA" and primeiro["meses_para_vencer"] == 0 and "habilitação" in primeiro["onde"]

    # Sozinho: das 31 rejeitadas por capacidade em mai–jun (as 30 de maio e a V1), 1 voltou.
    # Os outros tipos não têm amostra para taxa.
    assert kit["recupera_sozinho"]["CAPACIDADE"] == {"nome": "Diárias acima da capacidade instalada",
                                                      "rejeitadas": 31, "voltaram": 1, "taxa": 0.0323}
    assert kit["recupera_sozinho"]["PROFISSIONAL"] == {"nome": "Profissional sem vínculo ou CBO no CNES",
                                                        "rejeitadas": 2, "voltaram": 0, "taxa": None}
    # Sem amostra nos tipos recuperáveis, não há piso a descontar; capacidade não entra no recuperável.
    assert kit["piso_sem_acao"] == pytest.approx(0.0)

    todas = _get(http, "/api/revenue-scan/kit?uf=CE&referencia=202610&lista=todas")
    assert todas["itens_total"] == 38
    v1 = next(i for i in todas["itens"] if i["n_aih"] == "V1")
    assert (v1["classe"], v1["perda"]) == ("NAO_REAPRESENTAVEL", "CAPACIDADE")


def test_referencia_muda_o_que_venceu(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    kit = _get(http, "/api/revenue-scan/kit?uf=CE&referencia=202612")
    # Em dezembro, só a do HRC ainda cabe; as de outubro e novembro venceram.
    assert kit["recuperavel_no_prazo"] == {"aih": 1, "valor": 700.0, "pct_valor": round(700 / 462600, 4)}
    assert kit["classes"]["PRAZO_VENCIDO"]["aih"] == 3
    janela = next(p for p in kit["perdas"] if p["codigo"] == "JANELA_REAPRESENTACAO")
    assert (janela["aih"], janela["valor"]) == (2, 3000.0)


def test_escopo_do_contrato(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato({"cnes": [HRC]})))
    kit = _get(http, "/api/revenue-scan/kit?referencia=202610")
    assert [h["cnes"] for h in kit["hospitais"]] == [HRC] and kit["rejeitadas"]["aih"] == 1


def test_classificar_e_referencia_padrao():
    base = {"situacao": "RECUPERAR", "categoria": "PROFISSIONAL", "dt_saida": "2026-06-01"}
    assert classificar(base, "202611") == ("ALTA", "202611")
    assert classificar(base, "202612") == ("PRAZO_VENCIDO", "202611")
    assert classificar({**base, "situacao": "JA_RECEBIDA"}, "202601") == ("JA_RECEBIDA", None)
    assert classificar({**base, "dt_saida": None}, "202610") == ("INVESTIGAR", None)
    assert classificar({**base, "categoria": "ADMINISTRATIVO"}, "202610") == ("GESTOR", "202611")
    # Capacidade é cancelada mesmo dentro do prazo e mesmo junto de outro motivo.
    assert classificar({**base, "categoria": "CAPACIDADE"}, "202607") == ("NAO_REAPRESENTAVEL", "202611")
    com_capacidade = {**base, "motivos": [{"codigo": "060109"}, {"codigo": "060084"}]}
    assert classificar(com_capacidade, "202607") == ("NAO_REAPRESENTAVEL", "202611")
    assert referencia_padrao(date(2026, 9, 15)) == "202609"
