#!/usr/bin/env bash
# Deploy the latest main to a stage. Run as your normal user (no sudo):
#   bash ~/lumawen-test/deploy/deploy.sh test
#   bash ~/lumawen-prod/deploy/deploy.sh prod
# Pulls main, installs dependencies, migrates the database, publishes the site files,
# restarts the API and email worker under pm2, and checks /api/health.
set -euo pipefail

STAGE="${1:-}"
case "$STAGE" in
  test) HOST="test.lumawen.com"; PORT=3041 ;;
  prod) HOST="lumawen.com"; PORT=3040 ;;
  *) echo "usage: deploy.sh test|prod" >&2; exit 2 ;;
esac
DIR="$HOME/lumawen-$STAGE"
WEB="/var/www/$HOST/html"
# shellcheck source=SCRIPTDIR/pm2-path.sh
. "$(dirname "$(readlink -f "$0")")/pm2-path.sh"
if ! command -v pm2 >/dev/null || ! command -v node >/dev/null; then
  echo "pm2 or node not found. Run: which pm2 node and paste the result into the Lumawen thread." >&2
  exit 1
fi
say() { printf '\n==> %s\n' "$*"; }

# Flutter is installed once into ~/flutter-<version> (no sudo; about 2 GB), pinned by
# app/FLUTTER_VERSION, and builds the dashboard into app/build/web.
build_dashboard() {
  local version flutter_home
  version="$(cat "$DIR/app/FLUTTER_VERSION")"
  flutter_home="$HOME/flutter-$version"
  if [[ ! -x "$flutter_home/bin/flutter" ]]; then
    for tool in git curl unzip xz; do
      command -v "$tool" >/dev/null || {
        echo "Flutter needs $tool: sudo apt install git curl unzip xz-utils" >&2
        exit 1
      }
    done
    echo "Installing Flutter $version into $flutter_home (once, a few minutes)"
    rm -rf "$flutter_home.tmp"
    git clone --quiet --depth 1 --branch "$version" https://github.com/flutter/flutter.git \
      "$flutter_home.tmp"
    "$flutter_home.tmp/bin/flutter" --disable-analytics >/dev/null
    rm -rf "$flutter_home"  # a broken earlier install
    mv "$flutter_home.tmp" "$flutter_home"
  fi
  (
    cd "$DIR/app"
    "$flutter_home/bin/flutter" pub get --enforce-lockfile >/dev/null
    "$flutter_home/bin/flutter" build web --release --base-href /dashboard/ \
      --no-web-resources-cdn --no-wasm-dry-run --no-pub >/dev/null
  )
  echo "built app/build/web"
}

cd "$DIR"
say "Updating code"
git fetch --quiet origin main
git checkout --quiet --detach origin/main
COMMIT="$(git rev-parse --short HEAD)"
echo "main is at $COMMIT"

[[ -f api/.env ]] || { echo "api/.env is missing: run setup-server.sh first." >&2; exit 1; }
# First, before anything on the server changes: the most likely step to fail.
say "Dashboard (Flutter web)"
build_dashboard

cd api
say "Python dependencies"
uv sync --locked --no-dev --quiet
say "Database migrations"
uv run --no-sync alembic upgrade head
printf '{"commit": "%s", "built_at": "%s"}\n' "$COMMIT" "$(date -u +%FT%TZ)" > build-info.json

say "Site files -> $WEB"
rsync -a --delete --exclude /dashboard/ "$DIR/site/" "$WEB/"
rsync -a --delete "$DIR/app/build/web/" "$WEB/dashboard/"
touch "$WEB/.deployed-from-repo"

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
