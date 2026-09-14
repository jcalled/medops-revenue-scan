"""Linha de base da recuperação (desconto do que a OS já recupera) e CRM de prospecção

Revision ID: 0006_prospeccao
Revises: 0005_recuperacao
Create Date: 2026-09-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_prospeccao"
down_revision = "0005_recuperacao"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_AGORA = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    with op.batch_alter_table("recovery_trackings") as tabela:
        tabela.add_column(sa.Column("linha_de_base", _JSON, nullable=False, server_default="{}"))
        tabela.add_column(sa.Column("linha_de_base_meses", _JSON, nullable=False, server_default="[]"))
        tabela.add_column(sa.Column("linha_de_base_origem", sa.String(12), nullable=False, server_default="CALCULADA"))

    op.create_table(
        "prospects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(),
                  sa.ForeignKey("management_organizations.id", ondelete="SET NULL")),
        sa.Column("nome", sa.String(255), nullable=False, unique=True),
        sa.Column("uf", sa.String(2)),
        sa.Column("rank", sa.Integer()),
        sa.Column("score", sa.Integer()),
        sa.Column("prioridade", sa.String(4)),
        sa.Column("presenca", sa.String(255)),
        sa.Column("situacao_escopo", sa.String(255)),
        sa.Column("hospitais_confirmados", sa.Integer()),
        sa.Column("rede", sa.Text()),
        sa.Column("principais_unidades", sa.Text()),
        sa.Column("lideranca", sa.String(255)),
        sa.Column("contato_publico", sa.Text()),
        sa.Column("site", sa.String(255)),
        sa.Column("fonte", sa.String(500)),
        sa.Column("fit", sa.String(255)),
        sa.Column("confianca", sa.String(20)),
        sa.Column("etapa", sa.String(20), nullable=False, server_default="MAPEADA"),
        sa.Column("contato_nome", sa.String(120)),
        sa.Column("contato_cargo", sa.String(120)),
        sa.Column("contato_email", sa.String(255)),
        sa.Column("contato_telefone", sa.String(60)),
        sa.Column("responsavel", sa.String(120)),
        sa.Column("proxima_acao", sa.Text()),
        sa.Column("proxima_acao_em", sa.Date()),
        sa.Column("proposta_percentual", sa.Numeric(5, 2)),
        sa.Column("proposta_fixo", sa.Numeric(12, 2)),
        sa.Column("motivo_perda", sa.Text()),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=_AGORA),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False, server_default=_AGORA),
    )
    op.create_index("ix_prospects_organization_id", "prospects", ["organization_id"])

    op.create_table(
        "prospect_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("prospect_id", sa.Integer(), sa.ForeignKey("prospects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tipo", sa.String(12), nullable=False),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("criado_por", sa.Integer()),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=_AGORA),
    )
    op.create_index("ix_prospect_events_prospect_id", "prospect_events", ["prospect_id"])


def downgrade() -> None:
    op.drop_table("prospect_events")
    op.drop_table("prospects")
    with op.batch_alter_table("recovery_trackings") as tabela:
        tabela.drop_column("linha_de_base_origem")
        tabela.drop_column("linha_de_base_meses")
        tabela.drop_column("linha_de_base")
