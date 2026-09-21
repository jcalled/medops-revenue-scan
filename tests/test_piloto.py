"""
Piloto de êxito: provar a recuperação antes do contrato.

O que estes testes travam:
- as candidatas são só as reapresentáveis, da que vence primeiro para a última;
- o piloto acompanha só as AIH escolhidas: rejeição nova do hospital não entra;
- AIH que não dá mais para reapresentar é recusada;
- volta aprovada no RD depois do início conta como êxito, com mês e valor, e o pacote traz só as que faltam;
- só a administração da plataforma mexe.
"""
from app.api.routes import piloto
from app.models import SihApprovedAih
from tests.conftest import token
from tests.test_dados import ADMIN, _admin
from tests.test_kit import _dados, _rejeitar
from tests.test_scan_api import HRC, HRVJ


def test_piloto_acompanha_so_as_escolhidas(app_com_nucleo, fabrica_sessao, monkeypatch):
    monkeypatch.setattr(piloto, "referencia_padrao", lambda: "202610")
    _dados(fabrica_sessao)
    http = _admin(app_com_nucleo)

    c = http.get(f"/api/revenue-scan/pilot/candidates?cnes={HRVJ},{HRC}", headers=ADMIN).json()
    assert [a["n_aih"] for a in c["aih"]] == ["H1", "P1", "B1"]          # vence out, nov, dez
    assert c["aih"][0]["vence_neste_mes"] and c["aih"][1]["botao_faturasus"]
    com_gestor = http.get(f"/api/revenue-scan/pilot/candidates?cnes={HRVJ}&incluir_gestor=true", headers=ADMIN).json()
    assert "G1" in [a["n_aih"] for a in com_gestor["aih"]]

    r = http.post("/api/revenue-scan/pilot", headers=ADMIN,
                  json={"cnes": [HRVJ, HRC], "aih": ["P1", "H1", "V1"], "nome": "Piloto HRVJ"})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["fora_do_prazo"] == ["V1"] and p["indicadas"] == {"aih": 2, "valor": 3000.0} and p["inicio"] == "202610"

    with fabrica_sessao() as db:
        db.add(SihApprovedAih(uf="CE", competencia="202610", cnes=HRVJ, n_aih="P1", valor=950))
        _rejeitar(db, HRVJ, "202610", "N1", 900.0, "060109", None)       # rejeição nova: não é do piloto
        db.commit()
    r = http.post(f"/api/revenue-scan/pilot/{p['id']}/check", headers=ADMIN).json()
    assert r["recuperadas_agora"] == 1 and r["voltaram"] == {"aih": 1, "valor": 950.0}
    assert r["por_mes"] == [{"competencia": "202610", "aih": 1, "valor": 950.0}] and r["honorarios"] == 142.5

    d = http.get(f"/api/revenue-scan/pilot/{p['id']}", headers=ADMIN).json()
    assert [(i["n_aih"], i["situacao"]) for i in d["itens"]] == [("P1", "RECUPERADA"), ("H1", "EM_ABERTO")]
    assert [x["id"] for x in http.get("/api/revenue-scan/pilot", headers=ADMIN).json()["pilotos"]] == [p["id"]]

    pacote = http.get(f"/api/revenue-scan/pilot/{p['id']}/package", headers=ADMIN)
    assert pacote.status_code == 200, pacote.text
    assert "H1" in pacote.text and '"P1"' not in pacote.text and "B1" not in pacote.text

    assert http.post("/api/revenue-scan/pilot", headers=ADMIN, json={"cnes": [HRVJ], "aih": ["V1"]}).status_code == 422
    assert http.get("/api/revenue-scan/pilot", headers={"Authorization": f"Bearer {token()}"}).status_code == 403
