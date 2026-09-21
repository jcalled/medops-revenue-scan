"""Arquivos entregues pelo hospital ou pela OSS no contrato, por tenant

Revision ID: 0012_arquivos_do_contrato
Revises: 0011_kits_regras_oficiais
Create Date: 2026-09-21

Ficam do tenant e à parte do dado público do DATASUS. O SISAIH01 vai para o
FaturaSUS do núcleo; aqui fica a referência da análise.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_arquivos_do_contrato"
down_revision = "0011_kits_regras_oficiais"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "contract_files",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("management_organizations.id", ondelete="SET NULL")),
        sa.Column("cnes", sa.String(7)),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("competencia", sa.String(6)),
        sa.Column("nome", sa.String(255), nullable=False),
        sa.Column("tamanho", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("conteudo", sa.LargeBinary()),
        sa.Column("analysis_id", sa.Integer()),
        sa.Column("resumo", _JSON),
        sa.Column("enviado_por", sa.Integer()),
        sa.Column("enviado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_contract_files_tenant_id", "contract_files", ["tenant_id"])
    op.create_index("ix_contract_files_organization_id", "contract_files", ["organization_id"])
    op.create_index("ix_contract_files_tenant_org", "contract_files", ["tenant_id", "organization_id"])


def downgrade() -> None:
    op.drop_table("contract_files")
