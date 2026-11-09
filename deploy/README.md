# Deploy

Scripts and config for running the Lumawen API on maktig's server (cobalt, Debian 13, nginx,
pm2, Postgres). No secrets are stored here: each stage's `api/.env` is written on the server.

| Stage | Checkout | Port | Address |
| --- | --- | --- | --- |
| test | `~/lumawen-test` | 3041 | https://test.lumawen.com |
| prod | `~/lumawen-prod` | 3040 | https://lumawen.com (and www) |

## Files

| File | What it is |
| --- | --- |
| `setup-server.sh` | One-time setup, run with sudo: Postgres user and database, clone, `api/.env`, nginx site, HTTPS certificate, first deploy. Safe to run again. |
| `deploy.sh` | Every deploy, no sudo: pull `main`, build the dashboard, `uv sync`, migrate, copy the site, restart pm2, check `/api/health`. |
| `ecosystem.config.cjs` | pm2 apps: `lumawen-api-<stage>` (uvicorn, 2 workers) and `lumawen-mail-<stage>` (email worker). |
| `nginx/site.conf.template` | Test site: static files plus `/api/` proxied to the API, with per-IP rate limits (certbot adds HTTPS). |
| `nginx/prod.conf.template` | Prod site (lumawen.com + www): the same, on the existing certificate, with no-cache on the dashboard's start files. |
| `nginx/lumawen-ratelimit.conf` | `limit_req` zones (goes in `/etc/nginx/conf.d/`). |
| `nginx/lumawen-proxy.conf` | Proxy headers, including the visitor's IP (goes in `/etc/nginx/snippets/`). |

## First time (test)

DNS: `test.lumawen.com` must point at cobalt (a CNAME to lumawen.com works).

```bash
ssh -t you@your-server
git clone https://github.com/lumawen/lumawen.com.git ~/lumawen-test
sudo bash ~/lumawen-test/deploy/setup-server.sh test     # asks for the Purelymail app password
~/lumawen-test/api/.venv/bin/lumawen-admin create-admin admin@lumawen.com
curl -s https://test.lumawen.com/api/health
```

## First time (prod)

Replaces the hand-made static-site nginx config for lumawen.com (a backup of `/etc/nginx` is
kept) and from then on publishes the site from this repo, so the old site zips and
`update-site.sh` are no longer used.

```bash
ssh -t you@your-server
git clone https://github.com/lumawen/lumawen.com.git ~/lumawen-prod
sudo bash ~/lumawen-prod/deploy/setup-server.sh prod     # asks for the Purelymail app password
~/lumawen-prod/api/.venv/bin/lumawen-admin create-admin admin@lumawen.com
curl -s https://lumawen.com/api/health
```

## Every deploy after that

```bash
ssh you@your-server 'bash ~/lumawen-test/deploy/deploy.sh test'
ssh you@your-server 'bash ~/lumawen-prod/deploy/deploy.sh prod'
```

## Handy

- Logs: `pm2 logs lumawen-api-test` and `pm2 logs lumawen-mail-test` (`-prod` for prod)
- Turn on Google sign-in: add `LUMAWEN_GOOGLE_CLIENT_IDS=<web client id>` to `api/.env`, then restart.
- Change a setting: edit `~/lumawen-test/api/.env`, then `pm2 restart lumawen-api-test lumawen-mail-test`
- Database backup: `sudo -u postgres pg_dump lumawen_test > lumawen_test-$(date +%F).sql`
- Undo everything: see the top of `setup-server.sh`
