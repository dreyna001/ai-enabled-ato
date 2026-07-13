#!/usr/bin/env bash
# Enable portal auth on an existing WSL local deploy (API on 8001 + OIDC dev issuer).
set -euo pipefail

readonly INSTALL_DIR="/opt/ato-analyzer"
readonly CREDENTIALS_DIR="/etc/ato-analyzer/credentials"
readonly RUNTIME_CONFIG_DEST="$INSTALL_DIR/runtime-config.json"
readonly OIDC_SECRET_PATH="$CREDENTIALS_DIR/oidc-client-secret"
readonly DATABASE_DSN_CREDENTIAL_PATH="$CREDENTIALS_DIR/database-dsn"

err() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "  $*"; }

[[ "$(id -u)" -eq 0 ]] || err "Run as root (sudo bash scripts/wsl-portal-enable.sh)"
grep -Eiq 'microsoft|wsl' /proc/version || err "Run inside WSL"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

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

info "Refreshing application package and migrations"
bash "$REPO_DIR/scripts/install.sh" --skip-nginx --skip-systemd

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

info "Portal API enabled on http://127.0.0.1:8001 (dev OIDC + sessions)"
info "Start the UI from Windows: cd portal && npm install && npm run dev"
info "Open http://localhost:5173"
