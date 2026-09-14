"""
Conferência do contrato no núcleo da plataforma.

Nega por padrão: qualquer resposta que não seja 200 — inclusive núcleo fora do
ar — bloqueia o acesso. Liberar quando não dá para conferir entregaria dado de
OSS a quem não contratou.

A resposta fica em cache por alguns segundos, pelo hash do token: sem isso
cada requisição do painel viraria uma ida ao núcleo. O custo é o contrato
ativado ou suspenso levar até `cache_seconds` para valer aqui.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

import httpx

from app.config import PRODUTO
from app.security import NaoAutenticado


class NaoContratado(Exception):
    """O tenant do token não tem o Revenue Scan ativo e vigente."""


class NucleoIndisponivel(Exception):
    """Não foi possível conferir o contrato."""


_LIMITE_CACHE = 5_000


class EntitlementClient:
    def __init__(self, base_url: str, cache_seconds: int = 60, *,
                 transport: httpx.BaseTransport | None = None, timeout: float = 5.0):
        self._base_url = base_url.rstrip("/")
        self._cache_seconds = max(0, cache_seconds)
        self._transport = transport
        self._timeout = timeout
        self._cache: dict[str, tuple[float, dict[str, Any] | NaoContratado]] = {}

    def consultar(self, token: str) -> dict[str, Any]:
        chave = hashlib.sha256(token.encode()).hexdigest()
        guardado = self._cache.get(chave)
        if guardado and guardado[0] > time.monotonic():
            if isinstance(guardado[1], NaoContratado):
                raise guardado[1]
            return guardado[1]

        try:
            with httpx.Client(base_url=self._base_url, transport=self._transport, timeout=self._timeout) as http:
                resposta = http.get(
                    f"/platform/me/entitlements/{PRODUTO}", headers={"Authorization": f"Bearer {token}"}
                )
        except httpx.HTTPError as exc:
            raise NucleoIndisponivel(f"Núcleo inacessível: {exc.__class__.__name__}") from exc

        if resposta.status_code == 401:
            raise NaoAutenticado("Token recusado pelo núcleo")
        if resposta.status_code in (403, 404):
            erro = NaoContratado(_detalhe(resposta))
            self._guardar(chave, erro)
            raise erro
        if resposta.status_code != 200:
            raise NucleoIndisponivel(f"Núcleo respondeu {resposta.status_code}")

        corpo = resposta.json()
        self._guardar(chave, corpo)
        return corpo

    def _guardar(self, chave: str, valor: dict[str, Any] | NaoContratado) -> None:
        if not self._cache_seconds:
            return
        if len(self._cache) >= _LIMITE_CACHE:
            agora = time.monotonic()
            self._cache = {k: v for k, v in self._cache.items() if v[0] > agora}
            if len(self._cache) >= _LIMITE_CACHE:
                self._cache.clear()
        self._cache[chave] = (time.monotonic() + self._cache_seconds, valor)


def _detalhe(resposta: httpx.Response) -> str:
    try:
        return str(resposta.json().get("detail") or "")
    except ValueError:
        return ""
