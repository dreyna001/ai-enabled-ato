#!/usr/bin/env bash
# wsl-local-reset.sh -- WSL dev-only reset: empty PostgreSQL data and package storage.
# Preserves credentials, runtime config, and installed package bytes under /opt.
if grep -q $'\r' "$0" 2>/dev/null; then
  exec /usr/bin/env bash <(sed 's/\r$//' "$0") "$@"
fi
set -euo pipefail
IFS=$'\n\t'

trap 'err "Failed at line ${LINENO}: ${BASH_COMMAND}"' ERR

readonly INSTALL_DIR="/opt/ato-analyzer"
readonly DATA_DIR="/var/ato-packages"
readonly STORAGE_BIND_TARGET="$INSTALL_DIR/data/ato-storage"
readonly DATABASE_DSN_CREDENTIAL_PATH="/etc/ato-analyzer/credentials/database-dsn"
readonly DB_NAME="${ATO_WSL_DB_NAME:-ato}"
readonly DB_USER="${ATO_WSL_DB_USER:-ato}"
readonly API_LOOPBACK_URL="${API_LOOPBACK_URL:-http://127.0.0.1:8001}"
readonly API_STARTUP_TIMEOUT_SECONDS="${API_STARTUP_TIMEOUT_SECONDS:-60}"

usage() {
    cat <<'EOF'
Usage: wsl-local-reset.sh [options]

WSL developer reset only. Stops API/workers, drops and recreates the local
PostgreSQL database, clears /var/ato-packages, reruns migrations, and restarts
WSL services. Does not remove credentials or reinstall /opt package bytes.

Options:
  --no-restart         Reset data only; do not restart services afterward
  -h, --help           Show this help

Environment overrides:
  ATO_WSL_DB_NAME      PostgreSQL database name (default: ato)
  ATO_WSL_DB_USER      PostgreSQL role name (default: ato)
EOF
}

err() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "  $*"; }

RESTART_SERVICES=true

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-restart)
            RESTART_SERVICES=false
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

require_wsl() {
    if [[ ! -r /proc/version ]]; then
        err "This script must run inside WSL"
    fi
    if ! grep -Eiq 'microsoft|wsl' /proc/version; then
        err "This script must run inside WSL (unexpected /proc/version)"
    fi
}

require_root() {
    [[ "$(id -u)" -eq 0 ]] || err "Run as root (sudo bash scripts/wsl-local-reset.sh)"
}

stop_services() {
    info "Stopping API and worker units"
    systemctl stop ato-api.service 2>/dev/null || true
    systemctl stop ato-analyzer-worker.service 2>/dev/null || true
    systemctl stop ato-synthetic-intake-worker.service 2>/dev/null || true
    systemctl stop ato-synthetic-intake-worker.timer 2>/dev/null || true
}

reset_database() {
    info "Recreating PostgreSQL database: ${DB_NAME}"
    sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = '${DB_NAME}'
  AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS ${DB_NAME};
CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};
SQL
}

clear_package_storage() {
    info "Clearing package storage under ${DATA_DIR}"
    install -d -o ato -g ato -m 0750 "$DATA_DIR"
    mkdir -p "$DATA_DIR/_tmp"
    chown -R ato:ato "$DATA_DIR"
    find "$DATA_DIR" -mindepth 1 -maxdepth 1 ! -name '_tmp' -exec rm -rf {} +
    find "$DATA_DIR/_tmp" -mindepth 1 -delete 2>/dev/null || true
    chmod 750 "$DATA_DIR" "$DATA_DIR/_tmp"
    if [[ -d "$STORAGE_BIND_TARGET" ]] && mountpoint -q "$STORAGE_BIND_TARGET" 2>/dev/null; then
        info "Storage bind mount remains active: ${STORAGE_BIND_TARGET}"
    fi
}

run_migrations() {
    [[ -x "$INSTALL_DIR/venv/bin/alembic" ]] || err "Alembic missing under $INSTALL_DIR"
    [[ -f "$DATABASE_DSN_CREDENTIAL_PATH" ]] \
        || err "Missing database DSN credential: $DATABASE_DSN_CREDENTIAL_PATH"
    info "Running database migrations"
    (
        cd "$INSTALL_DIR"
        ATO_DATABASE_DSN_FILE="$DATABASE_DSN_CREDENTIAL_PATH" \
            "$INSTALL_DIR/venv/bin/alembic" -c "$INSTALL_DIR/alembic.ini" upgrade head
    )
    info "Database migrations completed"
}

start_services() {
    if [[ -d /var/ato-packages ]] && ! mountpoint -q "$STORAGE_BIND_TARGET" 2>/dev/null; then
        mount --bind /var/ato-packages "$STORAGE_BIND_TARGET" \
            || err "Failed to bind-mount /var/ato-packages -> $STORAGE_BIND_TARGET"
    fi
    systemctl enable ato-api.service
    systemctl restart ato-api.service
    systemctl enable ato-synthetic-intake-worker.timer
    systemctl restart ato-synthetic-intake-worker.timer
    if systemctl is-enabled --quiet ato-analyzer-worker.service 2>/dev/null; then
        systemctl restart ato-analyzer-worker.service
    fi
    info "Restarted WSL services"
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

echo "=== ATO WSL Local Reset ==="
echo ""

require_wsl
require_root
[[ -d "$INSTALL_DIR" ]] || err "Missing install tree: $INSTALL_DIR (run wsl-local-deploy.sh first)"

stop_services
reset_database
clear_package_storage
run_migrations

if [[ "$RESTART_SERVICES" == "true" ]]; then
    start_services
    wait_for_api_loopback
else
    info "Skipped service restart (--no-restart)"
fi

echo ""
echo "WSL local reset complete."
echo "  Database: empty schema at head migration"
echo "  Storage:  cleared under $DATA_DIR"
echo "  API:      ${API_LOOPBACK_URL}/health/live"
echo ""
echo "Hard refresh the portal browser tab to clear any stale client session state."
