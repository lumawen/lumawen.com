# Deploy

Scripts and config for running the Lumawen API on maktig's server (cobalt, Debian 13, nginx,
pm2, Postgres). No secrets are stored here: each stage's `api/.env` is written on the server.

| Stage | Checkout | Port | Address |
| --- | --- | --- | --- |
| test | `~/lumawen-test` | 3041 | https://test.lumawen.com |
| prod | `~/lumawen-prod` | 3040 | https://lumawen.com (later; its nginx config will be merged by hand) |

## Files

| File | What it is |
| --- | --- |
| `setup-server.sh` | One-time setup, run with sudo: Postgres user and database, clone, `api/.env`, nginx site, HTTPS certificate, first deploy. Safe to run again. |
| `deploy.sh` | Every deploy, no sudo: pull `main`, `uv sync`, migrate, copy the site (test), restart pm2, check `/api/health`. |
| `ecosystem.config.cjs` | pm2 apps: `lumawen-api-<stage>` (uvicorn, 2 workers) and `lumawen-mail-<stage>` (email worker). |
| `nginx/site.conf.template` | The site: static files plus `/api/` proxied to the API, with per-IP rate limits. |
| `nginx/lumawen-ratelimit.conf` | `limit_req` zones (goes in `/etc/nginx/conf.d/`). |
| `nginx/lumawen-proxy.conf` | Proxy headers, including the visitor's IP (goes in `/etc/nginx/snippets/`). |

## First time (test)

DNS: `test.lumawen.com` must point at cobalt (a CNAME to lumawen.com works).

```bash
ssh -t shawn@cobalt
git clone https://github.com/lumawen/lumawen.com.git ~/lumawen-test
sudo bash ~/lumawen-test/deploy/setup-server.sh test     # asks for the Purelymail app password
~/lumawen-test/api/.venv/bin/lumawen-admin create-admin admin@lumawen.com
curl -s https://test.lumawen.com/api/health
```

## Every deploy after that

```bash
ssh shawn@cobalt 'bash ~/lumawen-test/deploy/deploy.sh test'
```

## Handy

- Logs: `pm2 logs lumawen-api-test` and `pm2 logs lumawen-mail-test`
- Turn on Google sign-in: add `LUMAWEN_GOOGLE_CLIENT_IDS=<web id>,<android id>` to `api/.env`, then restart.
- Change a setting: edit `~/lumawen-test/api/.env`, then `pm2 restart lumawen-api-test lumawen-mail-test`
- Database backup: `sudo -u postgres pg_dump lumawen_test > lumawen_test-$(date +%F).sql`
- Undo everything: see the top of `setup-server.sh`
