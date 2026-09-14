"""
Cadastro do estabelecimento pela API de dados abertos do Ministério da Saúde.

É de onde vem o nome que o hospital usa ("HOSPITAL REGIONAL DO CARIRI"): nos
arquivos do SIH só há o número do CNES, e hospital estadual gerido por OSS tem
como razão social a secretaria de saúde.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from app.adapters.ibge import UF_POR_CODIGO

BASE_URL = "https://apidadosabertos.saude.gov.br"


def normalizar(dados: dict[str, Any]) -> dict[str, Any]:
    def texto(chave: str, limite: int) -> str | None:
        valor = str(dados.get(chave) or "").strip()
        return valor[:limite] or None

    return {
        "cnes": str(dados["codigo_cnes"]).zfill(7),
        "nome_fantasia": texto("nome_fantasia", 255),
        "razao_social": texto("nome_razao_social", 255),
        "uf": UF_POR_CODIGO.get(str(dados.get("codigo_uf") or "")),
        "codigo_municipio": texto("codigo_municipio", 7),
        "cnpj_entidade": texto("numero_cnpj_entidade", 14) or texto("numero_cnpj", 14),
        "natureza_juridica": texto("descricao_natureza_juridica_estabelecimento", 4),
        "esfera": texto("descricao_esfera_administrativa", 20),
        "tipo_unidade": dados.get("codigo_tipo_unidade"),
    }


class CnesDadosAbertos:
    def __init__(self, base_url: str = BASE_URL, *, transport: httpx.BaseTransport | None = None,
                 timeout: float = 20.0, tentativas: int = 3, espera: Callable[[float], None] = time.sleep):
        self._http = httpx.Client(base_url=base_url, transport=transport, timeout=timeout)
        self._tentativas = max(1, tentativas)
        self._espera = espera

    def __enter__(self) -> CnesDadosAbertos:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def estabelecimento(self, cnes: str) -> dict[str, Any] | None:
        """Cadastro normalizado, ou None se o CNES não existe na base."""
        for tentativa in range(1, self._tentativas + 1):
            resposta = self._http.get(f"/cnes/estabelecimentos/{str(cnes).zfill(7)}")
            if resposta.status_code == 404:
                return None
            if resposta.status_code in (429, 500, 502, 503, 504) and tentativa < self._tentativas:
                self._espera(2.0 * tentativa)
                continue
            resposta.raise_for_status()
            dados = resposta.json()
            return normalizar(dados) if dados and dados.get("codigo_cnes") else None
        return None
