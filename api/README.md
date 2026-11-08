# Lumawen API

Accounts, mailing list, team journal and review queue for lumawen.com.
Python 3.12+, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL. Licensed AGPL-3.0 (see LICENSE).

## Run it locally

```bash
cd api
uv sync                                   # install dependencies
cp .env.example .env                      # then point LUMAWEN_DATABASE_URL at a local Postgres
uv run alembic upgrade head               # create tables
LUMAWEN_ENV=development uv run uvicorn lumawen_api.main:app --reload --port 3040
```

Open http://localhost:3040/api/health and http://localhost:3040/api/docs.

## Tests

Tests create and drop their own Postgres database. Point them at a server where you may create
databases:

```bash
LUMAWEN_TEST_ADMIN_URL=postgresql+psycopg://postgres:postgres@localhost:5432/postgres uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## Layout

- `src/lumawen_api/` the app (`main.py` builds it, one module per area)
- `migrations/` Alembic migrations, numbered `0001_...`, run on every deploy
- `tests/` pytest
