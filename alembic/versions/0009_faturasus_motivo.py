"""FaturaSUS por motivo: taxa medida na prevenção e o "passou" registrado na situação da AIH

Revision ID: 0009_faturasus_motivo
Revises: 0008_kits_motivo
Create Date: 2026-09-15
"""
import json
from collections import defaultdict

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_faturasus_motivo"
down_revision = "0008_kits_motivo"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_AGORA = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    stats = op.create_table(
        "motive_prevention_stats",
        sa.Column("uf", sa.String(2), nullable=False),
        sa.Column("codigo", sa.String(6), nullable=False),
        sa.Column("grupo", sa.String(30)),
        sa.Column("avaliadas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pegaria", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("regras", _JSON, nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False, server_default=_AGORA),
        sa.PrimaryKeyConstraint("uf", "codigo"),
    )
    with op.batch_alter_table("aih_treatments") as tabela:
        tabela.add_column(sa.Column("faturasus", sa.String(12)))
    with op.batch_alter_table("aih_treatment_events") as tabela:
        tabela.add_column(sa.Column("faturasus", sa.String(12)))

    # A prevenção que já rodou vira taxa por motivo agora; as próximas refazem sozinhas.
    por: dict[tuple[str, str], dict] = defaultdict(lambda: {"grupo": None, "avaliadas": 0, "pegaria": 0, "regras": set()})
    for uf, motivos in op.get_bind().execute(sa.text("SELECT uf, motivos FROM sih_prevention")):
        for m in (json.loads(motivos) if isinstance(motivos, str) else motivos) or []:
            p = por[(uf, m["codigo"])]
            p["grupo"] = p["grupo"] or m.get("grupo")
            p["avaliadas"] += 1
            p["pegaria"] += bool(m.get("pegaria"))
            p["regras"].update(m.get("regras") or [])
    if por:
        op.bulk_insert(stats, [
            {"uf": uf, "codigo": codigo, "grupo": p["grupo"], "avaliadas": p["avaliadas"], "pegaria": p["pegaria"],
             "regras": sorted(p["regras"])}
            for (uf, codigo), p in por.items()
        ])


def downgrade() -> None:
    with op.batch_alter_table("aih_treatment_events") as tabela:
        tabela.drop_column("faturasus")
    with op.batch_alter_table("aih_treatments") as tabela:
        tabela.drop_column("faturasus")
    op.drop_table("motive_prevention_stats")
