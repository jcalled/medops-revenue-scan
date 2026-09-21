"""Kits com as regras oficiais do MS: capacidade não reapresentável, prazo, datas e fontes

Revision ID: 0011_kits_regras_oficiais
Revises: 0010_evidencia_rd
Create Date: 2026-09-21

Atualiza só os kits que ninguém editou pela tela (atualizado_por vazio) e inclui
os que faltam. Kit editado ou confirmado por uma pessoa fica como está.

A classe NAO_REAPRESENTAVEL tem 18 letras: a coluna passa de 16 para 24 antes dos textos.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_kits_regras_oficiais"
down_revision = "0010_evidencia_rd"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_CAMPOS = ("titulo", "significado", "classe", "onde_corrigir", "passos", "dados_do_hospital", "evidencias",
           "prevencao", "fonte")


def upgrade() -> None:
    from app.seed.kits_motivo import KITS

    with op.batch_alter_table("motive_kits") as tabela:
        tabela.alter_column("classe", type_=sa.String(24), existing_type=sa.String(16), existing_nullable=False)

    kits = sa.table(
        "motive_kits",
        sa.column("codigo", sa.String), sa.column("titulo", sa.String), sa.column("significado", sa.Text),
        sa.column("classe", sa.String), sa.column("onde_corrigir", sa.String), sa.column("passos", _JSON),
        sa.column("dados_do_hospital", _JSON), sa.column("evidencias", _JSON), sa.column("prevencao", sa.Text),
        sa.column("fonte", sa.Text), sa.column("revisao", sa.String), sa.column("atualizado_por", sa.Integer),
    )
    conexao = op.get_bind()
    existentes = {c for (c,) in conexao.execute(sa.select(kits.c.codigo))}
    for k in KITS:
        dados = {c: k.get(c) for c in _CAMPOS}
        dados["evidencias"] = dados["evidencias"] or []
        if k["codigo"] in existentes:
            conexao.execute(kits.update().where(kits.c.codigo == k["codigo"], kits.c.atualizado_por.is_(None))
                            .values(**dados))
        else:
            conexao.execute(kits.insert().values(codigo=k["codigo"], revisao="A_CONFIRMAR", **dados))


def downgrade() -> None:
    # O texto anterior dos kits não é restaurado: a revisão é de conteúdo, não de estrutura.
    pass
