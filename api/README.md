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
| `POST /auth/confirm` | Needs the link's token **and** the password chosen at that sign-up, so nobody can claim your address with their own password. Then signs you in, like `/auth/login` (same `client` choice and wrong-password limits). |
| `POST /auth/login` | `client: "web"` sets an HttpOnly cookie; `client: "app"` returns an access token (15 min) and a refresh token (30 days). |
| `POST /auth/refresh` | App only. Rotates both tokens. Re-using the previous token within 30 s gets `409` (a retry or race); later, it ends the session. |
| `POST /auth/logout`, `/auth/logout-all` | Ends this session, or every session. |
| `POST /auth/forgot-password`, `/auth/reset-password` | One-time reset link (60 min). A reset signs out every device. |
| `POST /auth/google` | Sign in with a Google ID token (from the web dashboard or the Android app). Links to the account with the same email, or creates one. |
| `GET /me` | The signed-in user. |

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

## Mailing list

"Lumawen updates", with double opt-in. The site's "Get updates" form posts here.

| Route | What it does |
| --- | --- |
| `POST /list/subscribe` | `{email}`. Always answers `202 check_email` and emails a confirm link (7 days), unless the address is already subscribed. At most 10 sign-ups an hour per IP and 300 confirm emails an hour in all; unconfirmed sign-ups are deleted after 30 days. A filled-in `website` field (a hidden honeypot) is dropped. |
| `GET`/`POST /list/confirm?token=` | `GET` shows a page with a button; only the button's `POST` subscribes, so mail scanners that open links can't. |
| `GET`/`POST /list/unsubscribe?token=` | The same, and `POST` also takes one-click unsubscribe from mail apps (RFC 8058). |
| `GET /admin/subscribers` | Admins: paged list with counts, `status=` filter and `q=` email search. |
| `GET /admin/subscribers.csv` | Admins: CSV export (subscribed only by default; `status=` another status or `all`). |
| `DELETE /admin/subscribers/{id}` | Admins: erase someone completely (privacy request). |

Confirm tokens are stored as hashes. Each subscriber's unsubscribe token is stored as-is so every
newsletter can print it (`lists.unsubscribe_url`); it can only unsubscribe.

## Roles and staff tools

| Role | Can |
| --- | --- |
| `user` | Use their own account. |
| `contributor` | Write, edit and test newsletter drafts (tests go only to themselves). |
| `moderator` | Find users; suspend and unsuspend users and contributors. |
| `security` | Moderator, plus suspend moderators, sign anyone but an admin out everywhere, read the activity log. |
| `admin` | Everything: roles, account deletion, mailing list, scheduling and sending. |

| Route | What it does |
| --- | --- |
| `GET /admin/users` | Moderators, security, admins: `q=` (email or name), `role=`, `suspended=`; counts per role. |
| `PUT /admin/users/{id}/role` | Admins: `{role}`. Not your own. |
| `POST /admin/users/{id}/suspend`, `/unsuspend` | `{reason}`. Suspending ends every session and blocks sign-in. |
| `POST /admin/users/{id}/sign-out` | Security, admins: end every session. |
| `DELETE /admin/users/{id}` | Admins: delete the account (not your own). |
| `GET /admin/activity` | Security, admins: every staff action, newest first, `q=` search. |

Make the first admin with `lumawen-admin create-admin`; give other roles from the Users tab.

## Newsletters

Writers (admins and contributors) write a newsletter and send themselves a test; admins then
send it now or schedule it. All under `/api/admin/newsletters` (cookie requests must come from
our own origin):

| Route | What it does |
| --- | --- |
| `POST /admin/newsletters` | New draft: `{subject, body_text}` (plain text). |
| `GET /admin/newsletters`, `GET /{id}` | Newsletters with status and delivery counts (pending, sent, failed); `archived=true` for archived ones. |
| `PATCH /{id}` | Edit, until it's sent (contributors: drafts only). |
| `DELETE /{id}` | Draft: deleted. Scheduled (admins): cancelled and deleted. Sent (admins): archived, deleted for good after 30 days. |
| `POST /{id}/restore` | Admins: un-archive. |
| `POST /{id}/test` | One `[Test]` copy to you (or `{to}`), right away. 20 an hour. |
| `POST /{id}/schedule` | `{send_at}` with a time zone, or `{}` for now. |
| `POST /{id}/cancel` | Back to draft, until the send worker picks it up. |

When it's due, the send worker copies it into the outbox once per subscribed address (a unique
index makes that happen only once), adding a footer with that person's unsubscribe link and the
`List-Unsubscribe` + `List-Unsubscribe-Post` headers, so Gmail and others show their own
unsubscribe button. It then sends at `LUMAWEN_MAIL_PER_SECOND`. Each email is sent and committed on
its own, and on SIGINT/SIGTERM (pm2 restarts) the worker stops after the email in flight. A
newsletter is skipped for anyone who unsubscribed after it was queued. Sent and failed emails
are deleted after 30 days.

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
