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

## Accounts

Email accounts with Argon2 passwords. All routes are under `/api`:

| Route | What it does |
| --- | --- |
| `POST /auth/register` | Sends a confirm link. Always answers `202 check_email`, so it never reveals who has an account. |
| `POST /auth/resend-confirmation` | Sends the confirm link again. |
| `POST /auth/confirm` | Uses the link's token. The account's password is the one chosen with that link. |
| `POST /auth/login` | `client: "web"` sets an HttpOnly cookie; `client: "app"` returns an access token (15 min) and a refresh token (30 days). |
| `POST /auth/refresh` | App only. Rotates both tokens; re-using an old refresh token ends that session. |
| `POST /auth/logout`, `/auth/logout-all` | Ends this session, or every session. |
| `POST /auth/forgot-password`, `/auth/reset-password` | One-time reset link (60 min). A reset signs out every device. |
| `GET /me` | The signed-in user. |
| `GET /admin/users` | Admins only. |

Five wrong passwords lock an account for 15 minutes. Each address gets at most 3 emails of a kind
an hour. Tokens are stored only as SHA-256 hashes. Cookie-authenticated POSTs from another origin
are refused. Per-IP limits come from nginx (`limit_req`) at deploy time.

Emails go into the `email_outbox` table and a worker sends them:

```bash
uv run lumawen-admin send-emails --loop                 # under pm2 on the server
uv run lumawen-admin create-admin admin@lumawen.com     # asks for a password
```

With `LUMAWEN_MAIL_BACKEND=console` (the default) the worker only logs emails.

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
