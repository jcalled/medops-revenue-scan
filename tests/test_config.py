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


def test_schema_com_nome_invalido_e_recusado(monkeypatch):
    monkeypatch.setenv("DB_SCHEMA", 'revenue"; drop schema public; --')
    with pytest.raises(RuntimeError, match="DB_SCHEMA"):
        carregar()
