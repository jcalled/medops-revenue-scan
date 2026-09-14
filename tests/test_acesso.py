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
import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.api.deps import get_entitlement_client
from app.config import get_settings
from app.entitlements import EntitlementClient
from app.main import create_app

CONTRATO = {
    "tenant_id": 1, "user_id": 10, "role": "admin", "platform_admin": False,
    "entitlement": {"product": "REVENUE_SCAN_SUS", "status": "ACTIVE", "modules": ["DASHBOARD_OSS"],
                    "scope": {"cnes": ["2785900"], "pdf": True}},
}


def _token(**extra) -> str:
    settings = get_settings()
    claims = {"sub": "10", "tenant_id": 1, "slug": "isgh", "role": "admin", **extra}
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_alg)


def _cabecalho(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def montar():
    def _montar(responder):
        chamadas: list[httpx.Request] = []

        def _registrar(request: httpx.Request) -> httpx.Response:
            chamadas.append(request)
            return responder(request)

        app = create_app()
        cliente = EntitlementClient("http://nucleo.test", cache_seconds=60, transport=httpx.MockTransport(_registrar))
        app.dependency_overrides[get_entitlement_client] = lambda: cliente
        return TestClient(app), chamadas

    return _montar


def test_saude_nao_exige_login(montar):
    http, _ = montar(lambda r: httpx.Response(500))
    assert http.get("/health").json()["status"] == "ok"


def test_sem_token_ou_token_forjado_nao_entra(montar):
    http, chamadas = montar(lambda r: httpx.Response(200, json=CONTRATO))
    assert http.get("/api/revenue-scan/me").status_code == 401
    forjado = jwt.encode({"sub": "10", "tenant_id": 1, "role": "admin"}, "outro-segredo", algorithm="HS256")
    assert http.get("/api/revenue-scan/me", headers=_cabecalho(forjado)).status_code == 401
    assert not chamadas


def test_tenant_sem_contrato_recebe_403(montar):
    http, _ = montar(lambda r: httpx.Response(403, json={"detail": "Produto REVENUE_SCAN_SUS não contratado"}))
    resposta = http.get("/api/revenue-scan/me", headers=_cabecalho(_token()))
    assert resposta.status_code == 403
    assert "contrato" in resposta.json()["detail"]


def test_tenant_com_contrato_ve_o_escopo(montar):
    http, chamadas = montar(lambda r: httpx.Response(200, json=CONTRATO))
    token = _token()
    corpo = http.get("/api/revenue-scan/me", headers=_cabecalho(token)).json()
    assert corpo["tenant_id"] == 1 and corpo["modules"] == ["DASHBOARD_OSS"]
    assert corpo["scope"] == {"cnes": ["2785900"], "pdf": True}
    assert chamadas[0].url.path == "/platform/me/entitlements/REVENUE_SCAN_SUS"
    assert chamadas[0].headers["Authorization"] == f"Bearer {token}"


def test_nucleo_fora_do_ar_bloqueia(montar):
    def recusar(request):
        raise httpx.ConnectError("conexão recusada", request=request)

    http, _ = montar(recusar)
    assert http.get("/api/revenue-scan/me", headers=_cabecalho(_token())).status_code == 503


def test_erro_do_nucleo_bloqueia(montar):
    http, _ = montar(lambda r: httpx.Response(500))
    assert http.get("/api/revenue-scan/me", headers=_cabecalho(_token())).status_code == 503


def test_resposta_de_outro_tenant_e_recusada(montar):
    http, _ = montar(lambda r: httpx.Response(200, json={**CONTRATO, "tenant_id": 2}))
    assert http.get("/api/revenue-scan/me", headers=_cabecalho(_token())).status_code == 403


def test_contrato_fica_em_cache(montar):
    http, chamadas = montar(lambda r: httpx.Response(200, json=CONTRATO))
    token = _token()
    for _ in range(3):
        assert http.get("/api/revenue-scan/me", headers=_cabecalho(token)).status_code == 200
    assert len(chamadas) == 1


def test_administrador_da_plataforma_entra_sem_tenant(montar):
    resposta = {"tenant_id": None, "user_id": 99, "role": "platform_admin", "platform_admin": True,
                "entitlement": {"product": "REVENUE_SCAN_SUS", "status": "ACTIVE", "modules": None, "scope": {}}}
    http, _ = montar(lambda r: httpx.Response(200, json=resposta))
    token = _token(sub="99", tenant_id=None, role="platform_admin")
    corpo = http.get("/api/revenue-scan/me", headers=_cabecalho(token)).json()
    assert corpo["platform_admin"] is True and corpo["tenant_id"] is None


def test_token_de_tenant_com_resposta_de_administrador_e_recusado(montar):
    """Token de tenant nunca vira acesso de plataforma, mesmo que a resposta diga."""
    resposta = {**CONTRATO, "platform_admin": True}
    http, _ = montar(lambda r: httpx.Response(200, json=resposta))
    assert http.get("/api/revenue-scan/me", headers=_cabecalho(_token())).json()["platform_admin"] is False
