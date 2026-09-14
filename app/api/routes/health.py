from fastapi import APIRouter

from app import __version__

router = APIRouter(tags=["saude"])


@router.get("/health")
def saude() -> dict[str, str]:
    """Sem login: é o que o compose e o balanceador consultam."""
    return {"status": "ok", "service": "medops-revenue-scan", "version": __version__}
