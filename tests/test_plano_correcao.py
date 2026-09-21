"""Propostas não podem virar correções aplicadas ou inventar campos."""
from copy import deepcopy

import httpx
import pytest

from app.domain.plano_correcao import montar_plano, REGRAS
from tests.conftest import contrato, token
from tests.test_kit import _dados
from tests.test_scan_api import HRVJ, HRC
from app.models import SihRejection


def historia(codigos=None):
    return {"cnes": HRVJ, "n_aih": "1234567890123", "eventos": [
        {"competencia": "202605", "situacao": "REJEITADA", "motivos": codigos if codigos is not None else ["060109"],
         "campos": {"dt_saida": "2026-05-10", "procedimento": "0301010001", "valor": 2000},
         "fonte": None, "mudancas": []},
    ], "leitura": "Histórico público"}


@pytest.mark.parametrize("codigo", sorted(REGRAS))
def test_regra_explica_sem_inventar_nem_alterar(codigo):
    h = historia([codigo])
    original = deepcopy(h)
    p = montar_plano(h, "202609")
    assert h == original
    assert p["motivos_cobertos"] == p["motivos_total"] == 1
    r = p["planos"][0]
    assert all(r[k] for k in ["problema", "antes", "depois", "acao", "porque", "documentos", "fonte_descricao"])
    assert r["valor_proposto"] is None
    assert r["natureza"] == "PROPOSTA_CONDICIONAL"
    assert p["alteracoes_aplicadas"] == 0
    assert p["pronta_para_reapresentar"] is False


def test_motivo_desconhecido_nao_desaparece():
    p = montar_plano(historia(["060109", "999999", "060109"]), "202609")
    assert p["motivos_cobertos"] == 1 and p["motivos_total"] == 2
    assert p["planos"][1]["natureza"] == "SEM_REGRA"
    assert "não cobre toda" in " ".join(p["pendencias"])


def test_sem_motivo_sem_alta_e_janela_vencida():
    h = historia([])
    h["eventos"][0]["campos"]["dt_saida"] = None
    p = montar_plano(h, "202609")
    assert p["motivos_total"] == 0
    assert p["prazo_estimado"] is None
    assert "Data de alta indisponível" in p["pendencias"][0]
    p = montar_plano(historia(), "202612")
    assert p["prazo_estimado"] == "202610"
    assert "encerrada" in p["pendencias"][0]


def test_aprovacao_anterior_ou_mesmo_mes_nao_resolve_ultima_rejeicao():
    h = historia()
    h["eventos"].append({**deepcopy(h["eventos"][0]), "situacao": "APROVADA"})
    assert montar_plano(h, "202609")["aprovacao_posterior"] is None
    h["eventos"][-1]["competencia"] = "202606"
    p = montar_plano(h, "202609")
    assert p["aprovacao_posterior"]["competencia"] == "202606"
    assert not p["pronta_para_reapresentar"]  # aprovação não comprova nossa proposta
    h["eventos"].append({**deepcopy(h["eventos"][0]), "competencia": "202607", "motivos": ["999999"]})
    p = montar_plano(h, "202609")
    assert p["aprovacao_posterior"] is None
    assert p["planos"][0]["codigo"] == "999999"


def test_api_plano_autorizacao_e_validacao(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    with fabrica_sessao() as db:
        numero = db.query(SihRejection).filter_by(cnes=HRVJ).first().n_aih
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    url = f"/api/revenue-scan/hospitals/{HRVJ}/aih/{numero}/plano-correcao"
    headers = {"Authorization": f"Bearer {token()}"}
    assert http.get(url).status_code == 401
    r = http.get(url + "?referencia=202609", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["n_aih"] == numero
    assert r.json()["alteracoes_aplicadas"] == 0
    assert http.get(url + "?referencia=202613", headers=headers).status_code == 422
    assert http.get(url.replace(numero, "inexistente"), headers=headers).status_code == 404


def test_api_plano_nao_expande_escopo(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato({"cnes": [HRC]})))
    assert http.get(f"/api/revenue-scan/hospitals/{HRVJ}/aih/P1/plano-correcao",
                    headers={"Authorization": f"Bearer {token()}"}).status_code == 404
