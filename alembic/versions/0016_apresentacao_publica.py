"""Apresentação por link público: retrato agregado, hash do token, validade e revogação

Revision ID: 0016_apresentacao_publica
Revises: 0015_homologacao
Create Date: 2026-09-21
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_apresentacao_publica"
down_revision = "0015_homologacao"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "public_presentations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("titulo", sa.String(255), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("management_organizations.id", ondelete="SET NULL")),
        sa.Column("cnes", _JSON, nullable=False),
        sa.Column("percentual", sa.Numeric(5, 2)),
        sa.Column("retrato", _JSON, nullable=False),
        sa.Column("criado_por", sa.Integer()),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revogado_em", sa.DateTime(timezone=True)),
        sa.Column("visualizacoes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ultima_visualizacao", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("public_presentations")
