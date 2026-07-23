#!/usr/bin/env bash
# wsl-standup.sh -- Post-reboot WSL bring-up: storage bind, service restart, health wait.
if grep -q $'\r' "$0" 2>/dev/null; then
  exec /usr/bin/env bash <(sed 's/\r$//' "$0") "$@"
fi
set -euo pipefail
IFS=$'\n\t'

readonly INSTALL_DIR="/opt/ato-analyzer"
readonly DATA_DIR="/var/ato-packages"
readonly STORAGE_BIND_TARGET="$INSTALL_DIR/data/ato-storage"
readonly API_LOOPBACK_URL="${API_LOOPBACK_URL:-http://127.0.0.1:8001}"
readonly API_STARTUP_TIMEOUT_SECONDS="${API_STARTUP_TIMEOUT_SECONDS:-60}"
readonly REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

RUN_UPGRADE=false

usage() {
    cat <<'EOF'
Usage: wsl-standup.sh [options]

Bring up the WSL local stack after reboot or idle shutdown:
  - bind /var/ato-packages to application storage
  - restart API and worker units
  - wait for API liveness

Options:
  --upgrade    Run scripts/upgrade.sh before restarting services
  -h, --help   Show this help

After this script completes, start the portal UI separately:
  bash scripts/start-portal.sh
EOF
}

err() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "  $*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --upgrade) RUN_UPGRADE=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) err "Unknown argument: $1" ;;
    esac
done

require_wsl() {
    grep -Eiq 'microsoft|wsl' /proc/version 2>/dev/null || err "Run inside WSL"
}

require_root() {
    [[ "$(id -u)" -eq 0 ]] || err "Run as root (sudo bash scripts/wsl-standup.sh)"
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
    err "API did not become ready at $live_url (check journalctl -u ato-api -n 100 --no-pager)"
}

echo "=== ATO WSL Standup ==="
echo ""

require_wsl
require_root
[[ -d "$INSTALL_DIR" ]] || err "Missing install tree: $INSTALL_DIR (run wsl-local-deploy.sh first)"

if [[ "$RUN_UPGRADE" == "true" ]]; then
    info "Refreshing installed package bytes from repository"
    bash "$REPO_DIR/scripts/upgrade.sh"
fi

install -d -o ato -g ato -m 0750 "$DATA_DIR"
mkdir -p "$DATA_DIR/_tmp"
if ! mountpoint -q "$STORAGE_BIND_TARGET" 2>/dev/null; then
    mount --bind "$DATA_DIR" "$STORAGE_BIND_TARGET"
    info "Bound $DATA_DIR -> $STORAGE_BIND_TARGET"
else
    info "Storage bind mount already active: $STORAGE_BIND_TARGET"
fi

systemctl restart ato-api.service
systemctl restart ato-synthetic-intake-worker.timer
if systemctl is-enabled --quiet ato-analyzer-worker.service 2>/dev/null; then
    systemctl restart ato-analyzer-worker.service
    info "Restarted ato-analyzer-worker.service"
fi
info "Restarted ato-api.service and intake worker timer"

wait_for_api_loopback

echo ""
echo "WSL standup complete."
echo "  API:    ${API_LOOPBACK_URL}/health/live"
echo "  Portal: bash scripts/start-portal.sh  (then open http://localhost:5173)"
echo "  Logs:   journalctl -u ato-api -f"
