# Deploy

Scripts and config for running the Lumawen API on maktig's server (cobalt, Debian 13, nginx,
systemd, Postgres). No secrets are stored here: each stage's `api/.env` is written on the server.

| Stage | Checkout | Runs from | Port | Address |
| --- | --- | --- | --- | --- |
| test | `~/lumawen-test` | `/srv/lumawen-test` | 3041 | https://test.lumawen.com |
| prod | `~/lumawen-prod` | `/srv/lumawen-prod` | 3040 | https://lumawen.com (and www) |

The API and email worker run as systemd services under the `lumawen` system user: no sudo, no
login, no home folder, and read-only access to their own copy of `api/` in `/srv`. If the API
were ever broken into, it couldn't change its code, reach your home folder or use sudo. Deploys
still need no password: a rule in `/etc/sudoers.d/lumawen-<stage>` allows exactly one command,
restarting those two services.

## Files

| File | What it is |
| --- | --- |
| `setup-server.sh` | One-time setup, run with sudo: Postgres user and database, clone, `api/.env`, nginx site, HTTPS certificate, the `lumawen` user and services, first deploy. Safe to run again. |
| `deploy.sh` | Every deploy, no sudo: pull `main`, build the dashboard, copy `api/` to `/srv`, `uv sync`, migrate, copy the site, restart the services, check `/api/health`. |
| `systemd/lumawen-api.service`, `lumawen-mail.service` | The services (uvicorn with 2 workers; the email worker), sandboxed. |
| `ecosystem.config.cjs` | The old pm2 setup, used only until `setup-server.sh` has moved a stage to systemd. |
| `nginx/site.conf.template` | Test site: static files plus `/api/` proxied to the API, with per-IP rate limits (certbot adds HTTPS). |
| `nginx/prod.conf.template` | Prod site (lumawen.com + www): the same, on the existing certificate, with no-cache on the dashboard's start files. |
| `nginx/lumawen-ratelimit.conf` | `limit_req` zones, per IPv4 address or IPv6 /64 (goes in `/etc/nginx/conf.d/`). |
| `nginx/lumawen-headers.conf` | Security headers (goes in `/etc/nginx/snippets/`). |
| `nginx/lumawen-proxy.conf` | Proxy headers, including the visitor's IP (goes in `/etc/nginx/snippets/`). |

## First time (test)

DNS: `test.lumawen.com` must point at cobalt (a CNAME to lumawen.com works).

```bash
ssh -t you@your-server
git clone https://github.com/lumawen/lumawen.com.git ~/lumawen-test
sudo bash ~/lumawen-test/deploy/setup-server.sh test     # asks for the Purelymail app password
/srv/lumawen-test/api/.venv/bin/lumawen-admin create-admin admin@lumawen.com
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
/srv/lumawen-prod/api/.venv/bin/lumawen-admin create-admin admin@lumawen.com
curl -s https://lumawen.com/api/health
```

## Every deploy after that

```bash
ssh you@your-server 'bash ~/lumawen-test/deploy/deploy.sh test'
ssh you@your-server 'bash ~/lumawen-prod/deploy/deploy.sh prod'
```

## Handy

- Logs: `sudo journalctl -u lumawen-api-test -n 100` and `sudo journalctl -u lumawen-mail-test` (`-prod` for prod; add `-f` to follow)
- Status: `systemctl status lumawen-api-test lumawen-mail-test`
- Turn on Google sign-in: add `LUMAWEN_GOOGLE_CLIENT_IDS=<web client id>` to `~/lumawen-test/api/.env`, then deploy.
- Change a setting: edit `~/lumawen-test/api/.env`, then deploy again (it copies the file to `/srv` and restarts).
- Admin commands: `/srv/lumawen-test/api/.venv/bin/lumawen-admin --help`
- Database backup: `sudo -u postgres pg_dump lumawen_test > lumawen_test-$(date +%F).sql`
- Undo everything: see the top of `setup-server.sh`
