"""
Carga da APAC do SIA (arquivo PA) por UF e competência, agregada por hospital.

Roda depois da carga do SIH da UF, nos mesmos meses. Falhar aqui não desfaz o
SIH: a APAC é um indicador à parte. Os arquivos somem ao fim de cada competência.
"""
from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from app.adapters import datasus, sia
from app.models import DataLoad, SiaApacMonth

logger = logging.getLogger(__name__)


def carregar_apac(db: Session, uf: str, competencias: list[str], *,
                  listar: Callable[[str], list[str]] = datasus.listar,
                  baixar: Callable[..., Path] = datasus.baixar,
                  ler: Callable[[Path], object] = datasus.ler_dbc) -> dict[str, int]:
    """Hospitais com APAC por competência carregada; competência sem PA publicado fica de fora."""
    nomes = listar(sia.PASTA)
    resultado: dict[str, int] = {}
    for competencia in sorted(set(competencias)):
        arquivos = sia.arquivos_da_competencia(nomes, uf, competencia)
        if not arquivos:
            logger.info("SIA PA %s %s ainda não publicado", uf, competencia)
            continue
        carga = DataLoad(fonte=sia.FONTE, uf=uf, competencia=competencia, origem="DOWNLOAD",
                         arquivo=",".join(arquivos), status="RUNNING")
        db.add(carga)
        db.commit()
        try:
            with tempfile.TemporaryDirectory() as pasta:
                def linhas():
                    for nome in arquivos:
                        caminho = baixar(sia.PASTA, nome, Path(pasta))
                        yield from ler(caminho)  # type: ignore[misc]
                        Path(caminho).unlink(missing_ok=True)
                por_cnes = sia.agregar(linhas())
            db.execute(delete(SiaApacMonth).where(SiaApacMonth.uf == uf, SiaApacMonth.competencia == competencia))
            if por_cnes:
                db.execute(insert(SiaApacMonth), [{"uf": uf, "competencia": competencia, "cnes": cnes, **dados}
                                                  for cnes, dados in por_cnes.items()])
            carga.status, carga.linhas = "OK", sum(d["linhas"] for d in por_cnes.values())
            resultado[competencia] = len(por_cnes)
        except Exception as exc:  # noqa: BLE001 — registra e segue para a próxima competência
            db.rollback()
            carga.status, carga.erro = "FAILED", str(exc)[:2000]
            logger.exception("SIA PA %s %s falhou", uf, competencia)
        carga.concluido_em = datetime.now(timezone.utc)
        db.commit()
    return resultado
