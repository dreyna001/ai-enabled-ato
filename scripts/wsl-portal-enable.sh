#!/usr/bin/env bash
# Enable portal auth on an existing WSL local deploy (API on 8001 + OIDC dev issuer).
if grep -q $'\r' "$0" 2>/dev/null; then
  exec /usr/bin/env bash <(sed 's/\r$//' "$0") "$@"
fi
set -euo pipefail

readonly INSTALL_DIR="/opt/ato-analyzer"
readonly CREDENTIALS_DIR="/etc/ato-analyzer/credentials"
readonly DATA_DIR="/var/ato-packages"
readonly STORAGE_BIND_TARGET="$INSTALL_DIR/data/ato-storage"
readonly RUNTIME_CONFIG_DEST="$INSTALL_DIR/runtime-config.json"
readonly OIDC_SECRET_PATH="$CREDENTIALS_DIR/oidc-client-secret"
readonly LOCAL_ENV_DEST="$CREDENTIALS_DIR/ato-local.env"
readonly DATABASE_DSN_CREDENTIAL_PATH="$CREDENTIALS_DIR/database-dsn"

err() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "  $*"; }

[[ "$(id -u)" -eq 0 ]] || err "Run as root (sudo bash scripts/wsl-portal-enable.sh)"
grep -Eiq 'microsoft|wsl' /proc/version || err "Run inside WSL"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCAL_ENV_SOURCE="${ATO_LOCAL_ENV_FILE:-$REPO_DIR/config.local.env}"

[[ -d "$INSTALL_DIR/venv" ]] || err "Missing WSL install at $INSTALL_DIR; run scripts/wsl-local-deploy.sh first"
[[ -f "$DATABASE_DSN_CREDENTIAL_PATH" ]] || err "Missing database DSN credential"

info "Installing portal runtime config"
cp "$REPO_DIR/deployment/config/runtime-config.wsl_portal.json" "$RUNTIME_CONFIG_DEST"
chown root:ato "$RUNTIME_CONFIG_DEST"
chmod 640 "$RUNTIME_CONFIG_DEST"

if [[ ! -f "$OIDC_SECRET_PATH" ]]; then
  umask 077
  openssl rand -hex 16 >"$OIDC_SECRET_PATH"
  chown root:root "$OIDC_SECRET_PATH"
  chmod 600 "$OIDC_SECRET_PATH"
  info "Created OIDC client secret credential"
else
  info "OIDC client secret credential already exists"
fi

install_local_env_file() {
  [[ -f "$LOCAL_ENV_SOURCE" ]] \
    || err "Missing $LOCAL_ENV_SOURCE. Copy config.local.env.example to config.local.env and set ATO_TEXT_MODEL_API_KEY=your-key"
  grep -Eq '^[[:space:]]*ATO_TEXT_MODEL_API_KEY=.+$' "$LOCAL_ENV_SOURCE" \
    || err "$LOCAL_ENV_SOURCE must set a non-empty ATO_TEXT_MODEL_API_KEY=... line"
  install -o root -g root -m 600 "$LOCAL_ENV_SOURCE" "$LOCAL_ENV_DEST"
  info "Installed local env secrets from $(basename "$LOCAL_ENV_SOURCE")"
}

install_local_env_file

bind_package_storage() {
  mkdir -p "$DATA_DIR/_tmp" "$STORAGE_BIND_TARGET"
  chown -R ato:ato "$DATA_DIR"
  chmod 750 "$DATA_DIR" "$DATA_DIR/_tmp"
  chown ato:ato "$STORAGE_BIND_TARGET"
  chmod 750 "$STORAGE_BIND_TARGET"
  if mountpoint -q "$STORAGE_BIND_TARGET"; then
    info "Storage bind mount already active: $STORAGE_BIND_TARGET"
    return 0
  fi
  mount --bind "$DATA_DIR" "$STORAGE_BIND_TARGET"
  info "Bound $DATA_DIR -> $STORAGE_BIND_TARGET"
}

info "Refreshing application package and migrations"
bash "$REPO_DIR/scripts/install.sh" --skip-nginx --skip-systemd

info "Restoring package storage ownership and bind mount"
bind_package_storage

info "Installing updated WSL API unit (OIDC credential mapping)"
cp "$REPO_DIR/deployment/systemd/ato-api.wsl-local.service" /etc/systemd/system/ato-api.service
systemctl daemon-reload

info "Running database migrations"
(
  cd "$INSTALL_DIR"
  ATO_DATABASE_DSN_FILE="$DATABASE_DSN_CREDENTIAL_PATH" \
    "$INSTALL_DIR/venv/bin/alembic" -c "$INSTALL_DIR/alembic.ini" upgrade head
)

systemctl restart ato-api.service
systemctl restart ato-synthetic-intake-worker.timer 2>/dev/null || true

info "Portal API enabled on http://127.0.0.1:8001 (dev OIDC + sessions + OpenAI text model)"
info "Start the UI from WSL: bash scripts/start-portal.sh"
info "Open http://localhost:5173"
