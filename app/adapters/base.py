"""
Contrato de uma fonte de dados.

As regras do Revenue Scan não conhecem o formato físico do arquivo: cada fonte
(SIH RD, RJ, ER, SP; SIA; CNES; SIGTAP; IBGE; Painel de Oncologia) tem um
adapter que sabe descobrir competências, obter o arquivo — por download do
DATASUS, envio manual ou API — e entregar linhas com nomes estáveis.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from typing import Any


class ClasseDado(str, Enum):
    """Público vem do DATASUS e serve a qualquer tenant; privado é do hospital e nunca sai do tenant."""

    PUBLICO = "PUBLICO"
    PRIVADO = "PRIVADO"


class OrigemArquivo(str, Enum):
    DOWNLOAD = "DOWNLOAD"
    UPLOAD = "UPLOAD"
    API = "API"


class DataSourceAdapter(ABC):
    #: Identificador da fonte, ex.: "SIH_RD".
    fonte: str
    classe: ClasseDado = ClasseDado.PUBLICO
    #: De onde veio o arquivo. Arquivo baixado é apagado depois da carga;
    #: arquivo de pasta local (enviado) fica onde estava.
    origem: OrigemArquivo = OrigemArquivo.DOWNLOAD

    @abstractmethod
    def competencias_disponiveis(self, uf: str) -> list[str]:
        """Competências AAAAMM publicadas para a UF, da mais recente para a mais antiga."""

    @abstractmethod
    def obter(self, uf: str, competencia: str, destino: Path) -> Path:
        """Arquivo da UF e competência em `destino`. Levanta erro se não publicado."""

    @abstractmethod
    def ler(self, caminho: Path) -> Iterator[dict[str, Any]]:
        """Linhas do arquivo com as colunas normalizadas pelo adapter."""
