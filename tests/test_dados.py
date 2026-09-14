"""
Tela de atualização dos dados.

O que estes testes travam:
- só a administração da plataforma vê e dispara carga;
- o status mostra, por UF, os meses carregados, o CNES e o scan, e marca a UF atrás;
- a carga vai para a fila por UF, TODAS vira as 27, e UF já na fila não duplica;
- competência inválida e UF inexistente são recusadas;
- sem Redis o status ainda abre e o pedido responde 503;
- as competências publicadas vêm do FTP, marcando as já carregadas.
"""
import httpx
import pytest

from app.jobs.recalcular import recalcular
from app.models import SihHospitalMonth
from tests.conftest import contrato, token
from tests.test_scan_api import popular

ADMIN = {"Authorization": f"Bearer {token(role='platform_admin', tenant_id=None)}"}


def _admin(app_com_nucleo):
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato(platform_admin=True, tenant_id=None,
                                                                          role="platform_admin")))
    return http


@pytest.fixture
def fila_falsa(monkeypatch):
    estado = {"trabalhos": [], "cargas": [], "recalculos": []}

    def enfileirar_carga_uf(uf, competencias=None, quantidade=3):
        estado["cargas"].append((uf, competencias, quantidade))
        return f"job-{uf}"

    def enfileirar_recalculo(ufs=None):
        estado["recalculos"].append(ufs)
        return "job-recalculo"

    monkeypatch.setattr("app.jobs.fila.trabalhos", lambda limite=30: estado["trabalhos"])
    monkeypatch.setattr("app.jobs.fila.enfileirar_carga_uf", enfileirar_carga_uf)
    monkeypatch.setattr("app.jobs.fila.enfileirar_recalculo", enfileirar_recalculo)
    return estado


def test_so_administracao_da_plataforma(app_com_nucleo, fila_falsa):
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    cabecalho = {"Authorization": f"Bearer {token()}"}
    assert http.get("/api/revenue-scan/data/status", headers=cabecalho).status_code == 403
    assert http.post("/api/revenue-scan/data/loads", json={"ufs": ["CE"]}, headers=cabecalho).status_code == 403
    assert fila_falsa["cargas"] == []


def test_status_por_uf(app_com_nucleo, fabrica_sessao, fila_falsa):
    with fabrica_sessao() as db:
        popular(db)
        db.add(SihHospitalMonth(uf="PE", cnes="2600001", competencia="202606", aih_aprovadas=10, valor_aprovado=1.0,
                                aih_rejeitadas=0, valor_rejeitado=0.0, diarias=0, permanencia_dias=0))
        db.commit()
        recalcular(db, ufs=["CE"])
    fila_falsa["trabalhos"] = [{"id": "job-CE", "tipo": "CARGA", "ufs": ["CE"], "status": "RODANDO"}]
    corpo = _admin(app_com_nucleo).get("/api/revenue-scan/data/status", headers=ADMIN).json()

    ufs = {u["uf"]: u for u in corpo["ufs"]}
    assert len(ufs) == 27 and corpo["mais_recente"] == "202607"
    ce = ufs["CE"]
    assert [c["competencia"] for c in ce["competencias"]] == ["202605", "202606", "202607"]
    assert ce["hospitais"] == 9 and ce["cnes_competencia"] == "202607" and not ce["desatualizada"]
    assert ce["scan"]["hospitais"] == 9 and ce["scan"]["periodo_fim"] == "202607"
    assert ufs["PE"]["desatualizada"] and ufs["PE"]["scan"] is None
    assert ufs["SP"]["competencias"] == [] and not ufs["SP"]["desatualizada"]
    assert corpo["fila"] == {"disponivel": True, "trabalhos": fila_falsa["trabalhos"]}


def test_enfileira_por_uf_sem_duplicar(app_com_nucleo, fila_falsa):
    http = _admin(app_com_nucleo)
    fila_falsa["trabalhos"] = [{"id": "x", "tipo": "CARGA", "ufs": ["CE"], "status": "NA_FILA"}]
    resposta = http.post("/api/revenue-scan/data/loads", headers=ADMIN,
                         json={"ufs": ["ce", "PE"], "competencias": ["202607", "202606"]})
    assert resposta.status_code == 202
    assert resposta.json() == {"trabalhos": [{"uf": "PE", "id": "job-PE"}], "ignoradas": ["CE"]}
    assert fila_falsa["cargas"] == [("PE", ["202606", "202607"], 3)]

    todas = http.post("/api/revenue-scan/data/loads", headers=ADMIN, json={"ufs": ["TODAS"], "quantidade": 12}).json()
    assert len(todas["trabalhos"]) == 26 and todas["ignoradas"] == ["CE"]

    assert http.post("/api/revenue-scan/data/loads", headers=ADMIN,
                     json={"ufs": ["CE"], "competencias": ["2026-07"]}).status_code == 422
    assert http.post("/api/revenue-scan/data/loads", headers=ADMIN, json={"ufs": ["XX"]}).status_code == 422
    assert http.post("/api/revenue-scan/data/loads", headers=ADMIN, json={"ufs": []}).status_code == 422

    assert http.post("/api/revenue-scan/data/recalculate", headers=ADMIN, json={"ufs": ["CE"]}).json() == {"id": "job-recalculo"}
    assert fila_falsa["recalculos"] == [["CE"]]


def test_sem_redis(app_com_nucleo, monkeypatch):
    def fora(*_, **__):
        raise ConnectionError("redis")

    monkeypatch.setattr("app.jobs.fila.trabalhos", fora)
    monkeypatch.setattr("app.jobs.fila.enfileirar_carga_uf", fora)
    http = _admin(app_com_nucleo)
    status = http.get("/api/revenue-scan/data/status", headers=ADMIN).json()
    assert status["fila"]["disponivel"] is False and len(status["ufs"]) == 27
    resposta = http.post("/api/revenue-scan/data/loads", headers=ADMIN, json={"ufs": ["CE"]})
    assert resposta.status_code == 503 and "Redis" in resposta.json()["detail"]


def test_erro_do_trabalho_e_a_linha_da_excecao():
    from app.jobs.fila import _mensagem_de_erro

    traceback = (
        "Traceback (most recent call last):\n  File \"x.py\", line 1\n"
        "sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) conexão recusada\n"
        "(Background on this error at: https://sqlalche.me/e/20/e3q8)\n"
    )
    assert _mensagem_de_erro(traceback).startswith("sqlalchemy.exc.OperationalError: (psycopg2")
    assert _mensagem_de_erro("app.adapters.datasus.ArquivoIndisponivel: RDCE2608.dbc") == \
        "app.adapters.datasus.ArquivoIndisponivel: RDCE2608.dbc"
    assert "sem mensagem" in _mensagem_de_erro(None)


def test_competencias_publicadas(app_com_nucleo, fabrica_sessao, monkeypatch):
    with fabrica_sessao() as db:
        popular(db)

    class Falso:
        def __init__(self, meses):
            self.meses = meses

        def competencias_disponiveis(self, uf):
            return self.meses

    monkeypatch.setattr("app.jobs.carga_sih.adapters_padrao", lambda pasta_local=None: {
        "RD": Falso(["202608", "202607", "202606"]), "RJ": Falso(["202608", "202607"]), "ER": Falso(["202608", "202607"]),
    })
    corpo = _admin(app_com_nucleo).get("/api/revenue-scan/data/published?uf=CE", headers=ADMIN).json()
    assert corpo == {"uf": "CE", "publicadas": [{"competencia": "202608", "carregada": False},
                                                {"competencia": "202607", "carregada": True}]}
