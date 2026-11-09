"""The FastAPI application. Run: uvicorn lumawen_api.main:app --port 3040"""

from fastapi import FastAPI

from . import __version__, admin, auth, google, health, lists, newsletters
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
    app.include_router(auth.router, prefix="/api")
    app.include_router(auth.me_router, prefix="/api")
    app.include_router(google.router, prefix="/api")
    app.include_router(lists.router, prefix="/api")
    app.include_router(admin.router, prefix="/api")
    app.include_router(newsletters.router, prefix="/api")
    return app


app = create_app()
