"""The FastAPI application. Run: uvicorn lumawen_api.main:app --port 3040"""

from fastapi import FastAPI

from . import __version__, health
from .config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    is_prod = settings.env == "production"
    app = FastAPI(
        title="Lumawen API",
        version=__version__,
        # Interactive docs only outside production.
        docs_url=None if is_prod else "/api/docs",
        openapi_url=None if is_prod else "/api/openapi.json",
        redoc_url=None,
    )
    app.include_router(health.router, prefix="/api")
    return app


app = create_app()
