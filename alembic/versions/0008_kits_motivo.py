"""Kits por motivo de rejeição e situação do trabalho por AIH

Revision ID: 0008_kits_motivo
Revises: 0007_prevencao
Create Date: 2026-09-15
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_kits_motivo"
down_revision = "0007_prevencao"
branch_labels = None
depends_on = None

_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_AGORA = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    kits = op.create_table(
        "motive_kits",
        sa.Column("codigo", sa.String(6), primary_key=True),
        sa.Column("titulo", sa.String(160), nullable=False),
        sa.Column("significado", sa.Text(), nullable=False),
        sa.Column("classe", sa.String(16), nullable=False),
        sa.Column("onde_corrigir", sa.String(12), nullable=False),
        sa.Column("passos", _JSON, nullable=False),
        sa.Column("dados_do_hospital", _JSON, nullable=False),
        sa.Column("evidencias", _JSON, nullable=False),
        sa.Column("prevencao", sa.Text()),
        sa.Column("fonte", sa.Text()),
        sa.Column("revisao", sa.String(12), nullable=False, server_default="A_CONFIRMAR"),
        sa.Column("atualizado_por", sa.Integer()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False, server_default=_AGORA),
    )

    op.create_table(
        "aih_treatments",
        sa.Column("n_aih", sa.String(13), primary_key=True),
        sa.Column("cnes", sa.String(7), nullable=False),
        sa.Column("situacao", sa.String(16), nullable=False),
        sa.Column("competencia_reapresentacao", sa.String(6)),
        sa.Column("justificativa", sa.Text()),
        sa.Column("responsavel", sa.String(120)),
        sa.Column("tenant_id", sa.Integer()),
        sa.Column("atualizado_por", sa.Integer()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False, server_default=_AGORA),
    )
    op.create_index("ix_aih_treatments_cnes", "aih_treatments", ["cnes"])

    op.create_table(
        "aih_treatment_events",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("n_aih", sa.String(13), nullable=False),
        sa.Column("situacao", sa.String(16), nullable=False),
        sa.Column("competencia_reapresentacao", sa.String(6)),
        sa.Column("justificativa", sa.Text()),
        sa.Column("responsavel", sa.String(120)),
        sa.Column("tenant_id", sa.Integer()),
        sa.Column("criado_por", sa.Integer()),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=_AGORA),
    )
    op.create_index("ix_aih_treatment_events_n_aih", "aih_treatment_events", ["n_aih"])

    # Conteúdo inicial, todo A_CONFIRMAR. Depois disto os kits se editam pela tela.
    from app.seed.kits_motivo import KITS

    op.bulk_insert(kits, [{"evidencias": [], "prevencao": None, **k, "revisao": "A_CONFIRMAR"} for k in KITS])


def downgrade() -> None:
    op.drop_index("ix_aih_treatment_events_n_aih", table_name="aih_treatment_events")
    op.drop_table("aih_treatment_events")
    op.drop_index("ix_aih_treatments_cnes", table_name="aih_treatments")
    op.drop_table("aih_treatments")
    op.drop_table("motive_kits")
