"""
Migrations do Revenue Scan, no schema próprio.

A tabela de versão também fica no schema (`revenue_scan_alembic`): assim o
Alembic do núcleo e o deste serviço podem apontar para o mesmo banco sem um
achar que as migrations do outro estão faltando.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, text

from app.config import get_settings

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

from app.models import Base  # noqa: E402

settings = get_settings()
TABELA_DE_VERSAO = "revenue_scan_alembic"
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url, target_metadata=target_metadata, literal_binds=True,
        version_table=TABELA_DE_VERSAO, version_table_schema=settings.db_schema,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(settings.database_url)
    with engine.connect() as conexao:
        postgres = conexao.dialect.name == "postgresql"
        if postgres:
            conexao.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{settings.db_schema}"'))
            conexao.commit()
            # As migrations criam tabelas sem schema no nome; o search_path as
            # põe no schema do serviço, e não no public do núcleo.
            conexao.execute(text(f'SET search_path TO "{settings.db_schema}"'))
            conexao.commit()
        context.configure(
            connection=conexao, target_metadata=target_metadata,
            version_table=TABELA_DE_VERSAO, version_table_schema=settings.db_schema if postgres else None,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
