#!/usr/bin/env bash
# upgrade.sh -- Bounded safe upgrade: drain workers, refresh package, migrate, restart API.
# Does not enable worker units or activate nginx automatically.
set -euo pipefail
IFS=$'\n\t'

readonly INSTALL_DIR="/opt/ato-analyzer"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly WSL_API_LOOPBACK_URL="${API_LOOPBACK_URL:-http://127.0.0.1:8001}"
readonly WSL_API_STARTUP_TIMEOUT_SECONDS="${API_STARTUP_TIMEOUT_SECONDS:-60}"

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
warn() { echo "WARN: $*" >&2; }
info() { echo "  $*"; }

is_wsl() {
    grep -Eiq 'microsoft|wsl' /proc/version 2>/dev/null
}

wait_for_wsl_api_loopback() {
    local live_url="${WSL_API_LOOPBACK_URL%/}/health/live"
    local attempt=1
    local max_attempts=$((WSL_API_STARTUP_TIMEOUT_SECONDS / 2))
    info "Waiting for API liveness at $live_url"
    while (( attempt <= max_attempts )); do
        if curl -fsS --max-time 2 "$live_url" >/dev/null 2>&1; then
            info "API liveness OK (attempt $attempt/$max_attempts)"
            return 0
        fi
        sleep 2
        attempt=$((attempt + 1))
    done
    err "API did not become ready at $live_url within ${WSL_API_STARTUP_TIMEOUT_SECONDS}s (check journalctl -u ato-api -n 100 --no-pager)"
}

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

wsl_upgrade=false
if is_wsl; then
    wsl_upgrade=true
fi

install_args=()
if [[ "$RUN_MIGRATE" == "true" ]]; then
    install_args+=(--migrate)
fi
# WSL uses /opt runtime config and WSL-specific systemd units restored after install.sh.
# install.sh --start conflicts with --skip-systemd and expects /etc runtime config.
if [[ "$wsl_upgrade" != "true" ]]; then
    if [[ "$api_was_active" == "true" && "$RESTART_API" == "true" ]]; then
        install_args+=(--start)
    fi
    if [[ "$RUN_SMOKE" == "true" ]]; then
        if [[ "$RESTART_API" != "true" ]]; then
            err "--smoke requires API restart; omit --no-restart"
        fi
        install_args+=(--smoke)
    fi
fi

# WSL local deploys skip nginx and production systemd units (see wsl-local-deploy.sh).
if [[ "$wsl_upgrade" == "true" ]]; then
    install_args+=(--skip-nginx --skip-systemd)
    info "WSL detected; passing --skip-nginx --skip-systemd to install.sh"
    if [[ "$RUN_SMOKE" == "true" && "$RESTART_API" != "true" ]]; then
        err "--smoke requires API restart; omit --no-restart"
    fi
elif [[ ! -d /etc/nginx/conf.d ]]; then
    install_args+=(--skip-nginx)
    info "No /etc/nginx/conf.d; passing --skip-nginx to install.sh"
fi

info "Refreshing installed package bytes"
bash "$SCRIPT_DIR/install.sh" "${install_args[@]}"

if [[ "$wsl_upgrade" == "true" ]]; then
    runtime_config_dest="$INSTALL_DIR/runtime-config.json"
    runtime_config_src="$REPO_DIR/deployment/config/runtime-config.wsl_local.json"
    if [[ ! -f "$runtime_config_dest" ]]; then
        [[ -f "$runtime_config_src" ]] || err "Missing WSL runtime config template: $runtime_config_src"
        cp "$runtime_config_src" "$runtime_config_dest"
        sed -i 's/\r$//' "$runtime_config_dest" 2>/dev/null || true
        chown root:ato "$runtime_config_dest"
        chmod 640 "$runtime_config_dest"
        info "Installed missing WSL runtime config: $runtime_config_dest"
    fi

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

    if [[ "$api_was_active" == "true" && "$RESTART_API" == "true" ]]; then
        systemctl enable ato-api.service
        systemctl restart ato-api.service
        if systemctl is-enabled --quiet ato-synthetic-intake-worker.timer 2>/dev/null; then
            systemctl restart ato-synthetic-intake-worker.timer
        fi
        if systemctl is-enabled --quiet ato-analyzer-worker.service 2>/dev/null; then
            systemctl restart ato-analyzer-worker.service
        fi
        info "Restarted WSL services after package refresh"
        wait_for_wsl_api_loopback
    fi

    if [[ "$RUN_SMOKE" == "true" ]]; then
        smoke_script="$SCRIPT_DIR/smoke_service_chain.sh"
        [[ -f "$smoke_script" ]] || err "Missing smoke script: $smoke_script"
        ALLOW_DEGRADED_READY=true API_BASE_URL="${WSL_API_LOOPBACK_URL}" \
            bash "$smoke_script" || err "Smoke checks failed"
    fi

    echo ""
    echo "WSL upgrade verified at ${WSL_API_LOOPBACK_URL}/health/live"
fi

info "Upgrade complete; worker units remain disabled until explicitly enabled"
