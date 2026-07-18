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

TEXT_MODEL_MODE="${ATO_WSL_PORTAL_TEXT_MODEL:-openai}"

usage() {
  cat <<'EOF'
Usage: wsl-portal-enable.sh [options]

Enable dev OIDC, portal sessions, and text-model settings on an existing WSL
local deploy (API on 8001).

Options:
  --openai     Use OpenAI-compatible text model (default)
  --bedrock    Use AWS Bedrock text model (no OpenAI API key)
  -h, --help   Show this help

Environment:
  ATO_LOCAL_ENV_FILE           Source env file (default: <repo>/config.local.env)
  ATO_WSL_PORTAL_TEXT_MODEL    openai or bedrock (default: openai)
EOF
}

err() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "  $*"; }
warn() { echo "WARN: $*" >&2; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --openai)
      TEXT_MODEL_MODE="openai"
      shift
      ;;
    --bedrock)
      TEXT_MODEL_MODE="bedrock"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      err "Unknown argument: $1"
      ;;
  esac
done

case "$TEXT_MODEL_MODE" in
  openai|bedrock) ;;
  *) err "ATO_WSL_PORTAL_TEXT_MODEL must be openai or bedrock (got: $TEXT_MODEL_MODE)" ;;
esac

[[ "$(id -u)" -eq 0 ]] || err "Run as root (sudo bash scripts/wsl-portal-enable.sh)"
grep -Eiq 'microsoft|wsl' /proc/version || err "Run inside WSL"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCAL_ENV_SOURCE="${ATO_LOCAL_ENV_FILE:-$REPO_DIR/config.local.env}"

[[ -d "$INSTALL_DIR/venv" ]] || err "Missing WSL install at $INSTALL_DIR; run scripts/wsl-local-deploy.sh first"
[[ -f "$DATABASE_DSN_CREDENTIAL_PATH" ]] || err "Missing database DSN credential"

select_runtime_config() {
  case "$TEXT_MODEL_MODE" in
    openai)
      echo "$REPO_DIR/deployment/config/runtime-config.wsl_portal.json"
      ;;
    bedrock)
      echo "$REPO_DIR/deployment/config/runtime-config.wsl_portal.bedrock.json"
      ;;
  esac
}

install_openai_local_env_file() {
  if [[ -f "$LOCAL_ENV_DEST" ]] && ! [[ -f "$LOCAL_ENV_SOURCE" ]]; then
    info "Keeping existing $LOCAL_ENV_DEST (no config.local.env in repo)"
    return 0
  fi
  [[ -f "$LOCAL_ENV_SOURCE" ]] \
    || err "Missing $LOCAL_ENV_SOURCE. Copy config.local.env.example to config.local.env and set ATO_TEXT_MODEL_API_KEY=your-key"
  grep -Eq '^[[:space:]]*ATO_TEXT_MODEL_API_KEY=.+$' "$LOCAL_ENV_SOURCE" \
    || err "$LOCAL_ENV_SOURCE must set a non-empty ATO_TEXT_MODEL_API_KEY=... line"
  install -o root -g root -m 600 "$LOCAL_ENV_SOURCE" "$LOCAL_ENV_DEST"
  info "Installed local env secrets from $(basename "$LOCAL_ENV_SOURCE")"
}

local_env_has_assignments() {
  [[ -f "$LOCAL_ENV_SOURCE" ]] || return 1
  grep -Eq '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=.+$' "$LOCAL_ENV_SOURCE"
}

install_bedrock_local_env_file() {
  if local_env_has_assignments; then
    install -o root -g root -m 600 "$LOCAL_ENV_SOURCE" "$LOCAL_ENV_DEST"
    info "Installed AWS/local env secrets from $(basename "$LOCAL_ENV_SOURCE")"
    return 0
  fi
  if [[ -f "$LOCAL_ENV_DEST" ]]; then
    info "Keeping existing $LOCAL_ENV_DEST"
    return 0
  fi
  warn "No KEY=VALUE assignments in $LOCAL_ENV_SOURCE; skipping ato-local.env install"
  warn "Portal OIDC works without it. For Bedrock calls, add AWS_PROFILE or AWS_ACCESS_KEY_ID to config.local.env and rerun."
}

install_wsl_analyzer_worker_unit() {
  local worker_src="$REPO_DIR/deployment/systemd/ato-analyzer-worker.wsl-local.service"
  cp "$worker_src" /etc/systemd/system/ato-analyzer-worker.service
  systemctl daemon-reload
  info "Installed WSL analyzer worker unit (model-assisted + deterministic runs)"
}

start_wsl_analyzer_worker() {
  systemctl enable ato-analyzer-worker.service
  systemctl restart ato-analyzer-worker.service
  info "Started ato-analyzer-worker.service"
}

warn_bedrock_credentials_missing() {
  if [[ "$TEXT_MODEL_MODE" != "bedrock" ]]; then
    return 0
  fi
  if [[ -f "$LOCAL_ENV_DEST" ]] && grep -Eq '^[[:space:]]*(AWS_PROFILE|AWS_ACCESS_KEY_ID)=' "$LOCAL_ENV_DEST"; then
    return 0
  fi
  if local_env_has_assignments && grep -Eq '^[[:space:]]*(AWS_PROFILE|AWS_ACCESS_KEY_ID)=' "$LOCAL_ENV_SOURCE"; then
    return 0
  fi
  warn "Bedrock is configured but no AWS_PROFILE or AWS_ACCESS_KEY_ID found in config.local.env."
  warn "Add AWS credentials and rerun this script. LLM paths (targeted runs, chat, intake normalize) will fail until then."
}

install_bedrock_dependencies() {
  info "Ensuring Bedrock dependencies are installed in service venv"
  "$INSTALL_DIR/venv/bin/pip" install "$INSTALL_DIR[bedrock]" \
    || err "Failed to install Bedrock dependencies in $INSTALL_DIR/venv"
}

RUNTIME_CONFIG_SRC="$(select_runtime_config)"
[[ -f "$RUNTIME_CONFIG_SRC" ]] || err "Missing runtime config: $RUNTIME_CONFIG_SRC"

info "Installing portal runtime config ($(basename "$RUNTIME_CONFIG_SRC"))"
cp "$RUNTIME_CONFIG_SRC" "$RUNTIME_CONFIG_DEST"
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

if [[ "$TEXT_MODEL_MODE" == "openai" ]]; then
  install_openai_local_env_file
else
  install_bedrock_local_env_file
  install_bedrock_dependencies
fi

bind_package_storage() {
  install -d -o root -g root -m 0755 "$(dirname "$STORAGE_BIND_TARGET")"
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

if [[ "$TEXT_MODEL_MODE" == "bedrock" ]]; then
  install_bedrock_dependencies
fi

info "Restoring package storage ownership and bind mount"
bind_package_storage

info "Installing updated WSL API unit (OIDC credential mapping)"
cp "$REPO_DIR/deployment/systemd/ato-api.wsl-local.service" /etc/systemd/system/ato-api.service
cp "$REPO_DIR/deployment/systemd/ato-synthetic-intake-worker.service" \
  /etc/systemd/system/ato-synthetic-intake-worker.service
install_wsl_analyzer_worker_unit
systemctl daemon-reload

info "Running database migrations"
(
  cd "$INSTALL_DIR"
  ATO_DATABASE_DSN_FILE="$DATABASE_DSN_CREDENTIAL_PATH" \
    "$INSTALL_DIR/venv/bin/alembic" -c "$INSTALL_DIR/alembic.ini" upgrade head
)

systemctl restart ato-api.service
systemctl restart ato-synthetic-intake-worker.timer 2>/dev/null || true
start_wsl_analyzer_worker
warn_bedrock_credentials_missing

case "$TEXT_MODEL_MODE" in
  openai)
    info "Portal API enabled on http://127.0.0.1:8001 (dev OIDC + sessions + OpenAI text model)"
    info "Analyzer worker enabled for targeted/model-assisted runs (use Start Targeted Run in portal)"
    ;;
  bedrock)
    info "Portal API enabled on http://127.0.0.1:8001 (dev OIDC + sessions + AWS Bedrock text model)"
    info "Analyzer worker enabled for targeted/model-assisted runs (use Start Targeted Run in portal)"
    ;;
esac
info "Start the UI from WSL: bash scripts/start-portal.sh"
info "Open http://localhost:5173"
