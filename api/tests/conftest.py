"""Test fixtures: every test run gets its own throwaway Postgres database.

Set LUMAWEN_TEST_ADMIN_URL to a Postgres URL that may create databases, e.g.
postgresql+psycopg://postgres:postgres@localhost:5432/postgres (the CI default).
"""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

API_DIR = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get(
    "LUMAWEN_TEST_ADMIN_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"
)


def _reset_caches() -> None:
    from lumawen_api import config, db

    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db._session_factory.cache_clear()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    name = f"lumawen_test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    os.environ["LUMAWEN_DATABASE_URL"] = url
    os.environ["LUMAWEN_ENV"] = "test"
    _reset_caches()
    try:
        command.upgrade(Config(str(API_DIR / "alembic.ini")), "head")
        yield url
    finally:
        from lumawen_api.db import get_engine

        get_engine().dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def app(database_url: str):
    from lumawen_api.main import create_app

    return create_app()


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_tables(request) -> Iterator[None]:
    """Empty the account tables after each test that used the database."""
    yield
    if "database_url" not in request.fixturenames:
        return
    from lumawen_api.db import get_engine

    with get_engine().begin() as conn:
        conn.execute(
            text(
                "TRUNCATE users, subscribers, email_outbox, login_throttle RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
def db(database_url: str):
    from lumawen_api.db import _session_factory

    with _session_factory()() as session:
        yield session
