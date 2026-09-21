"""Campos públicos do RD para demonstrar mudanças entre processamentos.

Revision ID: 0010_evidencia_rd
Revises: 0009_faturasus_motivo
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_evidencia_rd"
down_revision = "0009_faturasus_motivo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sih_approved_aih", sa.Column("campos_publicos", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True))


def downgrade() -> None:
    op.drop_column("sih_approved_aih", "campos_publicos")
