"""
Ambiente de teste: nada de banco real nem núcleo real.

As variáveis são definidas antes de importar o app, porque a configuração é
lida uma vez.
"""
import os

os.environ.update({
    "APP_ENV": "test",
    "JWT_SECRET": "segredo-de-teste-com-mais-de-trinta-e-dois-caracteres",
    "CORE_API_URL": "http://nucleo.test",
    "DATABASE_URL": "sqlite://",
    "CORS_ORIGINS": "http://localhost:3000",
})

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from jose import jwt  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402


@pytest.fixture
def fabrica_sessao():
    from app.models import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def token(**extra) -> str:
    from app.config import get_settings

    settings = get_settings()
    claims = {"sub": "10", "tenant_id": 1, "slug": "isgh", "role": "admin", **extra}
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_alg)


def contrato(escopo: dict | None = None, **extra) -> dict:
    return {
        "tenant_id": 1, "user_id": 10, "role": "admin", "platform_admin": False,
        "entitlement": {"product": "REVENUE_SCAN_SUS", "status": "ACTIVE", "modules": None, "scope": escopo or {}},
        **extra,
    }


@pytest.fixture
def app_com_nucleo(fabrica_sessao):
    """App com o núcleo simulado respondendo `responder` e banco SQLite em memória."""
    from app.api.deps import get_entitlement_client
    from app.db import get_db
    from app.entitlements import EntitlementClient
    from app.main import create_app

    def _montar(responder):
        chamadas: list[httpx.Request] = []

        def _registrar(request: httpx.Request) -> httpx.Response:
            chamadas.append(request)
            return responder(request)

        def _db():
            sessao = fabrica_sessao()
            try:
                yield sessao
            finally:
                sessao.close()

        app = create_app()
        cliente = EntitlementClient("http://nucleo.test", cache_seconds=60, transport=httpx.MockTransport(_registrar))
        app.dependency_overrides[get_entitlement_client] = lambda: cliente
        app.dependency_overrides[get_db] = _db
        return TestClient(app), chamadas

    return _montar
