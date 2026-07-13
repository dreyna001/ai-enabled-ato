#!/usr/bin/env bash
# wsl-local-deploy.sh -- Install and start the implemented ATO API + synthetic
# intake worker inside WSL using production host paths (/opt, /etc, /var) and
# systemd. WSL-only; not a production release claim.
set -euo pipefail
IFS=$'\n\t'

trap 'err "Failed at line ${LINENO}: ${BASH_COMMAND}"' ERR

readonly INSTALL_DIR="/opt/ato-analyzer"
readonly CONFIG_DIR="/etc/ato-analyzer"
readonly DATA_DIR="/var/ato-packages"
readonly CREDENTIALS_DIR="$CONFIG_DIR/credentials"
readonly DATABASE_DSN_CREDENTIAL_PATH="$CREDENTIALS_DIR/database-dsn"
readonly AUDIT_HMAC_CREDENTIAL_PATH="$CREDENTIALS_DIR/audit-hmac-key"
readonly RUNTIME_CONFIG_DEST="$INSTALL_DIR/runtime-config.json"
readonly STORAGE_BIND_TARGET="$INSTALL_DIR/data/ato-storage"
readonly DB_NAME="${ATO_WSL_DB_NAME:-ato}"
readonly DB_USER="${ATO_WSL_DB_USER:-ato}"
readonly API_LOOPBACK_URL="${API_LOOPBACK_URL:-http://127.0.0.1:8001}"
readonly API_STARTUP_TIMEOUT_SECONDS="${API_STARTUP_TIMEOUT_SECONDS:-60}"

RUN_MIGRATE=true
START_SERVICES=true
RUN_SMOKE=true

usage() {
    cat <<'EOF'
Usage: wsl-local-deploy.sh [options]

Bootstrap PostgreSQL credentials, install the application tree with
scripts/install.sh, bind package storage to /var/ato-packages, install WSL
systemd units for the API and synthetic intake worker, migrate, start, and
smoke.

Options:
  --no-migrate         Skip alembic upgrade head
  --no-start           Install only; do not enable/start systemd units
  --no-smoke           Skip smoke checks (requires start unless --no-start)
  -h, --help           Show this help

Environment overrides:
  ATO_WSL_DB_NAME      PostgreSQL database name (default: ato)
  ATO_WSL_DB_USER      PostgreSQL role name (default: ato)
EOF
}

err() { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARN: $*" >&2; }
info() { echo "  $*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-migrate)
            RUN_MIGRATE=false
            shift
            ;;
        --no-start)
            START_SERVICES=false
            shift
            ;;
        --no-smoke)
            RUN_SMOKE=false
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

if [[ "$RUN_SMOKE" == "true" && "$START_SERVICES" != "true" ]]; then
    err "--no-smoke is required when using --no-start"
fi

require_wsl() {
    if [[ ! -r /proc/version ]]; then
        err "This script must run inside WSL"
    fi
    if ! grep -Eiq 'microsoft|wsl' /proc/version; then
        err "This script must run inside WSL (unexpected /proc/version)"
    fi
}

require_root() {
    [[ "$(id -u)" -eq 0 ]] || err "Run as root (sudo bash scripts/wsl-local-deploy.sh)"
}

require_systemd() {
    if ! command -v systemctl >/dev/null 2>&1; then
        err "systemctl not found; enable systemd in /etc/wsl.conf ([boot] systemd=true) and restart WSL"
    fi
    if ! systemctl is-system-running --wait >/dev/null 2>&1; then
        err "systemd is not running; enable it in /etc/wsl.conf and restart WSL"
    fi
}

install_os_packages() {
    if command -v apt-get >/dev/null 2>&1; then
        info "Installing OS packages (apt)"
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        apt-get install -y -qq \
            postgresql \
            postgresql-client \
            python3.12 \
            python3.12-venv \
            openssl \
            curl
        return 0
    fi
    err "Unsupported WSL distribution: install PostgreSQL 16+, Python 3.12+, openssl, and curl manually"
}

ensure_postgresql_running() {
    if systemctl list-unit-files postgresql.service >/dev/null 2>&1; then
        systemctl enable postgresql.service
        systemctl start postgresql.service
    elif systemctl list-unit-files "postgresql@*.service" >/dev/null 2>&1; then
        local unit=""
        unit="$(systemctl list-unit-files 'postgresql@*.service' --no-legend | awk 'NR==1 {print $1}')"
        [[ -n "$unit" ]] || err "Could not locate a postgresql systemd unit"
        systemctl enable "$unit"
        systemctl start "$unit"
    else
        err "PostgreSQL systemd unit not found after package install"
    fi
}

generate_token() {
    openssl rand -hex 32
}

write_credential_file() {
    local path="$1"
    local owner="$2"
    local mode="$3"
    local contents="$4"

    install -d -o root -g root -m 700 "$(dirname "$path")"
    umask 077
    printf '%s' "$contents" >"$path"
    chown "$owner" "$path"
    chmod "$mode" "$path"
}

bootstrap_postgresql() {
    local role_credential="$1"
    info "Ensuring PostgreSQL role and database exist"
    sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${DB_USER}') THEN
        CREATE ROLE ${DB_USER} LOGIN PASSWORD '${role_credential}';
    ELSE
        ALTER ROLE ${DB_USER} WITH LOGIN PASSWORD '${role_credential}';
    END IF;
END
\$\$;
SQL
    if sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
        info "Database exists: ${DB_NAME}"
    else
        sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"
        info "Created database: ${DB_NAME}"
    fi
}

bind_package_storage() {
    mkdir -p "$STORAGE_BIND_TARGET"
    chown ato:ato "$STORAGE_BIND_TARGET"
    chmod 750 "$STORAGE_BIND_TARGET"
    if mountpoint -q "$STORAGE_BIND_TARGET"; then
        info "Storage bind mount already active: $STORAGE_BIND_TARGET"
        return 0
    fi
    mount --bind "$DATA_DIR" "$STORAGE_BIND_TARGET"
    info "Bound $DATA_DIR -> $STORAGE_BIND_TARGET"
}

install_runtime_config() {
    local src="$REPO_DIR/deployment/config/runtime-config.wsl_local.json"
    [[ -f "$src" ]] || err "Missing WSL runtime config template: $src"
    cp "$src" "$RUNTIME_CONFIG_DEST"
    sed -i 's/\r$//' "$RUNTIME_CONFIG_DEST" 2>/dev/null || true
    chown root:ato "$RUNTIME_CONFIG_DEST"
    chmod 640 "$RUNTIME_CONFIG_DEST"
    info "Installed runtime config: $RUNTIME_CONFIG_DEST"
}

install_wsl_systemd_units() {
    local api_src="$REPO_DIR/deployment/systemd/ato-api.wsl-local.service"
    local worker_src="$REPO_DIR/deployment/systemd/ato-synthetic-intake-worker.service"
    local timer_src="$REPO_DIR/deployment/systemd/ato-synthetic-intake-worker.timer"
    cp "$api_src" /etc/systemd/system/ato-api.service
    cp "$worker_src" /etc/systemd/system/ato-synthetic-intake-worker.service
    cp "$timer_src" /etc/systemd/system/ato-synthetic-intake-worker.timer
    sed -i 's/\r$//' /etc/systemd/system/ato-api.service \
        /etc/systemd/system/ato-synthetic-intake-worker.service \
        /etc/systemd/system/ato-synthetic-intake-worker.timer 2>/dev/null || true
    systemctl daemon-reload
    info "Installed WSL systemd units (ato-api, synthetic intake worker + timer)"
}

run_migrations() {
    [[ -x "$INSTALL_DIR/venv/bin/alembic" ]] || err "Alembic missing under $INSTALL_DIR"
    (
        cd "$INSTALL_DIR"
        ATO_DATABASE_DSN_FILE="$DATABASE_DSN_CREDENTIAL_PATH" \
            "$INSTALL_DIR/venv/bin/alembic" -c "$INSTALL_DIR/alembic.ini" upgrade head
    )
    info "Database migrations completed"
}

start_services() {
    systemctl enable ato-api.service
    systemctl restart ato-api.service
    systemctl enable ato-synthetic-intake-worker.timer
    systemctl restart ato-synthetic-intake-worker.timer
    info "Started ato-api.service and ato-synthetic-intake-worker.timer"
}

wait_for_api_loopback() {
    local live_url="${API_LOOPBACK_URL%/}/health/live"
    local attempt=1
    local max_attempts=$((API_STARTUP_TIMEOUT_SECONDS / 2))
    info "Waiting for API liveness at $live_url"
    while (( attempt <= max_attempts )); do
        if curl -fsS --max-time 2 "$live_url" >/dev/null 2>&1; then
            info "API liveness OK (attempt $attempt/$max_attempts)"
            return 0
        fi
        sleep 2
        attempt=$((attempt + 1))
    done
    err "API did not become ready at $live_url within ${API_STARTUP_TIMEOUT_SECONDS}s"
}

run_smoke_checks() {
    local smoke_script="$REPO_DIR/scripts/smoke_service_chain.sh"
    [[ -f "$smoke_script" ]] || err "Missing smoke script: $smoke_script"
    chmod +x "$smoke_script"
    wait_for_api_loopback
    ALLOW_DEGRADED_READY=true API_BASE_URL="${API_LOOPBACK_URL}" bash "$smoke_script"
}

echo "=== ATO WSL Local Deploy ==="
echo ""

require_wsl
require_root
require_systemd

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "[1/8] Installing OS packages..."
install_os_packages
ensure_postgresql_running

echo "[2/8] Provisioning credentials..."
role_credential="$(generate_token)"
audit_key="$(generate_token)"
write_credential_file "$DATABASE_DSN_CREDENTIAL_PATH" "root:root" 600 \
    "postgresql+asyncpg://${DB_USER}:${role_credential}@127.0.0.1:5432/${DB_NAME}"
write_credential_file "$AUDIT_HMAC_CREDENTIAL_PATH" "root:root" 600 "$audit_key"
info "Wrote database DSN and audit HMAC credentials under $CREDENTIALS_DIR"
bootstrap_postgresql "$role_credential"

echo "[3/8] Installing application tree..."
bash "$REPO_DIR/scripts/install.sh" --skip-nginx --skip-systemd

echo "[4/8] Installing runtime config and storage bind..."
install_runtime_config
bind_package_storage

echo "[5/8] Installing WSL systemd units..."
install_wsl_systemd_units

if [[ "$RUN_MIGRATE" == "true" ]]; then
    echo "[6/8] Running database migrations..."
    run_migrations
else
    echo "[6/8] Skipped database migrations"
fi

if [[ "$START_SERVICES" == "true" ]]; then
    echo "[7/8] Starting services..."
    start_services
else
    echo "[7/8] Skipped service start"
fi

if [[ "$RUN_SMOKE" == "true" ]]; then
    echo "[8/8] Running smoke checks..."
    run_smoke_checks
else
    echo "[8/8] Skipped smoke checks"
fi

echo ""
echo "WSL local deploy complete."
echo "  API:      ${API_LOOPBACK_URL}/health/live"
echo "  Config:   $RUNTIME_CONFIG_DEST"
echo "  Storage:  $DATA_DIR (bound to dev_local data/ato-storage)"
echo "  Logs:     journalctl -u ato-api -f"
echo "  Worker:   systemctl start ato-synthetic-intake-worker.service"
echo ""
echo "Readiness may stay HTTP 503 while HS-001 keeps the authority manifest draft."
