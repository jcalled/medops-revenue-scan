"""Valor aprovado no RD e acompanhamento da recuperação (base da cobrança do híbrido)

Revision ID: 0005_recuperacao
Revises: 0004_recortes
Create Date: 2026-09-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_recuperacao"
down_revision = "0004_recortes"
branch_labels = None
depends_on = None

_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_AGORA = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    # Cargas anteriores ficam sem valor: recarregar o mês preenche.
    with op.batch_alter_table("sih_approved_aih") as tabela:
        tabela.add_column(sa.Column("valor", sa.Numeric(14, 2)))

    op.create_table(
        "recovery_trackings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer()),
        sa.Column("nome", sa.String(120), nullable=False),
        sa.Column("organization_id", sa.Integer(),
                  sa.ForeignKey("management_organizations.id", ondelete="SET NULL")),
        sa.Column("cnes", _JSON, nullable=False),
        sa.Column("inicio", sa.String(6), nullable=False),
        sa.Column("percentual", sa.Numeric(5, 2), nullable=False, server_default="15"),
        sa.Column("fixo_por_hospital", sa.Numeric(12, 2), nullable=False, server_default="6900"),
        sa.Column("status", sa.String(12), nullable=False, server_default="ATIVO"),
        sa.Column("criado_por", sa.Integer()),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=_AGORA),
        sa.Column("conferido_em", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_recovery_trackings_tenant_id", "recovery_trackings", ["tenant_id"])

    op.create_table(
        "recovery_items",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("tracking_id", sa.Integer(), sa.ForeignKey("recovery_trackings.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("cnes", sa.String(7), nullable=False),
        sa.Column("uf", sa.String(2), nullable=False),
        sa.Column("n_aih", sa.String(13), nullable=False),
        sa.Column("origem", sa.String(8), nullable=False),
        sa.Column("competencia_rejeicao", sa.String(6), nullable=False),
        sa.Column("competencia_aih", sa.String(6)),
        sa.Column("procedimento", sa.String(10)),
        sa.Column("dt_saida", sa.Date()),
        sa.Column("valor_rejeitado", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("categoria", sa.String(30), nullable=False),
        sa.Column("motivos", _JSON, nullable=False),
        sa.Column("situacao", sa.String(12), nullable=False, server_default="EM_ABERTO"),
        sa.Column("competencia_aprovacao", sa.String(6)),
        sa.Column("valor_aprovado", sa.Numeric(14, 2)),
        sa.Column("marcado_em", sa.DateTime(timezone=True), nullable=False, server_default=_AGORA),
        sa.Column("recuperado_em", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tracking_id", "n_aih", name="uq_recovery_items"),
    )
    op.create_index("ix_recovery_items_tracking_id", "recovery_items", ["tracking_id"])
    op.create_index("ix_recovery_items_tracking_situacao", "recovery_items", ["tracking_id", "situacao"])


def downgrade() -> None:
    op.drop_table("recovery_items")
    op.drop_table("recovery_trackings")
    with op.batch_alter_table("sih_approved_aih") as tabela:
        tabela.drop_column("valor")
