#!/usr/bin/env bash
# upgrade.sh -- Bounded safe upgrade: drain workers, refresh package, migrate, restart API.
# Does not enable worker units or activate nginx automatically.
set -euo pipefail
IFS=$'\n\t'

readonly INSTALL_DIR="/opt/ato-analyzer"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

RUN_MIGRATE=true
RUN_SMOKE=false
RESTART_API=true
DRY_RUN=false
EXPECTED_MIGRATION_HEAD="20260717_0013"

usage() {
    cat <<'EOF'
Usage: upgrade.sh [options]

Drain workers, reinstall package bytes from the repository, optionally migrate,
and restart ato-api.service when it was active. Worker units remain disabled.

Options:
  --no-migrate           Skip alembic upgrade head
  --smoke                Run scripts/smoke_service_chain.sh after restart
  --no-restart           Refresh files without restarting ato-api.service
  --dry-run              Validate upgrade contract without host mutations
  -h, --help             Show this help
EOF
}

err() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "  $*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-migrate) RUN_MIGRATE=false; shift ;;
        --smoke) RUN_SMOKE=true; shift ;;
        --no-restart) RESTART_API=false; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) err "Unknown argument: $1" ;;
    esac
done

if [[ "$DRY_RUN" == "true" && "$RUN_SMOKE" == "true" ]]; then
    err "--dry-run cannot be combined with --smoke"
fi

run_upgrade_dry_run() {
    info "Dry-run mode: validating upgrade contract without host mutations"
    bash "$SCRIPT_DIR/install.sh" --dry-run
    [[ -f "$SCRIPT_DIR/drain_workers.sh" ]] || err "Missing drain_workers.sh"
    [[ -f "$SCRIPT_DIR/verify_backup_contract.sh" ]] || err "Missing verify_backup_contract.sh"
    info "Upgrade dry-run contract satisfied"
    info "Live upgrade still requires explicit install.sh --migrate/--start and customer backup evidence"
}

if [[ "$DRY_RUN" == "true" ]]; then
    run_upgrade_dry_run
    echo "Upgrade dry-run complete."
    exit 0
fi

[[ "$(id -u)" -eq 0 ]] || err "Run as root (sudo)"
[[ -d "$INSTALL_DIR" ]] || err "Missing install tree: $INSTALL_DIR (run install.sh first)"

api_was_active=false
if systemctl is-active --quiet ato-api.service; then
    api_was_active=true
fi

info "Verifying backup contract prerequisites (fail-safe; HS-008 may block production claims)"
bash "$SCRIPT_DIR/verify_backup_contract.sh" --pre-upgrade || err "Backup contract verification failed"

info "Draining workers"
bash "$SCRIPT_DIR/drain_workers.sh"

install_args=()
if [[ "$RUN_MIGRATE" == "true" ]]; then
    install_args+=(--migrate)
fi
if [[ "$api_was_active" == "true" && "$RESTART_API" == "true" ]]; then
    install_args+=(--start)
fi
if [[ "$RUN_SMOKE" == "true" ]]; then
    if [[ "$RESTART_API" != "true" ]]; then
        err "--smoke requires API restart; omit --no-restart"
    fi
    install_args+=(--smoke)
fi

# WSL local deploys skip nginx and production systemd units (see wsl-local-deploy.sh).
if grep -Eiq 'microsoft|wsl' /proc/version 2>/dev/null; then
    install_args+=(--skip-nginx --skip-systemd)
    info "WSL detected; passing --skip-nginx --skip-systemd to install.sh"
elif [[ ! -d /etc/nginx/conf.d ]]; then
    install_args+=(--skip-nginx)
    info "No /etc/nginx/conf.d; passing --skip-nginx to install.sh"
fi

info "Refreshing installed package bytes"
bash "$SCRIPT_DIR/install.sh" "${install_args[@]}"

if grep -Eiq 'microsoft|wsl' /proc/version 2>/dev/null; then
    local_api_unit="$REPO_DIR/deployment/systemd/ato-api.wsl-local.service"
    [[ -f "$local_api_unit" ]] || err "Missing WSL API unit: $local_api_unit"
    cp "$local_api_unit" /etc/systemd/system/ato-api.service
    for unit in ato-synthetic-intake-worker.service ato-synthetic-intake-worker.timer; do
        if [[ -f "$REPO_DIR/deployment/systemd/$unit" ]]; then
            cp "$REPO_DIR/deployment/systemd/$unit" "/etc/systemd/system/$unit"
        fi
    done
    if [[ -f "$REPO_DIR/deployment/systemd/ato-analyzer-worker.wsl-local.service" ]]; then
        cp "$REPO_DIR/deployment/systemd/ato-analyzer-worker.wsl-local.service" \
            /etc/systemd/system/ato-analyzer-worker.service
    fi
    systemctl daemon-reload || err "Failed to reload systemd after WSL unit restore"
    info "Restored WSL systemd units (API on 8001, /opt runtime config)"
    storage_bind="$INSTALL_DIR/data/ato-storage"
    if [[ -d /var/ato-packages ]] && ! mountpoint -q "$storage_bind" 2>/dev/null; then
        mount --bind /var/ato-packages "$storage_bind" \
            || warn "Failed to bind-mount /var/ato-packages -> $storage_bind"
    fi
fi

info "Upgrade complete; worker units remain disabled until explicitly enabled"
