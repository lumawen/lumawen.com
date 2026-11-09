#!/usr/bin/env bash
# Deploy the latest main to a stage. Run as your normal user (no sudo):
#   bash ~/lumawen-test/deploy/deploy.sh test
# Pulls main, installs dependencies, migrates the database, publishes the site files,
# restarts the API and email worker under pm2, and checks /api/health.
set -euo pipefail

STAGE="${1:-}"
case "$STAGE" in
  test) HOST="test.lumawen.com"; PORT=3041 ;;
  *) echo "usage: deploy.sh test" >&2; exit 2 ;;
esac
DIR="$HOME/lumawen-$STAGE"
WEB="/var/www/$HOST/html"
# shellcheck source=SCRIPTDIR/pm2-path.sh
. "$(dirname "$(readlink -f "$0")")/pm2-path.sh"
command -v pm2 >/dev/null || { echo "pm2 not found. Run: which pm2 and paste the result into the Lumawen thread." >&2; exit 1; }
say() { printf '\n==> %s\n' "$*"; }

cd "$DIR"
say "Updating code"
git fetch --quiet origin main
git checkout --quiet --detach origin/main
COMMIT="$(git rev-parse --short HEAD)"
echo "main is at $COMMIT"

cd api
[[ -f .env ]] || { echo "api/.env is missing: run setup-server.sh first." >&2; exit 1; }
say "Python dependencies"
uv sync --locked --no-dev --quiet
say "Database migrations"
uv run --no-sync alembic upgrade head
printf '{"commit": "%s", "built_at": "%s"}\n' "$COMMIT" "$(date -u +%FT%TZ)" > build-info.json

if [[ "$STAGE" == "test" ]]; then
  say "Site files -> $WEB"
  rsync -a --delete "$DIR/site/" "$WEB/"
fi

say "Restarting under pm2"
pm2 startOrReload "$DIR/deploy/ecosystem.config.cjs" --only "lumawen-api-$STAGE,lumawen-mail-$STAGE"
pm2 save --force >/dev/null

say "Health check"
for _ in $(seq 1 20); do
  if body="$(curl -fsS "http://127.0.0.1:$PORT/api/health" 2>/dev/null)"; then
    echo "$body"
    if grep -q '"database":"ok"' <<<"$body" && grep -q "\"commit\":\"$COMMIT\"" <<<"$body"; then
      echo "Deployed $COMMIT to $STAGE."
      exit 0
    fi
  fi
  sleep 1
done
echo "The API did not come up healthy. Logs: pm2 logs lumawen-api-$STAGE --lines 50" >&2
exit 1
