"""Alembic must accept database URLs whose password is percent-encoded."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

API_DIR = Path(__file__).resolve().parent.parent


def test_alembic_accepts_percent_encoded_password(database_url):
    role = f"lumawen_pct_{uuid.uuid4().hex[:8]}"
    password = "p@ss%/w+rd="
    admin = create_engine(database_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'"))
    try:
        with admin.connect() as conn:
            conn.execute(text(f'GRANT SELECT ON alembic_version TO "{role}"'))
        url = make_url(database_url).set(username=role, password=password)
        rendered = url.render_as_string(hide_password=False)
        assert "%" in rendered
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "current"],
            cwd=API_DIR,
            env={**os.environ, "LUMAWEN_DATABASE_URL": rendered},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "(head)" in result.stdout
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP OWNED BY "{role}"'))
            conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        admin.dispose()


def test_models_match_migrations(database_url):
    """alembic check: the tables in models.py are exactly what the migrations create."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=API_DIR,
        env={**os.environ, "LUMAWEN_DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_downgrade_and_upgrade_again(database_url):
    def alembic(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=API_DIR,
            env={**os.environ, "LUMAWEN_DATABASE_URL": database_url},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr

    alembic("downgrade", "base")
    alembic("upgrade", "head")
