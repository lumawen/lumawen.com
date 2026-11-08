#!/usr/bin/env bash
# One-time server setup for the Lumawen API on cobalt. Run with sudo:
#   sudo bash setup-server.sh test      # test.lumawen.com, port 3041
#   sudo bash setup-server.sh prod      # lumawen.com, port 3040 (later)
#
# It creates the Postgres database and user, clones the repo for the stage, writes api/.env
# (asking for the Purelymail password, hidden), adds the nginx site and its HTTPS certificate,
# then runs deploy.sh as the normal user. Safe to run again: it keeps existing passwords.
#
# UNDO (test):
#   sudo -u postgres psql -c 'DROP DATABASE lumawen_test' -c 'DROP ROLE lumawen_test'
#   pm2 delete lumawen-api-test lumawen-mail-test && pm2 save
#   sudo rm /etc/nginx/sites-enabled/test.lumawen.com /etc/nginx/sites-available/test.lumawen.com
#   sudo systemctl reload nginx;  rm -rf ~/lumawen-test;  sudo rm -rf /var/www/test.lumawen.com
set -euo pipefail

STAGE="${1:-}"
case "$STAGE" in
  test) HOST="test.lumawen.com"; PORT=3041; CERT_NAMES=(-d test.lumawen.com) ;;
  prod) HOST="lumawen.com"; PORT=3040; CERT_NAMES=(-d lumawen.com -d www.lumawen.com) ;;
  *) echo "usage: sudo bash setup-server.sh test|prod" >&2; exit 2 ;;
esac
[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }

APP_USER="${SUDO_USER:-shawn}"
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
DIR="$APP_HOME/lumawen-$STAGE"
DB="lumawen_$STAGE"
REPO="https://github.com/lumawen/lumawen.com.git"
WEB="/var/www/$HOST/html"
as_user() { sudo -u "$APP_USER" -H "$@"; }
say() { printf '\n==> %s\n' "$*"; }

say "Checking tools"
for tool in git psql nginx certbot pm2 openssl; do
  if ! command -v "$tool" >/dev/null && ! as_user bash -lc "command -v $tool" >/dev/null; then
    echo "Missing: $tool" >&2; exit 1
  fi
done
if ! as_user bash -lc 'command -v uv' >/dev/null; then
  say "Installing uv (Python package manager) for $APP_USER"
  as_user bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi

say "Getting the code into $DIR"
if [[ -d "$DIR/.git" ]]; then
  as_user git -C "$DIR" fetch --quiet origin main
else
  as_user git clone --quiet "$REPO" "$DIR"
fi

say "Postgres database $DB"
ENV_FILE="$DIR/api/.env"
DB_PASS=""
if [[ -f "$ENV_FILE" ]]; then
  DB_PASS="$(sed -n "s#^LUMAWEN_DATABASE_URL=postgresql+psycopg://$DB:\([0-9a-f]*\)@.*#\1#p" "$ENV_FILE")"
fi
if [[ -z "$DB_PASS" ]]; then
  DB_PASS="$(openssl rand -hex 24)"  # hex: safe in a URL without escaping
fi
if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB'" | grep -q 1; then
  sudo -u postgres psql -q -c "ALTER ROLE $DB WITH LOGIN PASSWORD '$DB_PASS'"
else
  sudo -u postgres psql -q -c "CREATE ROLE $DB WITH LOGIN PASSWORD '$DB_PASS'"
fi
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB'" | grep -q 1; then
  sudo -u postgres createdb -O "$DB" "$DB"
fi

say "Writing $ENV_FILE"
SMTP_PASS=""
if [[ -f "$ENV_FILE" ]]; then
  SMTP_PASS="$(sed -n 's/^LUMAWEN_SMTP_PASSWORD=//p' "$ENV_FILE")"
fi
if [[ -z "$SMTP_PASS" || "$SMTP_PASS" == "CHANGE_ME" ]]; then
  read -rsp "Purelymail password for admin@lumawen.com (Enter to skip; emails are then only logged): " SMTP_PASS
  echo
fi
MAIL_BACKEND=smtp
[[ -n "$SMTP_PASS" ]] || MAIL_BACKEND=console
umask 077
cat > "$ENV_FILE" <<ENV
# Written by deploy/setup-server.sh. Never commit this file.
LUMAWEN_ENV=production
LUMAWEN_DATABASE_URL=postgresql+psycopg://$DB:$DB_PASS@localhost:5432/$DB
LUMAWEN_PUBLIC_BASE_URL=https://$HOST
LUMAWEN_MAIL_BACKEND=$MAIL_BACKEND
LUMAWEN_MAIL_FROM=Lumawen <hello@lumawen.com>
LUMAWEN_SMTP_HOST=smtp.purelymail.com
LUMAWEN_SMTP_PORT=465
LUMAWEN_SMTP_USERNAME=admin@lumawen.com
LUMAWEN_SMTP_PASSWORD=$SMTP_PASS
ENV
chown "$APP_USER": "$ENV_FILE"
chmod 600 "$ENV_FILE"
umask 022

say "Web folder $WEB (owned by $APP_USER so deploys need no sudo)"
mkdir -p "$WEB"
chown -R "$APP_USER": "/var/www/$HOST"

say "nginx site $HOST"
SRC="$DIR/deploy/nginx"
BACKUP="/etc/nginx.bak-$(date +%F-%H%M)"
cp -a /etc/nginx "$BACKUP"
echo "Backed up /etc/nginx to $BACKUP"
install -m 644 "$SRC/lumawen-ratelimit.conf" /etc/nginx/conf.d/lumawen-ratelimit.conf
install -m 644 "$SRC/lumawen-proxy.conf" /etc/nginx/snippets/lumawen-proxy.conf
SITE="/etc/nginx/sites-available/$HOST"
if [[ "$STAGE" == "prod" && -f "$SITE" ]] && ! grep -q "lumawen-proxy.conf" "$SITE"; then
  echo "The live lumawen.com site has its own config. Add the /api/ blocks from"
  echo "$SRC/site.conf.template by hand (keep certbot's lines), then: nginx -t && systemctl reload nginx"
else
  NOINDEX='add_header X-Robots-Tag "noindex, nofollow" always;'
  [[ "$STAGE" == "prod" ]] && NOINDEX=""
  if [[ ! -f "$SITE" ]] || ! grep -q "managed by Certbot" "$SITE"; then
    sed -e "s/__HOST__/$HOST/g" -e "s/__PORT__/$PORT/g" -e "s/__STAGE__/$STAGE/g" \
      -e "s|__NOINDEX__|$NOINDEX|" "$SRC/site.conf.template" > "$SITE"
  fi
  ln -sf "$SITE" "/etc/nginx/sites-enabled/$HOST"
fi
nginx -t
systemctl reload nginx

if [[ ! -d "/etc/letsencrypt/live/$HOST" ]]; then
  say "HTTPS certificate for $HOST"
  certbot --nginx --non-interactive --agree-tos -m admin@lumawen.com --redirect "${CERT_NAMES[@]}"
fi

say "First deploy"
as_user bash -lc "bash '$DIR/deploy/deploy.sh' $STAGE"

cat <<DONE

Done. Next:
  1. Make your admin account:
       $DIR/api/.venv/bin/lumawen-admin create-admin admin@lumawen.com
  2. Check: curl -s https://$HOST/api/health
  3. Record the nginx change: cd ~/cobalt-server && ./sync.sh && git add -A && git commit -m "Add $HOST" && git push
DONE
