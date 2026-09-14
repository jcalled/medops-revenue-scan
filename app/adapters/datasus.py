"""
FTP de disseminação do DATASUS e leitura de .dbc.

O download grava em `.parcial` e só renomeia no fim: uma queda de conexão não
pode deixar um arquivo truncado com cara de arquivo bom. O .dbc é descompactado
numa pasta temporária que some ao fim da leitura — os arquivos de UF grande
passam de centenas de MB descompactados.
"""
from __future__ import annotations

import ftplib
import hashlib
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

FTP_HOST = "ftp.datasus.gov.br"


class ArquivoIndisponivel(Exception):
    """Arquivo não publicado, vazio ou fora da pasta informada."""


def listar(diretorio: str, *, host: str = FTP_HOST, timeout: int = 60) -> list[str]:
    with ftplib.FTP(host, timeout=timeout) as ftp:
        ftp.login()
        ftp.cwd(diretorio)
        return ftp.nlst()


def baixar(diretorio: str, nome: str, destino_dir: Path, *, host: str = FTP_HOST,
           timeout: int = 180, minimo_bytes: int = 100) -> Path:
    destino_dir = Path(destino_dir)
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / nome
    parcial = destino.with_name(destino.name + ".parcial")
    try:
        with ftplib.FTP(host, timeout=timeout) as ftp, open(parcial, "wb") as arquivo:
            ftp.login()
            ftp.cwd(diretorio)
            ftp.retrbinary(f"RETR {nome}", arquivo.write)
        if parcial.stat().st_size < minimo_bytes:
            raise ArquivoIndisponivel(f"{nome}: arquivo vazio ou truncado")
        parcial.replace(destino)
        return destino
    except ftplib.error_perm as exc:
        raise ArquivoIndisponivel(f"{nome}: {exc}") from exc
    finally:
        parcial.unlink(missing_ok=True)


def ler_dbc(caminho: Path) -> Iterator[dict[str, Any]]:
    """Linhas de um .dbc (ou .dbf) do DATASUS."""
    from dbfread import DBF

    caminho = Path(caminho)
    if caminho.suffix.lower() == ".dbf":
        for linha in DBF(str(caminho), encoding="latin-1", char_decode_errors="replace"):
            yield dict(linha)
        return

    from pyreaddbc import dbc2dbf

    with tempfile.TemporaryDirectory() as pasta:
        dbf = Path(pasta) / f"{caminho.stem}.dbf"
        dbc2dbf(str(caminho), str(dbf))
        for linha in DBF(str(dbf), encoding="latin-1", char_decode_errors="replace"):
            yield dict(linha)


def sha256(caminho: Path) -> str:
    resumo = hashlib.sha256()
    with open(caminho, "rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1 << 20), b""):
            resumo.update(bloco)
    return resumo.hexdigest()


def achar_na_pasta(pasta: Path, nome: str) -> Path:
    """Arquivo pelo nome, sem diferenciar maiúsculas: o FTP publica RDCE2607.dbc."""
    achado = next((p for p in Path(pasta).iterdir() if p.name.lower() == nome.lower()), None)
    if achado is None:
        raise ArquivoIndisponivel(f"{nome} não está em {pasta}")
    return achado
