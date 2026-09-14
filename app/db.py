"""
Conexão com o Postgres, no schema próprio do Revenue Scan.

O banco pode ser o mesmo do núcleo, mas as tabelas ficam em `DB_SCHEMA`: o
Revenue Scan não lê nem escreve tabela do GlosaAI. O engine só é criado quando
alguém pede, para os testes e o /health não dependerem de banco.
"""
from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


@lru_cache(maxsize=1)
def engine() -> Engine:
    settings = get_settings()
    eng = create_engine(settings.database_url, pool_pre_ping=True)
    if eng.dialect.name == "postgresql":
        @event.listens_for(eng, "connect")
        def _schema(conexao, _registro):  # noqa: ANN001
            with conexao.cursor() as cursor:
                cursor.execute(f'SET search_path TO "{settings.db_schema}", public')
    return eng


def get_db() -> Iterator[Session]:
    sessao = sessionmaker(bind=engine(), expire_on_commit=False)()
    try:
        yield sessao
    finally:
        sessao.close()
