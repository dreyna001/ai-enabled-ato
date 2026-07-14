#!/usr/bin/env bash
# drain_workers.sh -- Gracefully stop intake and analyzer workers before upgrade.
set -euo pipefail
IFS=$'\n\t'

readonly WORKER_UNITS=(
    "ato-intake-worker.service"
    "ato-analyzer-worker.service"
)
readonly DRAIN_TIMEOUT_SECONDS="${DRAIN_TIMEOUT_SECONDS:-35}"

err() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "  $*"; }

[[ "$(id -u)" -eq 0 ]] || err "Run as root (sudo)"

for unit in "${WORKER_UNITS[@]}"; do
    if ! systemctl list-unit-files "$unit" >/dev/null 2>&1; then
        info "Unit not installed: $unit"
        continue
    fi
    if systemctl is-active --quiet "$unit"; then
        info "Stopping $unit"
        systemctl stop "$unit" || err "Failed to stop $unit"
        deadline=$((SECONDS + DRAIN_TIMEOUT_SECONDS))
        while systemctl is-active --quiet "$unit"; do
            if (( SECONDS >= deadline )); then
                err "$unit did not stop within ${DRAIN_TIMEOUT_SECONDS}s"
            fi
            sleep 1
        done
        info "$unit stopped"
    else
        info "$unit already inactive"
    fi
done

info "Worker drain complete"
