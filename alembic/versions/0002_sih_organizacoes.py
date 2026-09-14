"""SIH por hospital, rejeições, motivos, estabelecimentos e organizações

Revision ID: 0002_sih
Revises: 0001_schema
Create Date: 2026-09-14
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_sih"
down_revision = "0001_schema"
branch_labels = None
depends_on = None

_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
_AGORA = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.create_table(
        "data_loads",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("fonte", sa.String(20), nullable=False),
        sa.Column("uf", sa.String(2)),
        sa.Column("competencia", sa.String(6)),
        sa.Column("origem", sa.String(10), nullable=False),
        sa.Column("arquivo", sa.String(255)),
        sa.Column("checksum", sa.String(64)),
        sa.Column("status", sa.String(10), nullable=False, server_default="RUNNING"),
        sa.Column("linhas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("erro", sa.Text()),
        sa.Column("iniciado_em", sa.DateTime(timezone=True), nullable=False, server_default=_AGORA),
        sa.Column("concluido_em", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_data_loads_fonte_uf_competencia", "data_loads", ["fonte", "uf", "competencia"])

    op.create_table(
        "establishments",
        sa.Column("cnes", sa.String(7), primary_key=True),
        sa.Column("nome_fantasia", sa.String(255)),
        sa.Column("razao_social", sa.String(255)),
        sa.Column("uf", sa.String(2)),
        sa.Column("codigo_municipio", sa.String(7)),
        sa.Column("cnpj_entidade", sa.String(14)),
        sa.Column("natureza_juridica", sa.String(4)),
        sa.Column("esfera", sa.String(20)),
        sa.Column("tipo_unidade", sa.Integer()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False, server_default=_AGORA),
    )

    op.create_table(
        "management_organizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sigla", sa.String(30), nullable=False, unique=True),
        sa.Column("nome", sa.String(255), nullable=False),
        sa.Column("cnpj", sa.String(14)),
        sa.Column("uf", sa.String(2)),
        sa.Column("site", sa.String(255)),
        sa.Column("fonte", sa.String(500)),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=_AGORA),
    )
    op.create_table(
        "organization_establishments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(),
                  sa.ForeignKey("management_organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cnes", sa.String(7), nullable=False),
        sa.Column("sigla", sa.String(20)),
        sa.Column("situacao", sa.String(12), nullable=False, server_default="A_CONFIRMAR"),
        sa.Column("fonte", sa.String(500)),
        sa.Column("verificado_em", sa.Date()),
        sa.UniqueConstraint("organization_id", "cnes", name="uq_organization_establishments"),
    )
    op.create_index("ix_organization_establishments_organization_id", "organization_establishments",
                    ["organization_id"])

    op.create_table(
        "sih_hospital_month",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("uf", sa.String(2), nullable=False),
        sa.Column("cnes", sa.String(7), nullable=False),
        sa.Column("competencia", sa.String(6), nullable=False),
        sa.Column("aih_aprovadas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valor_aprovado", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("aih_rejeitadas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valor_rejeitado", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("diarias", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("diarias_uti", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("permanencia_dias", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("uf", "cnes", "competencia", name="uq_sih_hospital_month"),
    )
    op.create_index("ix_sih_hospital_month_cnes", "sih_hospital_month", ["cnes", "competencia"])

    op.create_table(
        "sih_approved_aih",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("uf", sa.String(2), nullable=False),
        sa.Column("competencia", sa.String(6), nullable=False),
        sa.Column("cnes", sa.String(7), nullable=False),
        sa.Column("n_aih", sa.String(13), nullable=False),
        sa.UniqueConstraint("uf", "n_aih", "competencia", name="uq_sih_approved_aih"),
    )
    op.create_index("ix_sih_approved_aih_n_aih", "sih_approved_aih", ["n_aih"])
    op.create_index("ix_sih_approved_aih_uf_competencia", "sih_approved_aih", ["uf", "competencia"])

    op.create_table(
        "sih_rejections",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("uf", sa.String(2), nullable=False),
        sa.Column("competencia", sa.String(6), nullable=False),
        sa.Column("cnes", sa.String(7), nullable=False),
        sa.Column("n_aih", sa.String(13), nullable=False),
        sa.Column("competencia_aih", sa.String(6)),
        sa.Column("proc_realizado", sa.String(10)),
        sa.Column("valor", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("dt_internacao", sa.Date()),
        sa.Column("dt_saida", sa.Date()),
        sa.Column("marca_uti", sa.String(2)),
        sa.UniqueConstraint("uf", "n_aih", "competencia", name="uq_sih_rejections"),
    )
    op.create_index("ix_sih_rejections_cnes_competencia", "sih_rejections", ["cnes", "competencia"])
    op.create_index("ix_sih_rejections_uf_competencia", "sih_rejections", ["uf", "competencia"])

    op.create_table(
        "sih_rejection_reasons",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("uf", sa.String(2), nullable=False),
        sa.Column("competencia", sa.String(6), nullable=False),
        sa.Column("cnes", sa.String(7), nullable=False),
        sa.Column("n_aih", sa.String(13), nullable=False),
        sa.Column("codigo_erro", sa.String(6), nullable=False),
        sa.UniqueConstraint("uf", "n_aih", "competencia", "codigo_erro", name="uq_sih_rejection_reasons"),
    )
    op.create_index("ix_sih_rejection_reasons_cnes_competencia", "sih_rejection_reasons", ["cnes", "competencia"])
    op.create_index("ix_sih_rejection_reasons_uf_competencia", "sih_rejection_reasons", ["uf", "competencia"])

    op.create_table(
        "sih_error_codes",
        sa.Column("codigo", sa.String(6), primary_key=True),
        sa.Column("descricao", sa.String(255), nullable=False),
    )


def downgrade() -> None:
    for tabela in ("sih_error_codes", "sih_rejection_reasons", "sih_rejections", "sih_approved_aih",
                   "sih_hospital_month", "organization_establishments", "management_organizations",
                   "establishments", "data_loads"):
        op.drop_table(tabela)
