"""Mix de procedimentos, leitos e habilitações do CNES, peer groups, benchmark e oportunidades

Revision ID: 0003_benchmark
Revises: 0002_sih
Create Date: 2026-09-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_benchmark"
down_revision = "0002_sih"
branch_labels = None
depends_on = None

_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_AGORA = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.create_table(
        "sih_hospital_procedure_month",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("uf", sa.String(2), nullable=False),
        sa.Column("cnes", sa.String(7), nullable=False),
        sa.Column("competencia", sa.String(6), nullable=False),
        sa.Column("proc_realizado", sa.String(10), nullable=False),
        sa.Column("complexidade", sa.String(2), nullable=False, server_default=""),
        sa.Column("aih", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valor", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("diarias", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("diarias_uti", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("permanencia_dias", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("uf", "cnes", "competencia", "proc_realizado", "complexidade",
                            name="uq_sih_hospital_procedure_month"),
    )
    op.create_index("ix_sih_hospital_procedure_month_cnes", "sih_hospital_procedure_month", ["cnes", "competencia"])
    op.create_index("ix_sih_hospital_procedure_month_proc", "sih_hospital_procedure_month",
                    ["proc_realizado", "competencia"])

    op.create_table(
        "cnes_beds",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("uf", sa.String(2), nullable=False),
        sa.Column("competencia", sa.String(6), nullable=False),
        sa.Column("cnes", sa.String(7), nullable=False),
        sa.Column("codigo_leito", sa.String(2), nullable=False),
        sa.Column("tipo_leito", sa.String(1), nullable=False),
        sa.Column("qt_existente", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("qt_sus", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("uf", "competencia", "cnes", "codigo_leito", "tipo_leito", name="uq_cnes_beds"),
    )
    op.create_index("ix_cnes_beds_cnes", "cnes_beds", ["cnes", "competencia"])

    op.create_table(
        "cnes_enablements",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("uf", sa.String(2), nullable=False),
        sa.Column("competencia", sa.String(6), nullable=False),
        sa.Column("cnes", sa.String(7), nullable=False),
        sa.Column("habilitacao", sa.String(4), nullable=False),
        sa.Column("competencia_inicio", sa.String(6), nullable=False, server_default=""),
        sa.Column("competencia_fim", sa.String(6)),
        sa.UniqueConstraint("uf", "competencia", "cnes", "habilitacao", "competencia_inicio",
                            name="uq_cnes_enablements"),
    )
    op.create_index("ix_cnes_enablements_cnes", "cnes_enablements", ["cnes", "competencia"])

    op.create_table(
        "peer_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cnes", sa.String(7), nullable=False),
        sa.Column("periodo_inicio", sa.String(6), nullable=False),
        sa.Column("periodo_fim", sa.String(6), nullable=False),
        sa.Column("criterio", _JSON, nullable=False),
        sa.Column("gerado_em", sa.DateTime(timezone=True), nullable=False, server_default=_AGORA),
        sa.UniqueConstraint("cnes", "periodo_inicio", "periodo_fim", name="uq_peer_groups"),
    )
    op.create_table(
        "peer_group_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("peer_group_id", sa.Integer(), sa.ForeignKey("peer_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cnes", sa.String(7), nullable=False),
        sa.Column("distancia", sa.Float(), nullable=False, server_default="0"),
    )
    op.create_index("ix_peer_group_members_peer_group_id", "peer_group_members", ["peer_group_id"])
    op.create_table(
        "benchmark_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("peer_group_id", sa.Integer(), sa.ForeignKey("peer_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cnes", sa.String(7), nullable=False),
        sa.Column("metrica", sa.String(40), nullable=False),
        sa.Column("valor", sa.Float()),
        sa.Column("mediana", sa.Float()),
        sa.Column("p25", sa.Float()),
        sa.Column("p75", sa.Float()),
        sa.Column("percentil", sa.Float()),
        sa.Column("n_pares", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("peer_group_id", "metrica", name="uq_benchmark_metrics"),
    )
    op.create_index("ix_benchmark_metrics_peer_group_id", "benchmark_metrics", ["peer_group_id"])

    op.create_table(
        "opportunities",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("cnes", sa.String(7), nullable=False),
        sa.Column("periodo_inicio", sa.String(6), nullable=False),
        sa.Column("periodo_fim", sa.String(6), nullable=False),
        sa.Column("opportunity_type", sa.String(40), nullable=False),
        sa.Column("categoria", sa.String(40), nullable=False, server_default=""),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("unidade", sa.String(12), nullable=False, server_default="R$"),
        sa.Column("observed_value", sa.Float()),
        sa.Column("benchmark_value", sa.Float()),
        sa.Column("gap", sa.Float()),
        sa.Column("estimated_financial_impact", sa.Numeric(16, 2)),
        sa.Column("confidence_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence", _JSON, nullable=False),
        sa.Column("recommended_action", sa.Text(), nullable=False, server_default=""),
        sa.Column("gerado_em", sa.DateTime(timezone=True), nullable=False, server_default=_AGORA),
    )
    op.create_index("ix_opportunities_cnes_periodo", "opportunities", ["cnes", "periodo_inicio", "periodo_fim"])
    op.create_index("ix_opportunities_tipo", "opportunities", ["opportunity_type"])

    op.create_table(
        "hospital_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cnes", sa.String(7), nullable=False),
        sa.Column("periodo_inicio", sa.String(6), nullable=False),
        sa.Column("periodo_fim", sa.String(6), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("impacto_estimado", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("valor_apresentado", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("principal_tipo", sa.String(40)),
        sa.Column("principal_categoria", sa.String(40)),
        sa.Column("gerado_em", sa.DateTime(timezone=True), nullable=False, server_default=_AGORA),
        sa.UniqueConstraint("cnes", "periodo_inicio", "periodo_fim", name="uq_hospital_scores"),
    )


def downgrade() -> None:
    for tabela in ("hospital_scores", "opportunities", "benchmark_metrics", "peer_group_members", "peer_groups",
                   "cnes_enablements", "cnes_beds", "sih_hospital_procedure_month"):
        op.drop_table(tabela)
