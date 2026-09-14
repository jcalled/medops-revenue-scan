"""Quem pode chamar as rotas do Revenue Scan."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings
from app.entitlements import EntitlementClient, NaoContratado, NucleoIndisponivel
from app.security import NaoAutenticado, Principal, ler_token

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Acesso:
    principal: Principal
    entitlement: dict[str, Any]

    @property
    def escopo(self) -> dict[str, Any]:
        return dict(self.entitlement.get("scope") or {})


@lru_cache(maxsize=1)
def _cliente_padrao() -> EntitlementClient:
    settings = get_settings()
    return EntitlementClient(settings.core_api_url, settings.entitlement_cache_seconds)


def get_entitlement_client() -> EntitlementClient:
    return _cliente_padrao()


def _nao_autenticado(detalhe: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detalhe, headers={"WWW-Authenticate": "Bearer"})


def require_revenue_scan(
    credenciais: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
    cliente: EntitlementClient = Depends(get_entitlement_client),
) -> Acesso:
    if credenciais is None or credenciais.scheme.lower() != "bearer":
        raise _nao_autenticado("Login necessário")
    try:
        principal = ler_token(credenciais.credentials, settings)
        resposta = cliente.consultar(credenciais.credentials)
    except NaoAutenticado as exc:
        raise _nao_autenticado(str(exc)) from exc
    except NaoContratado as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="O Revenue Scan SUS não está no contrato deste tenant.") from exc
    except NucleoIndisponivel as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Não foi possível conferir o contrato agora; o acesso fica bloqueado "
                                   "até a plataforma responder.") from exc

    # O núcleo responde pelo usuário do token. Conferir que é o mesmo tenant
    # impede aceitar a resposta de outro — um proxy mal configurado, um cache.
    if not principal.platform_admin and resposta.get("tenant_id") != principal.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Contrato de outro tenant.")
    if principal.platform_admin and not resposta.get("platform_admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Perfil do token não confere com a plataforma.")
    return Acesso(principal=principal, entitlement=dict(resposta.get("entitlement") or {}))
