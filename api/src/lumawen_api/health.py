"""GET /api/health: is the API up, which build is it, can it reach the database."""

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import __version__
from .config import get_settings
from .db import get_session

router = APIRouter()


def read_build_info(path: str) -> dict[str, Any]:
    """The deploy script writes build-info.json (commit, built_at). Missing is fine in dev."""
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


@router.get("/health")
def health(response: Response, session: Annotated[Session, Depends(get_session)]) -> dict:
    try:
        session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001 - any DB failure means unhealthy
        db_ok = False
    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    build = read_build_info(get_settings().build_info_path)
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "unreachable",
        "version": __version__,
        "commit": build.get("commit"),
        "built_at": build.get("built_at"),
    }
