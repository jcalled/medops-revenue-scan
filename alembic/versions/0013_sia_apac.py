"""APAC do SIA por hospital e processamento (arquivo PA agregado)

Revision ID: 0013_sia_apac
Revises: 0012_arquivos_do_contrato
Create Date: 2026-09-21
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_sia_apac"
down_revision = "0012_arquivos_do_contrato"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "sia_apac_month",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("uf", sa.String(2), nullable=False),
        sa.Column("competencia", sa.String(6), nullable=False),
        sa.Column("cnes", sa.String(7), nullable=False),
        sa.Column("linhas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valor_produzido", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("valor_aprovado", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("valor_nao_aprovado", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("valor_teto", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("ocorrencias", _JSON, nullable=False),
        sa.Column("procedimentos", _JSON, nullable=False),
        sa.UniqueConstraint("uf", "competencia", "cnes", name="uq_sia_apac_month"),
    )
    op.create_index("ix_sia_apac_month_cnes", "sia_apac_month", ["cnes", "competencia"])


def downgrade() -> None:
    op.drop_table("sia_apac_month")
