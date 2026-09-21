"""Homologação: situação de recuperação, prazo e tipo de correção de cada AIH do lote

Revision ID: 0017_homologacao_situacao
Revises: 0016_apresentacao_publica
Create Date: 2026-09-21

Lotes já montados ficam com as colunas vazias e são completados ao abrir.
"""
import sqlalchemy as sa
from alembic import op

revision = "0017_homologacao_situacao"
down_revision = "0016_apresentacao_publica"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("homologation_items", sa.Column("situacao", sa.String(16)))
    op.add_column("homologation_items", sa.Column("prazo", sa.String(6)))
    op.add_column("homologation_items", sa.Column("categoria", sa.String(30)))


def downgrade() -> None:
    op.drop_column("homologation_items", "categoria")
    op.drop_column("homologation_items", "prazo")
    op.drop_column("homologation_items", "situacao")
