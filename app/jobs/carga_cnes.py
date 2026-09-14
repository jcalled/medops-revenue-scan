"""
Carga do CNES por UF: leitos (LT) e habilitações (HB), da competência mais
recente publicada — ou da pedida. Recarregar substitui.

Uso:
    python -m app.jobs.carga_cnes --uf CE
    python -m app.jobs.carga_cnes --uf TODAS
    python -m app.jobs.carga_cnes --uf CE --competencia 202607 --pasta /dados/cnes
"""
from __future__ import annotations

import argparse
import logging
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.adapters import datasus
from app.adapters.base import DataSourceAdapter, OrigemArquivo
from app.adapters.cnes_arquivos import TIPOS_CNES, CnesArquivoAdapter
from app.adapters.ibge import validar_uf
from app.jobs.carga_sih import _agora, _inserir, ufs_do_argumento
from app.models import CnesBed, CnesEnablement, DataLoad

logger = logging.getLogger(__name__)


def _linhas_leitos(adapter: DataSourceAdapter, caminho: Path) -> list[dict[str, Any]]:
    # O arquivo pode repetir código e tipo de leito para o mesmo hospital.
    soma: dict[tuple[str, str, str], dict[str, int]] = defaultdict(lambda: {"qt_existente": 0, "qt_sus": 0})
    for linha in adapter.ler(caminho):
        chave = (linha["cnes"], linha["codigo_leito"], linha["tipo_leito"])
        soma[chave]["qt_existente"] += linha["qt_existente"]
        soma[chave]["qt_sus"] += linha["qt_sus"]
    return [{"cnes": c, "codigo_leito": codigo, "tipo_leito": tipo, **q} for (c, codigo, tipo), q in soma.items()]


def _linhas_habilitacoes(adapter: DataSourceAdapter, caminho: Path) -> list[dict[str, Any]]:
    unicas = {(l["cnes"], l["habilitacao"], l["competencia_inicio"]): l for l in adapter.ler(caminho)}
    return list(unicas.values())


def carregar_cnes_uf(db: Session, uf: str, *, competencia: str | None = None,
                     adapters: dict[str, DataSourceAdapter] | None = None,
                     pasta_local: Path | None = None) -> dict[str, tuple[str, int]]:
    uf = validar_uf(uf)
    adapters = adapters or {tipo: CnesArquivoAdapter(tipo, pasta_local=pasta_local) for tipo in TIPOS_CNES}
    resultado: dict[str, tuple[str, int]] = {}
    for tipo, modelo, montar in (("LT", CnesBed, _linhas_leitos), ("HB", CnesEnablement, _linhas_habilitacoes)):
        adapter = adapters[tipo]
        alvo = competencia or next(iter(adapter.competencias_disponiveis(uf)), None)
        if not alvo:
            raise datasus.ArquivoIndisponivel(f"CNES {tipo} de {uf} não publicado")
        carga = DataLoad(fonte=adapter.fonte, uf=uf, competencia=alvo, origem=adapter.origem.value)
        db.add(carga)
        db.commit()
        with tempfile.TemporaryDirectory() as pasta:
            caminho = None
            try:
                caminho = adapter.obter(uf, alvo, Path(pasta))
                carga.arquivo, carga.checksum = caminho.name, datasus.sha256(caminho)
                linhas = montar(adapter, caminho)
                db.execute(delete(modelo).where(modelo.uf == uf, modelo.competencia == alvo))
                _inserir(db, modelo, [{"uf": uf, "competencia": alvo, **l} for l in linhas])
                carga.linhas, carga.status, carga.concluido_em = len(linhas), "OK", _agora()
                db.commit()
            except Exception as exc:
                db.rollback()
                carga.status, carga.erro, carga.concluido_em = "FAILED", f"{exc.__class__.__name__}: {exc}"[:2000], _agora()
                db.commit()
                raise
            finally:
                if caminho is not None and adapter.origem == OrigemArquivo.DOWNLOAD:
                    caminho.unlink(missing_ok=True)
        resultado[tipo] = (alvo, carga.linhas)
        logger.info("CNES %s %s %s: %d linhas", tipo, uf, alvo, carga.linhas)
    return resultado


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Carga do CNES (leitos e habilitações) por UF.")
    parser.add_argument("--uf", required=True, help="UFs separadas por vírgula ou TODAS")
    parser.add_argument("--competencia", help="AAAAMM; sem isto, a mais recente publicada")
    parser.add_argument("--pasta", type=Path, help="Pasta com LT*.dbc e HB*.dbc já baixados")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from sqlalchemy.orm import sessionmaker

    from app.db import engine

    falhas = []
    with sessionmaker(bind=engine(), expire_on_commit=False)() as db:
        for uf in ufs_do_argumento(args.uf):
            try:
                for tipo, (competencia, linhas) in carregar_cnes_uf(
                        db, uf, competencia=args.competencia, pasta_local=args.pasta).items():
                    print(f"{uf} {tipo} {competencia}: {linhas} linhas")
            except Exception as exc:  # noqa: BLE001
                logger.exception("CNES de %s falhou", uf)
                falhas.append(f"{uf}: {exc}")
    if falhas:
        print("UFs com falha:\n  " + "\n  ".join(falhas))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
