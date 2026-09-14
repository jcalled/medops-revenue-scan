from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import Acesso, require_revenue_scan
from app.config import PRODUTO

router = APIRouter(prefix="/api/revenue-scan", tags=["revenue-scan"])


@router.get("/me")
def meu_acesso(acesso: Acesso = Depends(require_revenue_scan)) -> dict[str, Any]:
    """O que o contrato libera para quem está logado."""
    e = acesso.entitlement
    return {
        "service": "medops-revenue-scan",
        "tenant_id": acesso.principal.tenant_id,
        "user_id": acesso.principal.user_id,
        "role": acesso.principal.role,
        "platform_admin": acesso.principal.platform_admin,
        "product": e.get("product", PRODUTO),
        "status": e.get("status"),
        "modules": e.get("modules"),
        "scope": acesso.escopo,
    }
