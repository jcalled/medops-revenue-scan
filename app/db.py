"""
Conexão com o Postgres, no schema próprio do Revenue Scan.

O banco pode ser o mesmo do núcleo, mas as tabelas ficam em `DB_SCHEMA`: o
Revenue Scan não lê nem escreve tabela do GlosaAI. O engine só é criado quando
alguém pede, para os testes e o /health não dependerem de banco.

O schema entra na compilação de cada query (`schema_translate_map`), não num
`SET search_path` da conexão: atrás de um pool PgBouncer em modo transação (o
connection pool da DigitalOcean) a conexão do servidor muda a cada transação e
o `SET` se perderia. Assim o serviço funciona pela porta direta ou pelo pool.
"""
from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings


def criar_engine(settings: Settings) -> Engine:
    if settings.database_url.startswith("sqlite"):
        return create_engine(settings.database_url, pool_pre_ping=True)
    eng = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        # Poucas conexões por processo: o Postgres gerenciado tem limite e o GlosaAI divide o mesmo.
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=30,
        # Conexão parada é fechada pelo Postgres gerenciado; recicla antes disso.
        pool_recycle=600,
        pool_use_lifo=True,
    )
    return eng.execution_options(schema_translate_map={None: settings.db_schema})


@lru_cache(maxsize=1)
def engine() -> Engine:
    return criar_engine(get_settings())


def get_db() -> Iterator[Session]:
    sessao = sessionmaker(bind=engine(), expire_on_commit=False)()
    try:
        yield sessao
    finally:
        sessao.close()
