"""
SIH/SUS: AIH aprovadas (RD), rejeitadas (RJ) e motivos de rejeição (ER).

Um arquivo por UF e mês de processamento, no FTP de disseminação. As colunas
saem daqui com nomes estáveis; o resto do serviço não conhece o layout do DBF.

O DATASUS zera diárias, permanência e valores parciais nas rejeitadas — só
VAL_TOT, datas e procedimento vêm preenchidos. Por isso a rejeição guarda só
o que é publicado igual nas duas.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

from app.adapters import datasus
from app.adapters.base import DataSourceAdapter, OrigemArquivo
from app.adapters.ibge import validar_uf

DIRETORIO_SIH = "/dissemin/publicos/SIHSUS/200801_/Dados"
TIPOS = ("RD", "RJ", "ER")
_NOME = re.compile(r"^(RD|RJ|ER|SP)([A-Z]{2})(\d{2})(\d{2})\.DBC$", re.IGNORECASE)


def competencias_da_listagem(nomes: list[str], tipo: str, uf: str) -> list[str]:
    """Competências AAAAMM de um tipo e UF numa listagem de arquivos, da mais recente."""
    saida = set()
    for nome in nomes:
        m = _NOME.match(Path(nome).name)
        if m and m.group(1).upper() == tipo and m.group(2).upper() == uf:
            saida.add(f"20{m.group(3)}{m.group(4)}")
    return sorted(saida, reverse=True)


def _texto(valor: Any) -> str:
    return str(valor).strip() if valor is not None else ""


def _data(valor: Any) -> date | None:
    texto = _texto(valor)
    if len(texto) != 8 or not texto.isdigit():
        return None
    try:
        return date(int(texto[:4]), int(texto[4:6]), int(texto[6:]))
    except ValueError:
        return None


def _numero(valor: Any) -> float:
    try:
        return float(valor or 0)
    except (TypeError, ValueError):
        return 0.0


def _inteiro(valor: Any) -> int:
    return int(_numero(valor))


def normalizar_aih(linha: dict[str, Any]) -> dict[str, Any] | None:
    n_aih, cnes = _texto(linha.get("N_AIH")), _texto(linha.get("CNES"))
    if not n_aih or not cnes.strip("0"):
        return None
    mes = _texto(linha.get("MES_CMPT"))
    return {
        "n_aih": n_aih,
        "cnes": cnes.zfill(7),
        "competencia_aih": f"{_texto(linha.get('ANO_CMPT'))}{mes.zfill(2)}" if mes else None,
        "proc_realizado": _texto(linha.get("PROC_REA")) or None,
        "valor": round(_numero(linha.get("VAL_TOT")), 2),
        "dt_internacao": _data(linha.get("DT_INTER")),
        "dt_saida": _data(linha.get("DT_SAIDA")),
        "diarias": _inteiro(linha.get("QT_DIARIAS")),
        "diarias_uti": _inteiro(linha.get("UTI_MES_TO")),
        "permanencia": _inteiro(linha.get("DIAS_PERM")),
        "marca_uti": _texto(linha.get("MARCA_UTI"))[:2] or None,
    }


def normalizar_erro(linha: dict[str, Any]) -> dict[str, Any] | None:
    n_aih, codigo = _texto(linha.get("AIH")), _texto(linha.get("CO_ERRO"))
    if not n_aih or not codigo:
        return None
    mes = _texto(linha.get("MES"))
    return {
        "n_aih": n_aih,
        "cnes": _texto(linha.get("CNES")).zfill(7),
        "codigo_erro": codigo[:6],
        "competencia_aih": f"{_texto(linha.get('ANO'))}{mes.zfill(2)}" if mes else None,
    }


class SihAdapter(DataSourceAdapter):
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
        if self.tipo not in TIPOS:
            raise ValueError(f"Tipo de arquivo do SIH desconhecido: {tipo}")
        self.fonte = f"SIH_{self.tipo}"
        self._pasta_local = Path(pasta_local) if pasta_local else None
        self.origem = OrigemArquivo.UPLOAD if self._pasta_local else OrigemArquivo.DOWNLOAD
        self._listar, self._baixar, self._ler = listar, baixar, ler

    def nome_arquivo(self, uf: str, competencia: str) -> str:
        return f"{self.tipo}{validar_uf(uf)}{competencia[2:]}.dbc"

    def competencias_disponiveis(self, uf: str) -> list[str]:
        if self._pasta_local:
            nomes = [p.name for p in self._pasta_local.iterdir()]
        else:
            nomes = self._listar(DIRETORIO_SIH)
        return competencias_da_listagem(nomes, self.tipo, validar_uf(uf))

    def obter(self, uf: str, competencia: str, destino: Path) -> Path:
        nome = self.nome_arquivo(uf, competencia)
        if self._pasta_local:
            return datasus.achar_na_pasta(self._pasta_local, nome)
        return self._baixar(DIRETORIO_SIH, nome, Path(destino))

    def ler(self, caminho: Path) -> Iterator[dict[str, Any]]:
        normalizar = normalizar_erro if self.tipo == "ER" else normalizar_aih
        for linha in self._ler(caminho):
            normalizada = normalizar(linha)
            if normalizada:
                yield normalizada
