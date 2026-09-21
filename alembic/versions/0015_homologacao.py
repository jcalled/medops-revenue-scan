"""Homologação: lotes de AIH rejeitadas para o faturamento conferir o que o sistema diz

Revision ID: 0015_homologacao
Revises: 0014_alertas_mensais
Create Date: 2026-09-21
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015_homologacao"
down_revision = "0014_alertas_mensais"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "homologation_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("titulo", sa.String(200), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("management_organizations.id", ondelete="SET NULL")),
        sa.Column("cnes", _JSON, nullable=False),
        sa.Column("competencia", sa.String(6), nullable=False),
        sa.Column("criado_por", sa.Integer()),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_homologation_batches_organization_id", "homologation_batches", ["organization_id"])
    op.create_table(
        "homologation_items",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("homologation_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("n_aih", sa.String(13), nullable=False),
        sa.Column("cnes", sa.String(7), nullable=False),
        sa.Column("valor", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("motivos", _JSON, nullable=False),
        sa.Column("origem", sa.String(10), nullable=False),
        sa.Column("regras", _JSON, nullable=False),
        sa.Column("o_que_diz", sa.Text(), nullable=False, server_default=""),
        sa.Column("correcao", sa.Text(), nullable=False, server_default=""),
        sa.Column("veredito", sa.String(10)),
        sa.Column("comentario", sa.Text()),
        sa.Column("respondido_por", sa.String(120)),
        sa.Column("respondido_em", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("batch_id", "n_aih", name="uq_homologation_items"),
    )
    op.create_index("ix_homologation_items_batch_id", "homologation_items", ["batch_id"])


def downgrade() -> None:
    op.drop_table("homologation_items")
    op.drop_table("homologation_batches")
