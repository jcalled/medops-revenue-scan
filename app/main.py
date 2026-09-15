from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import (
    dados, explorar, gestao, health, kit, kits_motivo, me, organizacoes, prospeccao, prova, recuperacao, scan,
)
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="MedOps Revenue Scan SUS", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(health.router)
    app.include_router(me.router)
    app.include_router(organizacoes.router)
    app.include_router(scan.router)
    app.include_router(explorar.router)
    app.include_router(prova.router)
    app.include_router(dados.router)
    app.include_router(recuperacao.router)
    app.include_router(gestao.router)
    app.include_router(prospeccao.router)
    app.include_router(kit.router)
    app.include_router(kits_motivo.router)
    return app


app = create_app()
