"""Produção não sobe com segredo fraco nem schema com nome perigoso."""
import pytest

from app.config import carregar


@pytest.mark.parametrize("segredo", ["dev-secret-change-me", "curto"])
def test_producao_recusa_segredo_fraco(monkeypatch, segredo):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", segredo)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        carregar()


def test_producao_exige_https_para_o_nucleo(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("CORE_API_URL", "http://nucleo.exemplo.com.br")
    with pytest.raises(RuntimeError, match="CORE_API_URL"):
        carregar()


def test_engine_do_postgres_funciona_atras_de_pool_em_modo_transacao():
    """Schema na compilação da query, não num SET da sessão; poucas conexões por processo."""
    from dataclasses import replace

    from app.config import get_settings
    from app.db import criar_engine

    base = get_settings()
    postgres = criar_engine(replace(base, database_url="postgresql+psycopg2://u:s@db.exemplo:25061/pool?sslmode=require"))
    assert postgres.get_execution_options()["schema_translate_map"] == {None: "revenue_scan"}
    assert postgres.pool.size() == 2 and postgres.pool._max_overflow == 1

    sqlite = criar_engine(replace(base, database_url="sqlite://"))
    assert "schema_translate_map" not in sqlite.get_execution_options()


def test_schema_com_nome_invalido_e_recusado(monkeypatch):
    monkeypatch.setenv("DB_SCHEMA", 'revenue"; drop schema public; --')
    with pytest.raises(RuntimeError, match="DB_SCHEMA"):
        carregar()
