"""schema do Revenue Scan

Revision ID: 0001_schema
Revises:
Create Date: 2026-09-14

Só o schema. As tabelas de fontes, cargas e fatos do SIH entram com a carga do
DATASUS, na próxima etapa.
"""
import sqlalchemy as sa
from alembic import op

from app.config import get_settings

revision = "0001_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{get_settings().db_schema}"'))


def downgrade() -> None:
    # Apagar o schema levaria junto qualquer dado carregado; é decisão manual.
    pass
