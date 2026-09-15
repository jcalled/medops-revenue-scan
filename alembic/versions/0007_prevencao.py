"""Prevenção: campos do RJ para o FaturaSUS, acumulado do lote e o resultado por AIH

Revision ID: 0007_prevencao
Revises: 0006_prospeccao
Create Date: 2026-09-15
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_prevencao"
down_revision = "0006_prospeccao"
branch_labels = None
depends_on = None

_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_AGORA = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    # Cargas anteriores ficam sem campos: recarregar o mês habilita a prevenção dele.
    with op.batch_alter_table("sih_rejections") as tabela:
        tabela.add_column(sa.Column("campos", _JSON))
        tabela.add_column(sa.Column("diarias_antes", sa.Integer(), nullable=False, server_default="0"))
        tabela.add_column(sa.Column("diarias_uti_antes", sa.Integer(), nullable=False, server_default="0"))

    op.create_table(
        "sih_prevention",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("uf", sa.String(2), nullable=False),
        sa.Column("competencia", sa.String(6), nullable=False),
        sa.Column("cnes", sa.String(7), nullable=False),
        sa.Column("n_aih", sa.String(13), nullable=False),
        sa.Column("grupo", sa.String(30), nullable=False),
        sa.Column("pegaria", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("motivos", _JSON, nullable=False),
        sa.Column("falhas", _JSON, nullable=False),
        sa.Column("avisos", _JSON, nullable=False),
        sa.Column("mensagens", _JSON, nullable=False),
        sa.Column("referencias", _JSON, nullable=False),
        sa.Column("avaliado_em", sa.DateTime(timezone=True), nullable=False, server_default=_AGORA),
        sa.UniqueConstraint("uf", "n_aih", "competencia", name="uq_sih_prevention"),
    )
    op.create_index("ix_sih_prevention_cnes_competencia", "sih_prevention", ["cnes", "competencia"])


def downgrade() -> None:
    op.drop_table("sih_prevention")
    with op.batch_alter_table("sih_rejections") as tabela:
        tabela.drop_column("diarias_uti_antes")
        tabela.drop_column("diarias_antes")
        tabela.drop_column("campos")
