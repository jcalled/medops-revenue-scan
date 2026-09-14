"""
Quem entra no Revenue Scan.

O que estes testes travam:
- sem token, ou com token que o núcleo não assinou, não entra — e nem chega a
  perguntar ao núcleo;
- token válido de tenant sem o produto contratado recebe 403;
- núcleo fora do ar bloqueia (503) em vez de liberar;
- a resposta do núcleo precisa ser do mesmo tenant do token;
- a resposta fica em cache: o núcleo não é consultado a cada requisição.
"""
import httpx
from jose import jwt

from tests.conftest import contrato, token

CONTRATO = contrato({"cnes": ["2785900"], "pdf": True})
CONTRATO["entitlement"]["modules"] = ["DASHBOARD_OSS"]


def _cabecalho(valor: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {valor}"}


def test_saude_nao_exige_login(app_com_nucleo):
    http, _ = app_com_nucleo(lambda r: httpx.Response(500))
    assert http.get("/health").json()["status"] == "ok"


def test_sem_token_ou_token_forjado_nao_entra(app_com_nucleo):
    http, chamadas = app_com_nucleo(lambda r: httpx.Response(200, json=CONTRATO))
    assert http.get("/api/revenue-scan/me").status_code == 401
    forjado = jwt.encode({"sub": "10", "tenant_id": 1, "role": "admin"}, "outro-segredo", algorithm="HS256")
    assert http.get("/api/revenue-scan/me", headers=_cabecalho(forjado)).status_code == 401
    assert not chamadas


def test_tenant_sem_contrato_recebe_403(app_com_nucleo):
    http, _ = app_com_nucleo(lambda r: httpx.Response(403, json={"detail": "Produto REVENUE_SCAN_SUS não contratado"}))
    resposta = http.get("/api/revenue-scan/me", headers=_cabecalho(token()))
    assert resposta.status_code == 403
    assert "contrato" in resposta.json()["detail"]


def test_tenant_com_contrato_ve_o_escopo(app_com_nucleo):
    http, chamadas = app_com_nucleo(lambda r: httpx.Response(200, json=CONTRATO))
    valor = token()
    corpo = http.get("/api/revenue-scan/me", headers=_cabecalho(valor)).json()
    assert corpo["tenant_id"] == 1 and corpo["modules"] == ["DASHBOARD_OSS"]
    assert corpo["scope"] == {"cnes": ["2785900"], "pdf": True}
    assert chamadas[0].url.path == "/platform/me/entitlements/REVENUE_SCAN_SUS"
    assert chamadas[0].headers["Authorization"] == f"Bearer {valor}"


def test_nucleo_fora_do_ar_bloqueia(app_com_nucleo):
    def recusar(request):
        raise httpx.ConnectError("conexão recusada", request=request)

    http, _ = app_com_nucleo(recusar)
    assert http.get("/api/revenue-scan/me", headers=_cabecalho(token())).status_code == 503


def test_erro_do_nucleo_bloqueia(app_com_nucleo):
    http, _ = app_com_nucleo(lambda r: httpx.Response(500))
    assert http.get("/api/revenue-scan/me", headers=_cabecalho(token())).status_code == 503


def test_resposta_de_outro_tenant_e_recusada(app_com_nucleo):
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json={**CONTRATO, "tenant_id": 2}))
    assert http.get("/api/revenue-scan/me", headers=_cabecalho(token())).status_code == 403


def test_contrato_fica_em_cache(app_com_nucleo):
    http, chamadas = app_com_nucleo(lambda r: httpx.Response(200, json=CONTRATO))
    valor = token()
    for _ in range(3):
        assert http.get("/api/revenue-scan/me", headers=_cabecalho(valor)).status_code == 200
    assert len(chamadas) == 1


def test_administrador_da_plataforma_entra_sem_tenant(app_com_nucleo):
    resposta = {"tenant_id": None, "user_id": 99, "role": "platform_admin", "platform_admin": True,
                "entitlement": {"product": "REVENUE_SCAN_SUS", "status": "ACTIVE", "modules": None, "scope": {}}}
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json=resposta))
    corpo = http.get("/api/revenue-scan/me", headers=_cabecalho(token(sub="99", tenant_id=None, role="platform_admin"))).json()
    assert corpo["platform_admin"] is True and corpo["tenant_id"] is None


def test_token_de_tenant_com_resposta_de_administrador_nao_vira_administrador(app_com_nucleo):
    http, _ = app_com_nucleo(lambda r: httpx.Response(200, json={**CONTRATO, "platform_admin": True}))
    assert http.get("/api/revenue-scan/me", headers=_cabecalho(token())).json()["platform_admin"] is False
