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
| `POST /auth/register` | Sends a confirm link. Always answers `202 check_email`, so it never reveals who has an account. Signing up again sends a new link. |
| `POST /auth/confirm` | Needs the link's token **and** the password chosen at that sign-up, so nobody can claim your address with their own password. |
| `POST /auth/login` | `client: "web"` sets an HttpOnly cookie; `client: "app"` returns an access token (15 min) and a refresh token (30 days). |
| `POST /auth/refresh` | App only. Rotates both tokens. Re-using the previous token within 30 s gets `409` (a retry or race); later, it ends the session. |
| `POST /auth/logout`, `/auth/logout-all` | Ends this session, or every session. |
| `POST /auth/forgot-password`, `/auth/reset-password` | One-time reset link (60 min). A reset signs out every device. |
| `POST /auth/google` | Sign in with a Google ID token (from the web dashboard or the Android app). Links to the account with the same email, or creates one. |
| `GET /me` | The signed-in user. |
| `GET /admin/users` | Admins only. |

Five wrong passwords from one IP for one email pause that pair for 15 minutes, and 50 from anywhere
pause the email for an hour (a reset lifts it). Unknown emails are counted the same way, and every
sign-in does exactly one Argon2 check, so neither answers nor timing reveal who has an account.
Each address gets at most 3 emails of a kind an hour. Tokens are stored only as SHA-256 hashes.
Cookie-authenticated POSTs and web sign-ins from another origin are refused. Behind nginx, run
uvicorn with `--proxy-headers --forwarded-allow-ips 127.0.0.1` so the visitor's IP is used; nginx
`limit_req` adds per-IP limits at deploy time.

Google sign-in accepts only ID tokens issued to our own OAuth client IDs
(`LUMAWEN_GOOGLE_CLIENT_IDS`) for a Google-verified email. It links to an existing account only
when Google really owns the address (Gmail, or a Workspace domain via the `hd` claim); for any
other address an existing account must sign in with its password. A password reset removes
linked Google accounts. (Unlike register, a 409 here can tell someone holding a Google account
for that exact address that a Lumawen account exists. Workspace alias domains don't auto-link.) Google's signing keys are cached and fetched with a 5 s timeout.

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
