"""
Cliente do FaturaSUS do núcleo, serviço a serviço, para a prevenção.

Chama a rota interna pela rede do compose com a chave INTERNAL_SERVICE_TOKEN.
Qualquer falha vira MotorIndisponivel: a carga do SIH não depende da prevenção,
que roda de novo pela tela de dados.
"""
from __future__ import annotations

from typing import Any

import httpx


class MotorIndisponivel(Exception):
    """Núcleo fora, chave errada ou resposta inesperada."""


class FaturaSusNucleo:
    ROTA = "/internal/fatursus/sih/avaliar"

    def __init__(self, base_url: str, token: str, *, timeout: float = 600,
                 transport: httpx.BaseTransport | None = None):
        self._cliente = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport,
                                     headers={"X-Internal-Token": token})

    def avaliar(self, aihs: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            resposta = self._cliente.post(self.ROTA, json={"aihs": aihs})
        except httpx.HTTPError as exc:
            raise MotorIndisponivel(f"O FaturaSUS não respondeu ({type(exc).__name__}).") from exc
        if resposta.status_code != 200:
            raise MotorIndisponivel(f"O FaturaSUS respondeu {resposta.status_code}: {resposta.text[:200]}")
        corpo = resposta.json()
        if not isinstance(corpo.get("resultados"), list):
            raise MotorIndisponivel("Resposta do FaturaSUS sem resultados.")
        return corpo

    def close(self) -> None:
        self._cliente.close()

    def __enter__(self) -> FaturaSusNucleo:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
