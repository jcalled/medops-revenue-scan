"""
Agendador do Revenue Scan: uma vez por dia, confere no FTP do DATASUS se saiu mês
novo nas UFs das organizações com alerta ligado e enfileira só o que falta. A
carga termina gerando os alertas — é assim que o alerta mensal sai sozinho.

Como o agendador do núcleo: roda dentro da stack, e a trava diária no Redis faz
a verificação acontecer uma vez por dia, qualquer que seja o número de processos.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

HORA_LOCAL = 7            # depois das 7h de Brasília, quando o FTP do dia já foi atualizado
FUSO = timezone(timedelta(hours=-3))
MESES = 6


def verificar(db: Session, *, adapters: Any = None, enfileirar: Any = None, ocupadas: set[str] | None = None) -> list[dict[str, Any]]:
    """Para cada UF com alerta ligado, os meses publicados que faltam carregar; enfileira a carga deles."""
    from app.domain.alertas import ufs_das_assinaturas
    from app.jobs import carga_sih
    from app.jobs import fila as fila_jobs
    from app.models import SihHospitalMonth

    adapters = adapters or carga_sih.adapters_padrao()
    enfileirar = enfileirar or fila_jobs.enfileirar_carga_uf
    if ocupadas is None:
        ocupadas = {uf for t in fila_jobs.trabalhos(limite=200)
                    if t["tipo"] == "CARGA" and t["status"] in fila_jobs.EM_ANDAMENTO for uf in t["ufs"]}
    resultado = []
    for uf in sorted(ufs_das_assinaturas(db)):
        try:
            recentes = carga_sih.competencias_para_carga(adapters, uf, MESES)
        except Exception as exc:  # noqa: BLE001 — FTP fora: tenta amanhã
            resultado.append({"uf": uf, "situacao": "FTP_FORA", "detalhe": type(exc).__name__})
            continue
        carregadas = set(db.execute(select(SihHospitalMonth.competencia).where(SihHospitalMonth.uf == uf).distinct()).scalars())
        faltando = [c for c in recentes if c not in carregadas]
        if not faltando:
            resultado.append({"uf": uf, "situacao": "EM_DIA"})
        elif uf in ocupadas:
            resultado.append({"uf": uf, "situacao": "JA_NA_FILA", "faltando": faltando})
        else:
            resultado.append({"uf": uf, "situacao": "ENFILEIRADA", "faltando": faltando,
                              "trabalho": enfileirar(uf, faltando, len(faltando))})
    return resultado


def main() -> None:
    from sqlalchemy.orm import sessionmaker

    from app.db import engine
    from app.jobs.fila import fila

    Sessao = sessionmaker(bind=engine(), expire_on_commit=False)
    redis = fila().connection
    print({"step": "agenda_revenue_scan_start", "hora_local": HORA_LOCAL}, flush=True)
    while True:
        agora = datetime.now(FUSO)
        try:
            chave = f"revenue-scan:agenda:{agora.date().isoformat()}"
            if agora.hour >= HORA_LOCAL and redis.set(chave, agora.isoformat(), nx=True, ex=36 * 3600):
                try:
                    with Sessao() as db:
                        print({"step": "agenda_verificou", "ufs": verificar(db)}, flush=True)
                except Exception:
                    redis.delete(chave)  # sem a trava, tenta de novo no próximo ciclo
                    raise
        except Exception as exc:  # noqa: BLE001 — o agendador não pode morrer
            print({"step": "agenda_erro", "erro": f"{type(exc).__name__}: {exc}"}, flush=True)
        time.sleep(300)


if __name__ == "__main__":
    main()
