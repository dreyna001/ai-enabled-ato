#!/usr/bin/env bash
# rollback.sh -- Restore the last recorded application snapshot marker.
# Database rollback is not performed; incompatible releases require restore drills.
set -euo pipefail
IFS=$'\n\t'

readonly INSTALL_DIR="/opt/ato-analyzer"
readonly RELEASE_STATE_DIR="/var/lib/ato/release"
readonly CURRENT_SNAPSHOT_FILE="$RELEASE_STATE_DIR/current-snapshot"
DRY_RUN=false

err() { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARN: $*" >&2; }
info() { echo "  $*"; }

usage() {
    cat <<'EOF'
Usage: rollback.sh [options]

Stop API and worker units, then restore the most recent install snapshot metadata
recorded by install.sh. This does not downgrade the database schema. When the
prior release is incompatible with persisted state, use the customer restore
procedure documented in docs/OPERATIONS_AND_RECOVERY.md instead.

Options:
  --dry-run            Validate rollback contract without stopping services
  -h, --help           Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) err "rollback.sh accepts only --dry-run or --help" ;;
    esac
done

run_rollback_dry_run() {
    info "Dry-run mode: validating rollback contract without host mutations"
    [[ -f "$SCRIPT_DIR/drain_workers.sh" ]] || err "Missing drain_workers.sh"
    if [[ -f "$CURRENT_SNAPSHOT_FILE" ]]; then
        stamp="$(tr -d '[:space:]' < "$CURRENT_SNAPSHOT_FILE")"
        [[ -n "$stamp" ]] || err "Release snapshot marker is empty"
        snapshot_dir="$RELEASE_STATE_DIR/snapshots/$stamp"
        [[ -d "$snapshot_dir" ]] || err "Snapshot directory missing: $snapshot_dir"
        info "Rollback snapshot marker present: $stamp"
    else
        warn "No release snapshot marker at $CURRENT_SNAPSHOT_FILE (install.sh not run on host)"
    fi
    info "Rollback dry-run contract satisfied"
    info "Database schema rollback and restore drills remain operator-owned"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$DRY_RUN" == "true" ]]; then
    run_rollback_dry_run
    echo "Rollback dry-run complete."
    exit 0
fi

[[ "$(id -u)" -eq 0 ]] || err "Run as root (sudo)"
[[ -f "$CURRENT_SNAPSHOT_FILE" ]] || err "No release snapshot marker at $CURRENT_SNAPSHOT_FILE"

stamp="$(tr -d '[:space:]' < "$CURRENT_SNAPSHOT_FILE")"
[[ -n "$stamp" ]] || err "Release snapshot marker is empty"
snapshot_dir="$RELEASE_STATE_DIR/snapshots/$stamp"
[[ -d "$snapshot_dir" ]] || err "Snapshot directory missing: $snapshot_dir"

info "Stopping workers and API"
bash "$SCRIPT_DIR/drain_workers.sh" || true
if systemctl is-active --quiet ato-api.service; then
    systemctl stop ato-api.service || err "Failed to stop ato-api.service"
fi

if [[ -f "$snapshot_dir/pyproject.toml" ]]; then
    cp "$snapshot_dir/pyproject.toml" "$INSTALL_DIR/pyproject.toml" \
        || err "Failed to restore pyproject.toml from snapshot"
    info "Restored pyproject.toml from snapshot $stamp"
fi
if [[ -f "$snapshot_dir/alembic.ini" ]]; then
    cp "$snapshot_dir/alembic.ini" "$INSTALL_DIR/alembic.ini" \
        || err "Failed to restore alembic.ini from snapshot"
    info "Restored alembic.ini from snapshot $stamp"
fi

warn "Database schema was not rolled back automatically"
warn "If migrations advanced, execute the tested restore procedure before restarting work"
info "Rollback metadata restore complete for snapshot $stamp"
