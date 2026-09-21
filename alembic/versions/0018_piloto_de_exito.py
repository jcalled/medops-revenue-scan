"""Acompanhamento do tipo PILOTO: só as AIH escolhidas, para provar o êxito com dado público

Revision ID: 0018_piloto_de_exito
Revises: 0017_homologacao_situacao
Create Date: 2026-09-21
"""
import sqlalchemy as sa
from alembic import op

revision = "0018_piloto_de_exito"
down_revision = "0017_homologacao_situacao"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recovery_trackings", sa.Column("tipo", sa.String(10), nullable=False, server_default="CONTRATO"))


def downgrade() -> None:
    op.drop_column("recovery_trackings", "tipo")
