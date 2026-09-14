"""Atributos do hospital no score (para filtrar) e municípios do IBGE

Revision ID: 0004_recortes
Revises: 0003_benchmark
Create Date: 2026-09-14
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_recortes"
down_revision = "0003_benchmark"
branch_labels = None
depends_on = None

_COLUNAS = (
    sa.Column("impacto_confirmado", sa.Numeric(16, 2), nullable=False, server_default="0"),
    sa.Column("impacto_sinais", sa.Numeric(16, 2), nullable=False, server_default="0"),
    sa.Column("uf", sa.String(2)),
    sa.Column("codigo_municipio", sa.String(7)),
    sa.Column("natureza_codigo", sa.String(4)),
    sa.Column("natureza_grupo", sa.String(20)),
    sa.Column("gestao", sa.String(20)),
    sa.Column("porte", sa.String(30)),
    sa.Column("leitos_sus", sa.Integer()),
)


def upgrade() -> None:
    with op.batch_alter_table("hospital_scores") as tabela:
        for coluna in _COLUNAS:
            tabela.add_column(coluna.copy())
    op.create_index("ix_hospital_scores_uf_natureza", "hospital_scores", ["uf", "natureza_grupo"])

    op.create_table(
        "municipalities",
        sa.Column("codigo", sa.String(7), primary_key=True),
        sa.Column("codigo_cnes", sa.String(6), nullable=False, unique=True),
        sa.Column("nome", sa.String(120), nullable=False),
        sa.Column("uf", sa.String(2), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("municipalities")
    op.drop_index("ix_hospital_scores_uf_natureza", table_name="hospital_scores")
    with op.batch_alter_table("hospital_scores") as tabela:
        for coluna in reversed(_COLUNAS):
            tabela.drop_column(coluna.name)
