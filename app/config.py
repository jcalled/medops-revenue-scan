"""
Configuração do serviço, lida do ambiente.

O login é o do núcleo da plataforma: o serviço valida o mesmo JWT com o mesmo
segredo e pergunta ao núcleo, com o token do usuário, se o tenant contratou o
Revenue Scan. Não guarda usuário nem senha.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache

PRODUTO = "REVENUE_SCAN_SUS"
# O mesmo padrão inseguro do núcleo, para token de desenvolvimento valer nos dois.
_SEGREDO_PADRAO = "dev-secret-change-me"
_NOME_SCHEMA = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True)
class Settings:
    app_env: str
    jwt_secret: str
    jwt_alg: str
    core_api_url: str
    entitlement_cache_seconds: int
    database_url: str
    db_schema: str
    redis_url: str
    rq_queue: str
    cors_origins: tuple[str, ...]
    # Conexões por processo (API e worker): o Postgres gerenciado tem limite e o GlosaAI divide o mesmo.
    db_pool_size: int = 2
    db_max_overflow: int = 1

    @property
    def producao(self) -> bool:
        return self.app_env.lower() in {"prod", "production"}


def carregar() -> Settings:
    settings = Settings(
        app_env=os.getenv("APP_ENV", "dev"),
        jwt_secret=os.getenv("JWT_SECRET", _SEGREDO_PADRAO),
        jwt_alg=os.getenv("JWT_ALG", "HS256"),
        core_api_url=os.getenv("CORE_API_URL", "http://localhost:8000").rstrip("/"),
        entitlement_cache_seconds=int(os.getenv("ENTITLEMENT_CACHE_SECONDS", "60")),
        database_url=os.getenv("DATABASE_URL", "sqlite://"),
        db_schema=os.getenv("DB_SCHEMA", "revenue_scan"),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        rq_queue=os.getenv("RQ_QUEUE", "revenue-scan"),
        cors_origins=tuple(o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()),
        db_pool_size=int(os.getenv("DB_POOL_SIZE", "2")),
        db_max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "1")),
    )
    if not _NOME_SCHEMA.match(settings.db_schema):
        raise RuntimeError(f"DB_SCHEMA inválido: {settings.db_schema!r}")
    if settings.producao:
        # Quem tem o segredo forja token de administrador da plataforma.
        if settings.jwt_secret == _SEGREDO_PADRAO or len(settings.jwt_secret) < 32:
            raise RuntimeError("JWT_SECRET ausente ou fraca em produção (mínimo de 32 caracteres).")
        if not settings.core_api_url.startswith("https://") and "localhost" not in settings.core_api_url \
                and not settings.core_api_url.startswith("http://api"):
            raise RuntimeError("CORE_API_URL em produção precisa ser HTTPS ou o serviço interno do compose.")
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return carregar()
