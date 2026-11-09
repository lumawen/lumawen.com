#!/usr/bin/env bash
# One-time server setup for a Lumawen stage on cobalt. Run over ssh -t with sudo:
#   sudo bash ~/lumawen-test/deploy/setup-server.sh test
#   sudo bash ~/lumawen-prod/deploy/setup-server.sh prod
#
# It creates the Postgres database and user, writes api/.env (asking for the Purelymail app
# password, hidden), adds the nginx site and its HTTPS certificate, then runs deploy.sh as you. Safe to run again: it never rewrites an existing api/.env, and it puts
# /etc/nginx back as it was if the new config doesn't pass nginx -t.
#
# Prod (lumawen.com) replaces the hand-made static-site nginx config with
# deploy/nginx/prod.conf.template, reusing the existing certificate, and from then on deploy.sh
# publishes the site from the repo (update-site.sh and site zips are no longer used).
#
# UNDO:
#   pm2 delete lumawen-api-test lumawen-mail-test && pm2 save
#   sudo rm /etc/nginx/sites-enabled/test.lumawen.com /etc/nginx/sites-available/test.lumawen.com
#   sudo nginx -t && sudo systemctl reload nginx
#   sudo certbot delete --cert-name test.lumawen.com
#   sudo -u postgres psql -c 'DROP DATABASE lumawen_test' -c 'DROP ROLE lumawen_test'
#   rm -rf ~/lumawen-test; sudo rm -rf /var/www/test.lumawen.com
# UNDO prod: pm2 delete lumawen-api-prod lumawen-mail-prod && pm2 save, put back
#   /etc/nginx/sites-available/lumawen.com from the /etc/nginx.bak-* folder this printed and reload,
#   then (optional) drop the lumawen_prod database and role as above and rm -rf ~/lumawen-prod.
set -euo pipefail

STAGE="${1:-}"
case "$STAGE" in
  test) HOST="test.lumawen.com"; PORT=3041; TEMPLATE=site.conf.template ;;
  prod) HOST="lumawen.com"; PORT=3040; TEMPLATE=prod.conf.template ;;
  *) echo "usage: sudo bash setup-server.sh test|prod" >&2; exit 2 ;;
esac
DB="lumawen_$STAGE"
[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
APP_USER="${SUDO_USER:-}"
if [[ -z "$APP_USER" || "$APP_USER" == "root" ]]; then
  echo "Run this with sudo from your normal account (the one that owns the checkout), not from a root shell." >&2
  exit 1
fi
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
DIR="$APP_HOME/lumawen-$STAGE"
REPO="https://github.com/lumawen/lumawen.com.git"
WEB="/var/www/$HOST/html"
ENV_FILE="$DIR/api/.env"
say() { printf '\n==> %s\n' "$*"; }
# Run a command as the normal user with pm2 (nvm) and uv (~/.local/bin) on PATH.
PATH_HELPER="$(dirname "$(readlink -f "$0")")/pm2-path.sh"
[[ -r "$PATH_HELPER" ]] || { echo "Missing $PATH_HELPER: run git -C ~/lumawen-$STAGE pull first." >&2; exit 1; }
as_user() {
  sudo -u "$APP_USER" -H bash -c '. "$1"; '"$1" _ "$PATH_HELPER"
}

say "Checking tools"
for tool in git psql nginx certbot openssl rsync curl ss; do
  command -v "$tool" >/dev/null || { echo "Missing: $tool" >&2; exit 1; }
done
as_user 'command -v pm2 && command -v node' >/dev/null || {
  echo "Missing: pm2 or node (for $APP_USER). Check: which pm2 node" >&2
  exit 1
}
if ss -ltnH "sport = :$PORT" | grep -q .; then
  if ! as_user 'pm2 jlist' | grep -q "\"name\":\"lumawen-api-$STAGE\""; then
    echo "Port $PORT is already used by something else:" >&2
    ss -ltnp "sport = :$PORT" >&2
    exit 1
  fi
fi
if ! as_user 'command -v uv' >/dev/null; then
  say "Installing uv (Python package manager) for $APP_USER"
  as_user 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi

say "Getting the code into $DIR"
if [[ -d "$DIR/.git" ]]; then
  as_user "git -C '$DIR' fetch --quiet origin main && git -C '$DIR' checkout --quiet --detach origin/main"
else
  as_user "git clone --quiet '$REPO' '$DIR'"
fi

say "Postgres database $DB"
DB_PASS=""
if [[ -f "$ENV_FILE" ]]; then
  DB_PASS="$(sed -n "s#^LUMAWEN_DATABASE_URL=postgresql+psycopg://$DB:\([0-9a-f]*\)@.*#\1#p" "$ENV_FILE")"
  [[ -n "$DB_PASS" ]] || {
    echo "$ENV_FILE exists but has no $DB database URL; fix it by hand or move it away." >&2
    exit 1
  }
else
  DB_PASS="$(openssl rand -hex 24)"  # hex: safe in a URL and in SQL without escaping
fi
# The password goes in on stdin, so it never shows up in ps or in sudo's log.
if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB'" | grep -q 1; then
  VERB=ALTER
else
  VERB=CREATE
fi
sudo -u postgres psql -q -v ON_ERROR_STOP=1 >/dev/null <<SQL
$VERB ROLE $DB WITH LOGIN PASSWORD '$DB_PASS';
SQL
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB'" | grep -q 1; then
  sudo -u postgres createdb -O "$DB" "$DB"
fi

if [[ -f "$ENV_FILE" ]]; then
  say "Keeping your existing $ENV_FILE"
else
  say "Writing $ENV_FILE"
  if [[ ! -t 0 ]]; then
    echo "Run this over 'ssh -t' so it can ask for the Purelymail password." >&2
    exit 1
  fi
  while true; do
    read -rsp "Purelymail app password for admin@lumawen.com (Enter to skip; emails are then only logged): " SMTP_PASS
    echo
    if [[ "$SMTP_PASS" =~ [[:space:]\'\"\\\$\#] ]]; then
      echo "That password has a space or one of ' \" \\ \$ # in it, which .env files mangle. Make a new app password." >&2
      continue
    fi
    break
  done
  MAIL_BACKEND=smtp
  [[ -n "$SMTP_PASS" ]] || MAIL_BACKEND=console
  TMP="$(mktemp "$DIR/api/.env.tmp.XXXXXX.env")"  # *.env is gitignored
  trap 'rm -f "$TMP"' EXIT
  chmod 600 "$TMP"
  cat > "$TMP" <<ENV
# Written by deploy/setup-server.sh. Never commit this file. Edit it, then:
#   pm2 restart lumawen-api-$STAGE lumawen-mail-$STAGE
LUMAWEN_ENV=production
LUMAWEN_DATABASE_URL=postgresql+psycopg://$DB:$DB_PASS@localhost:5432/$DB
LUMAWEN_PUBLIC_BASE_URL=https://$HOST
LUMAWEN_MAIL_BACKEND=$MAIL_BACKEND
LUMAWEN_MAIL_FROM='Lumawen <hello@lumawen.com>'
LUMAWEN_SMTP_HOST=smtp.purelymail.com
LUMAWEN_SMTP_PORT=465
LUMAWEN_SMTP_USERNAME=admin@lumawen.com
LUMAWEN_SMTP_PASSWORD='$SMTP_PASS'
ENV
  chown "$APP_USER": "$TMP"
  mv -f "$TMP" "$ENV_FILE"
  trap - EXIT
fi

say "Web folder $WEB"
if [[ "$STAGE" == prod && -d "$WEB" && ! -f "$WEB/.deployed-from-repo" ]]; then
  # First prod setup: deploys replace the hand-uploaded site with the repo's site/, so keep it.
  cp -a "$WEB" "$WEB.bak-$(date +%F-%H%M%S)"
  echo "Kept a copy of the current site in $WEB.bak-*"
fi
mkdir -p "$WEB"
chown -R "$APP_USER": "/var/www/$HOST"  # so deploys need no sudo

say "nginx site $HOST"
SRC="$DIR/deploy/nginx"
SITE="/etc/nginx/sites-available/$HOST"
BACKUP="/etc/nginx.bak-$(date +%F-%H%M%S)"
cp -a /etc/nginx "$BACKUP"
echo "Backed up /etc/nginx to $BACKUP"
install -m 644 "$SRC/lumawen-ratelimit.conf" /etc/nginx/conf.d/lumawen-ratelimit.conf
install -m 644 "$SRC/lumawen-proxy.conf" /etc/nginx/snippets/lumawen-proxy.conf
install -m 644 "$SRC/lumawen-headers.conf" /etc/nginx/snippets/lumawen-headers.conf
if [[ "$STAGE" == prod ]]; then
  CERT=/etc/letsencrypt/live/$HOST/fullchain.pem
  [[ -f "$CERT" ]] || { echo "No certificate at $CERT" >&2; exit 1; }
  NAMES="$HOST"
  if openssl x509 -in "$CERT" -noout -ext subjectAltName | grep -q "DNS:www.$HOST"; then
    NAMES="$HOST www.$HOST"
  else
    echo "Note: the certificate doesn't cover www.$HOST, so www isn't served; only $HOST is." >&2
  fi
  # Another enabled file serving this name would win over ours and leave /api/ unproxied.
  OTHERS="$(grep -lsE "server_name[^;]*[[:space:]]${HOST}[[:space:];]" /etc/nginx/sites-enabled/* \
    | grep -v "^/etc/nginx/sites-enabled/$HOST\$" || true)"
  if [[ -n "$OTHERS" ]]; then
    echo "These enabled nginx files also serve $HOST; disable them first:" >&2
    echo "$OTHERS" >&2
    exit 1
  fi
  if [[ -f "$SITE" ]] && ! grep -q "Lumawen prod" "$SITE"; then
    echo "Replacing the hand-made $SITE (old copy: $BACKUP/sites-available/$HOST)"
  fi
  sed -e "s/__PORT__/$PORT/g" -e "s/__NAMES__/$NAMES/g" "$SRC/$TEMPLATE" > "$SITE"
  if [[ "$NAMES" == "$HOST" ]]; then
    sed -i '/# BEGIN www/,/# END www/d' "$SITE"
  fi
elif [[ ! -f "$SITE" ]] || ! grep -q "managed by Certbot" "$SITE"; then
  sed -e "s/__HOST__/$HOST/g" -e "s/__PORT__/$PORT/g" -e "s/__STAGE__/$STAGE/g" \
    "$SRC/$TEMPLATE" > "$SITE"
elif ! grep -q "lumawen-headers.conf" "$SITE"; then
  # Certbot has edited this file, so keep it and add the security headers to each server block.
  sed -i '/add_header X-Robots-Tag/a\    include /etc/nginx/snippets/lumawen-headers.conf;' "$SITE"
fi
ln -sf "$SITE" "/etc/nginx/sites-enabled/$HOST"
if ! nginx -t; then
  echo "nginx -t failed: putting /etc/nginx back from $BACKUP" >&2
  rm -rf /etc/nginx
  cp -a "$BACKUP" /etc/nginx
  nginx -t
  exit 1
fi
systemctl reload nginx

if [[ "$STAGE" == test ]] && ! grep -q "managed by Certbot" "$SITE"; then
  say "HTTPS certificate for $HOST"
  certbot --nginx --non-interactive --agree-tos --keep-until-expiring -m admin@lumawen.com \
    --redirect -d "$HOST"
fi

say "First deploy"
as_user "bash '$DIR/deploy/deploy.sh' $STAGE"

cat <<DONE

Done. Next:
  1. Make your admin account:
       $DIR/api/.venv/bin/lumawen-admin create-admin admin@lumawen.com
  2. Check: curl -s https://$HOST/api/health
  3. Record the nginx change: cd ~/cobalt-server && ./sync.sh && git add -A && git commit -m "Add $HOST" && git push
DONE
