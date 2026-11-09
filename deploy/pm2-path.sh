# shellcheck shell=bash
# Put pm2 (and uv) on PATH for non-interactive shells: sudo -u, ssh 'cmd', bash -c.
# Sourced by deploy.sh and setup-server.sh. Login shells find pm2 through nvm's default
# alias or ~/.bashrc, which these shells skip, so also look where npm/nvm install it.
export PATH="$HOME/.local/bin:$PATH"
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
if ! command -v pm2 >/dev/null 2>&1 && [[ -s "$NVM_DIR/nvm.sh" ]]; then
  _lumawen_opts="$-"
  set +eu
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh" >/dev/null 2>&1 || true
  [[ "$_lumawen_opts" == *e* ]] && set -e
  [[ "$_lumawen_opts" == *u* ]] && set -u
  unset _lumawen_opts
fi
if ! command -v pm2 >/dev/null 2>&1; then
  # Newest nvm-installed node that has pm2, then common npm global locations.
  # shellcheck disable=SC2012  # nvm version dirs are plain names like v22.11.0
  for _lumawen_bin in $(ls -d "$NVM_DIR"/versions/node/*/bin 2>/dev/null | sort -rV) \
    "$HOME/.npm-global/bin" "$HOME/.local/share/pnpm" /usr/local/bin /usr/bin; do
    if [[ -x "$_lumawen_bin/pm2" ]]; then
      export PATH="$_lumawen_bin:$PATH"
      break
    fi
  done
  unset _lumawen_bin
fi
