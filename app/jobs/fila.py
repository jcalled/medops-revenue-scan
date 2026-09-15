"""Fila própria do Revenue Scan no Redis da plataforma."""
from __future__ import annotations

import re
from typing import Any

from redis import Redis
from rq import Queue, Worker
from rq.job import Job
from rq.registry import FailedJobRegistry, FinishedJobRegistry, StartedJobRegistry

from app.config import get_settings

# Resultado e falha ficam uma semana: é o histórico que a tela de dados mostra.
GUARDAR_POR = 7 * 24 * 3600

EM_ANDAMENTO = {"NA_FILA", "RODANDO"}
_STATUS = {
    "queued": "NA_FILA", "deferred": "NA_FILA", "scheduled": "NA_FILA",
    "started": "RODANDO",
    "finished": "CONCLUIDA",
    "failed": "FALHOU", "stopped": "FALHOU", "canceled": "FALHOU",
}


def fila() -> Queue:
    settings = get_settings()
    return Queue(settings.rq_queue, connection=Redis.from_url(settings.redis_url, socket_connect_timeout=3))


def enfileirar_carga_uf(uf: str, competencias: list[str] | None = None, quantidade: int = 3) -> str:
    # Uma UF grande leva mais de uma hora entre download e gravação.
    job = fila().enqueue(
        "app.jobs.carga_sih.job_carregar_uf", uf, competencias, quantidade,
        job_timeout="4h", result_ttl=GUARDAR_POR, failure_ttl=GUARDAR_POR,
        description=f"Carga do DATASUS · {uf}",
        meta={"tipo": "CARGA", "ufs": [uf], "competencias": competencias, "quantidade": quantidade},
    )
    return job.id


def enfileirar_recalculo(ufs: list[str] | None = None) -> str:
    job = fila().enqueue(
        "app.jobs.recalcular.job_recalcular", ufs,
        job_timeout="1h", result_ttl=GUARDAR_POR, failure_ttl=GUARDAR_POR,
        description=f"Recalcular scan · {', '.join(ufs) if ufs else 'todas as UFs'}",
        meta={"tipo": "RECALCULO", "ufs": ufs or []},
    )
    return job.id


def enfileirar_prevencao(uf: str) -> str:
    job = fila().enqueue(
        "app.jobs.prevencao.job_prevencao_uf", uf, None,
        job_timeout="2h", result_ttl=GUARDAR_POR, failure_ttl=GUARDAR_POR,
        description=f"Prevenção FaturaSUS · {uf}",
        meta={"tipo": "PREVENCAO", "ufs": [uf]},
    )
    return job.id


def _iso(valor: Any) -> str | None:
    return valor.isoformat() if valor else None


_LINHA_DE_EXCECAO = re.compile(r"^[A-Za-z_][\w.]*(Error|Exception|Indisponivel|Timeout)\b.*")


def _mensagem_de_erro(traceback: str | None) -> str:
    """A linha da exceção no traceback — a última linha costuma ser só um link de documentação."""
    linhas = [l.strip() for l in (traceback or "").strip().splitlines() if l.strip()]
    if not linhas:
        return "Falhou sem mensagem (tempo esgotado ou worker reiniciado)."
    return next((l for l in reversed(linhas) if _LINHA_DE_EXCECAO.match(l)), linhas[-1])[:500]


def trabalhos(limite: int = 30) -> list[dict[str, Any]]:
    """Trabalhos na fila, rodando e terminados na última semana, do mais novo ao mais antigo."""
    q = fila()
    ids = list(q.job_ids)
    for registro in (StartedJobRegistry, FinishedJobRegistry, FailedJobRegistry):
        ids += registro(queue=q).get_job_ids()
    jobs = [j for j in Job.fetch_many(list(dict.fromkeys(ids)), connection=q.connection) if j is not None]
    jobs.sort(key=lambda j: str(j.enqueued_at or j.created_at or ""), reverse=True)

    saida = []
    for j in jobs[:limite]:
        status = j.get_status(refresh=False)
        status = _STATUS.get(getattr(status, "value", status), str(status).upper())
        erro = None
        if status == "FALHOU":
            erro = _mensagem_de_erro(getattr(j, "exc_info", None))
        meta = j.meta or {}
        saida.append({
            "id": j.id,
            "descricao": j.description,
            "tipo": meta.get("tipo"),
            "ufs": meta.get("ufs") or [],
            "competencias": meta.get("competencias"),
            "quantidade": meta.get("quantidade"),
            "status": status,
            "enfileirado_em": _iso(j.enqueued_at),
            "iniciado_em": _iso(j.started_at),
            "terminado_em": _iso(j.ended_at),
            "erro": erro,
        })
    return saida


def main() -> None:
    Worker([fila()]).work()


if __name__ == "__main__":
    main()
