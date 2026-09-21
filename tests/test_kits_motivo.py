"""
Kits por motivo e situação por AIH.

O que estes testes travam:
- os kits iniciais entram uma vez, são válidos, e carregar de novo não apaga o que foi editado;
- kit a confirmar não muda a classificação; confirmado muda, e com mais de um motivo vale o mais difícil;
- o catálogo lista os motivos sem kit por valor e a cobertura dos kits;
- só a administração escreve kit;
- a situação da AIH pede justificativa para "sem como" e mês para "reapresentada", guarda o histórico,
  respeita o escopo e aparece na lista do kit de recuperação.
"""
from datetime import date

import httpx

from app.domain.kits_motivo import DIFICULDADE, ONDE
from app.models import MotiveKit, SihRejectionReason
from app.seed.kits_motivo import KITS, aplicar
from tests.conftest import contrato, token
from tests.test_dados import ADMIN, _admin
from tests.test_kit import _dados, _get, _rejeitar
from tests.test_scan_api import HRC, HRVJ

TENANT = {"Authorization": f"Bearer {token()}"}


def _kit(codigo, classe, revisao="A_CONFIRMAR"):
    return MotiveKit(codigo=codigo, titulo=f"Kit {codigo}", significado="teste", classe=classe, onde_corrigir="SESA",
                     passos=["conferir"], dados_do_hospital=["SISAIH01"], evidencias=[], revisao=revisao)


def test_kits_iniciais_entram_uma_vez(fabrica_sessao):
    assert len(KITS) >= 30 and len({k["codigo"] for k in KITS}) == len(KITS)
    assert all(k["classe"] in DIFICULDADE and k["onde_corrigir"] in ONDE and k["passos"] for k in KITS)
    with fabrica_sessao() as db:
        assert aplicar(db) == len(KITS)
        db.get(MotiveKit, "060082").titulo = "Editado"
        db.commit()
        assert aplicar(db) == 0
        assert db.get(MotiveKit, "060082").titulo == "Editado"
        assert {k.revisao for k in db.query(MotiveKit)} == {"A_CONFIRMAR"}


def test_kit_confirmado_muda_a_classificacao(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    with fabrica_sessao() as db:
        _rejeitar(db, HRVJ, "202606", "M1", 900.0, "060109", date(2026, 6, 1))
        db.add(SihRejectionReason(uf="CE", competencia="202606", cnes=HRVJ, n_aih="M1", codigo_erro="999999"))
        db.add(_kit("010003", "MEDIA"))
        db.commit()
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    url = "/api/revenue-scan/kit?uf=CE&referencia=202609&lista=todas"

    antes = {i["n_aih"]: i for i in _get(http, url)["itens"]}
    assert antes["G1"]["classe"] == "GESTOR" and not antes["G1"]["classe_pelo_kit"]
    assert antes["M1"]["classe"] == "INVESTIGAR"

    with fabrica_sessao() as db:
        db.get(MotiveKit, "010003").revisao = "CONFIRMADO"
        db.add(_kit("060109", "ALTA", "CONFIRMADO"))
        db.commit()
    kit = _get(http, url)
    depois = {i["n_aih"]: i for i in kit["itens"]}
    assert depois["G1"]["classe"] == "MEDIA" and depois["G1"]["classe_pelo_kit"]
    assert depois["G1"]["meses_para_vencer"] == 2  # alta em junho: reapresenta até novembro
    # 060109 confirmado junto de um motivo sem kit (vai para investigar): vale o mais difícil.
    assert depois["M1"]["classe"] == "INVESTIGAR"
    assert depois["P1"]["classe"] == "ALTA"
    assert set(kit["kits_motivo"]) == {"010003", "060109"}
    assert kit["kits_motivo"]["010003"]["passos"] == ["conferir"]


def test_catalogo_sem_kit_e_cobertura(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    with fabrica_sessao() as db:
        db.add(_kit("010003", "MEDIA"))
        db.commit()
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))

    cat = _get(http, "/api/revenue-scan/motive-kits?uf=CE")
    assert [s["codigo"] for s in cat["sem_kit"]] == ["060082", "060109", "060120", "040008", "999999"]
    assert cat["sem_kit"][0] == {"codigo": "060082", "descricao": None, "rejeicoes": 31, "valor": 453000.0,
                                 "faturasus": None}
    assert cat["sem_kit_total"] == 5 and cat["valor_rejeitado"] == 462600.0
    assert cat["kits"][0]["codigo"] == "010003" and (cat["kits"][0]["rejeicoes"], cat["kits"][0]["valor"]) == (1, 4000.0)
    assert cat["cobertura_valor"] == round(4000 / 462600, 4)
    assert _get(http, "/api/revenue-scan/motive-kits?uf=SP")["sem_kit"] == []


def test_so_administracao_escreve_kit(app_com_nucleo, fabrica_sessao):
    corpo = {"titulo": "Número da AIH fora da faixa", "significado": "Fora das faixas autorizadas.",
             "classe": "MEDIA", "onde_corrigir": "SESA", "passos": ["Conferir a faixa", "  "], "revisao": "CONFIRMADO"}
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    assert http.put("/api/revenue-scan/motive-kits/010003", json=corpo, headers=TENANT).status_code == 403

    admin = _admin(app_com_nucleo)
    assert admin.put("/api/revenue-scan/motive-kits/010003", json={**corpo, "classe": "QUALQUER"},
                     headers=ADMIN).status_code == 422
    assert admin.put("/api/revenue-scan/motive-kits/ABC", json=corpo, headers=ADMIN).status_code == 422
    salvo = admin.put("/api/revenue-scan/motive-kits/010003", json=corpo, headers=ADMIN)
    assert salvo.status_code == 200 and salvo.json()["revisao"] == "CONFIRMADO"
    assert admin.get("/api/revenue-scan/motive-kits/010003", headers=ADMIN).json()["passos"] == ["Conferir a faixa"]
    assert admin.get("/api/revenue-scan/motive-kits/060082", headers=ADMIN).status_code == 404


def test_situacao_da_aih(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    url = "/api/revenue-scan/aih/P1/treatment"

    assert http.put(url, json={"situacao": "SEM_COMO"}, headers=TENANT).status_code == 422
    assert http.put(url, json={"situacao": "REAPRESENTADA"}, headers=TENANT).status_code == 422
    assert http.put(url, json={"situacao": "INVENTADA"}, headers=TENANT).status_code == 422
    assert http.put("/api/revenue-scan/aih/NAOEXISTE/treatment", json={"situacao": "A_FAZER"},
                    headers=TENANT).status_code == 404

    assert http.put(url, json={"situacao": "CORRIGIDA", "responsavel": "Faturamento HRVJ"}, headers=TENANT).status_code == 200
    corpo = http.put(url, json={"situacao": "REAPRESENTADA", "competencia_reapresentacao": "202609"},
                     headers=TENANT).json()
    assert corpo["tratativa"]["situacao"] == "REAPRESENTADA" and corpo["cnes"] == HRVJ
    assert [h["situacao"] for h in corpo["historico"]] == ["REAPRESENTADA", "CORRIGIDA"]

    kit = _get(http, "/api/revenue-scan/kit?uf=CE&referencia=202609")
    p1 = next(i for i in kit["itens"] if i["n_aih"] == "P1")
    assert p1["tratativa"]["situacao"] == "REAPRESENTADA" and p1["tratativa"]["competencia_reapresentacao"] == "202609"
    # Para trabalhar: P1, H1, B1, S1, O1, G1 — capacidade saiu da lista (não reapresentável).
    assert kit["tratativas"] == {"REAPRESENTADA": 1, "SEM_SITUACAO": 5}


def test_situacao_fora_do_escopo(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato({"cnes": [HRC]})))
    assert http.put("/api/revenue-scan/aih/P1/treatment", json={"situacao": "A_FAZER"}, headers=TENANT).status_code == 404
    assert http.put("/api/revenue-scan/aih/B1/treatment", json={"situacao": "A_FAZER"}, headers=TENANT).status_code == 200
