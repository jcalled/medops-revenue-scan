"""Leitura do JWT emitido pelo núcleo da plataforma."""
from __future__ import annotations

from dataclasses import dataclass

from jose import JWTError, jwt

from app.config import Settings


class NaoAutenticado(Exception):
    """Token ausente, inválido, expirado ou recusado pelo núcleo."""


@dataclass(frozen=True)
class Principal:
    user_id: int
    tenant_id: int | None
    role: str
    slug: str | None

    @property
    def platform_admin(self) -> bool:
        return self.role == "platform_admin"


def ler_token(token: str, settings: Settings) -> Principal:
    """
    Mesmas regras do núcleo: sub e role obrigatórios; tenant_id obrigatório,
    exceto para o administrador da plataforma.
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_alg])
    except JWTError as exc:
        raise NaoAutenticado("Token inválido ou expirado") from exc

    sub, role, tenant_id = payload.get("sub"), payload.get("role"), payload.get("tenant_id")
    if not sub or not role:
        raise NaoAutenticado("Token incompleto")
    try:
        user_id = int(sub)
    except (TypeError, ValueError) as exc:
        raise NaoAutenticado("Token inválido") from exc

    if role == "platform_admin":
        return Principal(user_id=user_id, tenant_id=None, role=role, slug=payload.get("slug"))
    try:
        tenant = int(tenant_id)
    except (TypeError, ValueError) as exc:
        raise NaoAutenticado("Token sem tenant_id") from exc
    return Principal(user_id=user_id, tenant_id=tenant, role=role, slug=payload.get("slug"))
