"""
Relatório de recuperação por hospital e a carga do DATASUS na hora.

O que estes testes travam:
- cada AIH cai num grupo só: recuperada (aprovada depois da rejeição), a recuperar, depende do gestor, perdida;
- recuperada entra com o mês da aprovação, e o percentual incide sobre ela — só a partir do início do contrato;
- o percentual sobre o que falta recuperar é estimativa, à parte;
- por hospital e por mês de rejeição, com os principais motivos e o que o botão do FaturaSUS corrige;
- sem organização nem hospitais, o relatório é recusado;
- atualizar é da administração da plataforma e só enfileira os meses publicados que faltam, por UF.
"""
import httpx

from app.models import SihHospitalMonth
from tests.conftest import contrato, token
from tests.test_dados import ADMIN, _admin, fila_falsa  # noqa: F401 — fixture
from tests.test_kit import _dados, _get
from tests.test_scan_api import HRC, HRVJ


def test_grupos_por_hospital_e_mes(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    rel = _get(http, f"/api/revenue-scan/recovery-report?cnes={HRVJ},{HRC}&referencia=202610")

    t = rel["total"]
    assert t["rejeitadas"] == {"aih": 38, "valor": 462600.0}
    assert t["RECUPERADA"] == {"aih": 1, "valor": 15000.0}
    assert t["A_RECUPERAR"] == {"aih": 3, "valor": 3700.0}          # profissional x2 e habilitação
    assert t["DEPENDE_GESTOR"] == {"aih": 3, "valor": 5300.0}       # gestor e investigar
    assert t["PERDIDA"] == {"aih": 31, "valor": 438600.0}           # capacidade e prazo
    assert t["botao"] == {"aih": 3, "valor": 2500.0}                # 060109 é do botão
    assert t["vence_neste_mes"] == {"aih": 1, "valor": 2000.0}
    assert rel["recuperado_por_mes"] == [{"competencia": "202606", "aih": 1, "valor": 15000.0}]
    assert rel["taxa"] == {"sobre_recuperado": 2250.0, "recuperado_cobravel": 15000.0,
                           "estimada_sobre_a_recuperar": 555.0, "estimada_com_gestor": 1350.0}

    [hrvj, hrc] = rel["hospitais"]
    assert (hrvj["cnes"], hrc["cnes"]) == (HRVJ, HRC)
    assert [m["competencia"] for m in hrvj["meses"]] == ["202605", "202606"]
    assert hrvj["meses"][0]["RECUPERADA"]["aih"] == 1 and hrvj["meses"][0]["PERDIDA"]["aih"] == 29
    assert [(m["codigo"], m["aih"], m["valor"]) for m in hrvj["motivos"]] == [
        ("010003", 1, 4000.0), ("060120", 1, 2000.0), ("060109", 2, 1800.0), ("999999", 1, 500.0)]
    assert hrc["total"]["A_RECUPERAR"] == {"aih": 1, "valor": 700.0} and hrc["taxa"]["sobre_recuperado"] == 0


def test_inicio_do_contrato_e_percentual(app_com_nucleo, fabrica_sessao):
    _dados(fabrica_sessao)
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    rel = _get(http, f"/api/revenue-scan/recovery-report?cnes={HRVJ}&referencia=202610&inicio=202607&percentual=20")
    # Aprovada em jun, antes do início: fica no relatório, mas não é cobrável.
    assert rel["total"]["RECUPERADA"]["aih"] == 1
    assert rel["taxa"]["recuperado_cobravel"] == 0 and rel["taxa"]["sobre_recuperado"] == 0
    assert rel["taxa"]["estimada_sobre_a_recuperar"] == 600.0      # 20% de 3.000


def test_sem_selecao_e_recusado(app_com_nucleo):
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=contrato()))
    resposta = http.get("/api/revenue-scan/recovery-report", headers={"Authorization": f"Bearer {token()}"})
    assert resposta.status_code == 422


def test_atualizar_so_os_meses_que_faltam(app_com_nucleo, fabrica_sessao, fila_falsa, monkeypatch):  # noqa: F811
    _dados(fabrica_sessao)
    with fabrica_sessao() as db:
        carregados = set(db.query(SihHospitalMonth.competencia).filter(SihHospitalMonth.uf == "CE").distinct().all())

    class Falso:
        def competencias_disponiveis(self, uf):
            return ["202605", "202606", "202607", "202608"]

    monkeypatch.setattr("app.jobs.carga_sih.adapters_padrao", lambda pasta_local=None: {t: Falso() for t in ("RD", "RJ", "ER")})
    http = _admin(app_com_nucleo)

    cliente = http.post("/api/revenue-scan/recovery-report/refresh", json={"cnes": [HRVJ]},
                        headers={"Authorization": f"Bearer {token()}"})
    assert cliente.status_code == 403 and fila_falsa["cargas"] == []

    corpo = http.post("/api/revenue-scan/recovery-report/refresh", json={"cnes": [HRVJ], "meses": 4}, headers=ADMIN).json()
    [ce] = corpo["ufs"]
    faltando = [c for c in ["202605", "202606", "202607", "202608"] if (c,) not in carregados]
    assert ce["uf"] == "CE" and ce["faltando"] == faltando and ce["situacao"] == "ENFILEIRADA"
    assert fila_falsa["cargas"] == [("CE", faltando, len(faltando))]

    # UF já na fila não duplica.
    fila_falsa["trabalhos"].append({"tipo": "CARGA", "status": "RODANDO", "ufs": ["CE"]})
    corpo = http.post("/api/revenue-scan/recovery-report/refresh", json={"cnes": [HRVJ], "meses": 4}, headers=ADMIN).json()
    assert corpo["ufs"][0]["situacao"] == "JA_NA_FILA" and len(fila_falsa["cargas"]) == 1
