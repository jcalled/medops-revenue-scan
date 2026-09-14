"""
CNES por UF: leitos (LT) e habilitações (HB) do FTP de disseminação.

Leito é o que diz o porte e a capacidade instalada; habilitação é o que o
hospital pode fazer de alta complexidade. Os dois entram no peer group e nas
oportunidades de capacidade e de habilitação.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from app.adapters import datasus
from app.adapters.base import DataSourceAdapter, OrigemArquivo
from app.adapters.ibge import validar_uf

DIRETORIO_CNES = "/dissemin/publicos/CNES/200508_/Dados"
TIPOS_CNES = ("LT", "HB")
_NOME = re.compile(r"^(LT|HB)([A-Z]{2})(\d{2})(\d{2})\.DBC$", re.IGNORECASE)


def _texto(valor: Any) -> str:
    return str(valor).strip() if valor is not None else ""


def _inteiro(valor: Any) -> int:
    try:
        return int(float(valor or 0))
    except (TypeError, ValueError):
        return 0


def normalizar_leito(linha: dict[str, Any]) -> dict[str, Any] | None:
    cnes, codigo = _texto(linha.get("CNES")), _texto(linha.get("CODLEITO"))
    if not cnes.strip("0") or not codigo:
        return None
    return {
        "cnes": cnes.zfill(7),
        "codigo_leito": codigo.zfill(2)[:2],
        "tipo_leito": _texto(linha.get("TP_LEITO"))[:1] or "0",
        "qt_existente": _inteiro(linha.get("QT_EXIST")),
        "qt_sus": _inteiro(linha.get("QT_SUS")),
    }


def normalizar_habilitacao(linha: dict[str, Any]) -> dict[str, Any] | None:
    cnes, habilitacao = _texto(linha.get("CNES")), _texto(linha.get("SGRUPHAB"))
    if not cnes.strip("0") or not habilitacao:
        return None
    return {
        "cnes": cnes.zfill(7),
        "habilitacao": habilitacao[:4],
        "competencia_inicio": _texto(linha.get("CMPT_INI"))[:6],
        "competencia_fim": _texto(linha.get("CMPT_FIM"))[:6] or None,
    }


class CnesArquivoAdapter(DataSourceAdapter):
    def __init__(
        self,
        tipo: str,
        *,
        pasta_local: Path | None = None,
        listar: Callable[[str], list[str]] = datasus.listar,
        baixar: Callable[..., Path] = datasus.baixar,
        ler: Callable[[Path], Iterator[dict[str, Any]]] = datasus.ler_dbc,
    ):
        self.tipo = tipo.upper()
        if self.tipo not in TIPOS_CNES:
            raise ValueError(f"Arquivo do CNES não suportado: {tipo}")
        self.fonte = f"CNES_{self.tipo}"
        self._pasta_local = Path(pasta_local) if pasta_local else None
        self.origem = OrigemArquivo.UPLOAD if self._pasta_local else OrigemArquivo.DOWNLOAD
        self._listar, self._baixar, self._ler = listar, baixar, ler

    @property
    def diretorio(self) -> str:
        return f"{DIRETORIO_CNES}/{self.tipo}"

    def nome_arquivo(self, uf: str, competencia: str) -> str:
        return f"{self.tipo}{validar_uf(uf)}{competencia[2:]}.dbc"

    def competencias_disponiveis(self, uf: str) -> list[str]:
        uf = validar_uf(uf)
        nomes = [p.name for p in self._pasta_local.iterdir()] if self._pasta_local else self._listar(self.diretorio)
        saida = set()
        for nome in nomes:
            m = _NOME.match(Path(nome).name)
            if m and m.group(1).upper() == self.tipo and m.group(2).upper() == uf:
                saida.add(f"20{m.group(3)}{m.group(4)}")
        return sorted(saida, reverse=True)

    def obter(self, uf: str, competencia: str, destino: Path) -> Path:
        nome = self.nome_arquivo(uf, competencia)
        if self._pasta_local:
            return datasus.achar_na_pasta(self._pasta_local, nome)
        return self._baixar(self.diretorio, nome, Path(destino))

    def ler(self, caminho: Path) -> Iterator[dict[str, Any]]:
        normalizar = normalizar_leito if self.tipo == "LT" else normalizar_habilitacao
        for linha in self._ler(caminho):
            normalizada = normalizar(linha)
            if normalizada:
                yield normalizada
