"""Fila própria do Revenue Scan no Redis da plataforma."""
from __future__ import annotations

from redis import Redis
from rq import Queue, Worker

from app.config import get_settings


def fila() -> Queue:
    settings = get_settings()
    return Queue(settings.rq_queue, connection=Redis.from_url(settings.redis_url))


def enfileirar_carga_uf(uf: str, competencias: list[str] | None = None, quantidade: int = 3) -> str:
    # Uma UF grande leva mais de uma hora entre download e gravação.
    job = fila().enqueue("app.jobs.carga_sih.job_carregar_uf", uf, competencias, quantidade, job_timeout="4h")
    return job.id


def main() -> None:
    settings = get_settings()
    Worker([Queue(settings.rq_queue, connection=Redis.from_url(settings.redis_url))]).work()


if __name__ == "__main__":
    main()
