"""Alertas mensais por organização: inscrição e histórico de envios

Revision ID: 0014_alertas_mensais
Revises: 0013_sia_apac
Create Date: 2026-09-21
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_alertas_mensais"
down_revision = "0013_sia_apac"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "alert_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("management_organizations.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("emails", _JSON, nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("incluir_honorarios", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("percentual", sa.Numeric(5, 2), nullable=False, server_default="15"),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "alert_issues",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("management_organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("competencia", sa.String(6), nullable=False),
        sa.Column("referencia", sa.String(6), nullable=False),
        sa.Column("conteudo", _JSON, nullable=False),
        sa.Column("destinatarios", _JSON, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("erro", sa.Text()),
        sa.Column("gerado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "competencia", name="uq_alert_issues"),
    )
    op.create_index("ix_alert_issues_organization_id", "alert_issues", ["organization_id"])


def downgrade() -> None:
    op.drop_table("alert_issues")
    op.drop_table("alert_subscriptions")
