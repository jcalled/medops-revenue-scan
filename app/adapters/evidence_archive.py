"""Preserva o arquivo usado na análise, mesmo após republicação do DATASUS."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

from app.adapters.datasus import sha256


def caminho_preservado(checksum: str | None) -> Path | None:
    if not checksum or not re.fullmatch(r"[a-f0-9]{64}", checksum):
        return None
    return Path(os.getenv("EVIDENCE_ARCHIVE_DIR", "data/evidence")) / checksum[:2] / checksum


def preservar(origem: Path, checksum: str) -> Path:
    destino = caminho_preservado(checksum)
    if destino is None:
        raise ValueError("SHA-256 inválido para preservação da evidência")
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists() and sha256(destino) == checksum:
        return destino
    # Copiar antes da limpeza do download; renomeação atômica evita arquivo parcial.
    with tempfile.NamedTemporaryFile(dir=destino.parent, delete=False) as temporario:
        temp = Path(temporario.name)
    try:
        shutil.copyfile(origem, temp)
        if sha256(temp) != checksum:
            raise ValueError("Arquivo mudou durante a preservação da evidência")
        temp.replace(destino)
    finally:
        temp.unlink(missing_ok=True)
    return destino
