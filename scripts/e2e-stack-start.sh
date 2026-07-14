#!/usr/bin/env bash
# Start the bounded portal E2E stack: API, intake poller, analyzer worker, optional Vite portal.
if grep -q $'\r' "$0" 2>/dev/null; then
  exec /usr/bin/env bash <(sed 's/\r$//' "$0") "$@"
fi
set -euo pipefail
IFS=$'\n\t'

START_PORTAL=false
FOR_PLAYWRIGHT=false
SKIP_MIGRATE=false
REUSE_RUNNING=false

usage() {
  cat <<'EOF'
Usage: e2e-stack-start.sh [options]

Start PostgreSQL-backed API + dev OIDC + synthetic intake poller + deterministic
analyzer worker for portal Playwright E2E. No Docker or public network.

Options:
  --portal             Also start the Vite dev server (portal UI)
  --for-playwright     Same as --portal; used by Playwright webServer
  --skip-migrate       Skip alembic upgrade head
  --reuse              Reuse already-running stack processes when healthy
  -h, --help           Show this help

Environment:
  ATO_E2E_DATABASE_URL   Full asyncpg DSN (overrides auto-provisioned local DB)
  ATO_E2E_STACK_DIR      Stack state directory (default: <repo>/.e2e-stack)
  ATO_E2E_API_PORT       API loopback port (default: 8000)
  ATO_E2E_PORTAL_PORT    Portal dev port (default: 5173)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --portal|--for-playwright)
      START_PORTAL=true
      FOR_PLAYWRIGHT=true
      shift
      ;;
    --skip-migrate)
      SKIP_MIGRATE=true
      shift
      ;;
    --reuse)
      REUSE_RUNNING=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/e2e-stack-common.sh
source "$SCRIPT_DIR/e2e-stack-common.sh"
e2e_export_paths
e2e_require_commands curl openssl
e2e_require_python_env

if [[ "$REUSE_RUNNING" == "true" ]] && [[ -f "$E2E_READY_FILE" ]]; then
  if curl -fsS --max-time 2 "${E2E_API_URL}/health/live" >/dev/null 2>&1; then
    if [[ "$START_PORTAL" != "true" ]] || curl -fsS --max-time 2 "${E2E_PORTAL_URL}/" >/dev/null 2>&1; then
      e2e_info "Reusing healthy E2E stack in ${E2E_STACK_DIR}"
      exit 0
    fi
  fi
fi

mkdir -p "$E2E_STACK_DIR"
rm -f "$E2E_STOP_FILE"
e2e_require_postgresql
e2e_ensure_database
e2e_write_credentials

export E2E_TEMPLATE="${E2E_REPO_ROOT}/deployment/config/runtime-config.dev_local.e2e.json"
e2e_write_runtime_config

if [[ "$SKIP_MIGRATE" != "true" ]]; then
  e2e_run_migrations
fi

start_api() {
  if e2e_is_running api; then
    e2e_info "API already running (pid $(e2e_read_pid api))"
    return 0
  fi
  (
    cd "$E2E_REPO_ROOT"
    ATO_RUNTIME_CONFIG_PATH="$E2E_RUNTIME_CONFIG" \
    ATO_DATABASE_DSN_FILE="$E2E_DATABASE_DSN_FILE" \
    ATO_HOST=127.0.0.1 \
    ATO_PORT="$E2E_API_PORT" \
      "${E2E_PYTHON}" -m ato_service.main --config "$E2E_RUNTIME_CONFIG" --host 127.0.0.1 --port "$E2E_API_PORT"
  ) >"${E2E_LOG_DIR}/api.log" 2>&1 &
  e2e_record_pid api "$!"
  e2e_info "Started API (pid $(e2e_read_pid api))"
}

start_intake_poller() {
  if e2e_is_running intake-poller; then
    e2e_info "Intake poller already running (pid $(e2e_read_pid intake-poller))"
    return 0
  fi
  (
    while [[ ! -f "$E2E_STOP_FILE" ]]; do
      ATO_RUNTIME_CONFIG_PATH="$E2E_RUNTIME_CONFIG" \
      ATO_DATABASE_DSN_FILE="$E2E_DATABASE_DSN_FILE" \
        "${E2E_PYTHON}" -m ato_service.synthetic_intake_worker --config "$E2E_RUNTIME_CONFIG" \
        >>"${E2E_LOG_DIR}/intake-poller.log" 2>&1 || true
      sleep 2
    done
  ) &
  e2e_record_pid intake-poller "$!"
  e2e_info "Started intake poller (pid $(e2e_read_pid intake-poller))"
}

start_analyzer_worker() {
  if e2e_is_running analyzer-worker; then
    e2e_info "Analyzer worker already running (pid $(e2e_read_pid analyzer-worker))"
    return 0
  fi
  (
    ATO_RUNTIME_CONFIG_PATH="$E2E_RUNTIME_CONFIG" \
    ATO_DATABASE_DSN_FILE="$E2E_DATABASE_DSN_FILE" \
      "${E2E_PYTHON}" -m ato_service.deterministic_analyzer_worker \
      --config "$E2E_RUNTIME_CONFIG" --poll-interval-seconds 2
  ) >"${E2E_LOG_DIR}/analyzer-worker.log" 2>&1 &
  e2e_record_pid analyzer-worker "$!"
  e2e_info "Started analyzer worker (pid $(e2e_read_pid analyzer-worker))"
}

start_portal() {
  if e2e_is_running portal; then
    e2e_info "Portal dev server already running (pid $(e2e_read_pid portal))"
    return 0
  fi
  e2e_require_commands npm
  (
    cd "${E2E_REPO_ROOT}/portal"
    VITE_DEV_API_TARGET="$E2E_API_URL" \
      npm run dev -- --host 127.0.0.1 --port "$E2E_PORTAL_PORT" --strictPort
  ) >"${E2E_LOG_DIR}/portal.log" 2>&1 &
  e2e_record_pid portal "$!"
  e2e_info "Started portal dev server (pid $(e2e_read_pid portal))"
}

echo "=== ATO portal E2E stack start ==="
start_api
e2e_wait_for_url "${E2E_API_URL}/health/live" "API liveness"
start_intake_poller
start_analyzer_worker

if [[ "$START_PORTAL" == "true" ]]; then
  start_portal
  e2e_wait_for_url "${E2E_PORTAL_URL}/" "Portal dev server"
fi

e2e_mark_ready
echo ""
echo "E2E stack ready."
echo "  Stack dir:  ${E2E_STACK_DIR}"
echo "  API:        ${E2E_API_URL}"
if [[ "$START_PORTAL" == "true" ]]; then
  echo "  Portal:     ${E2E_PORTAL_URL}"
fi
echo "  Logs:       ${E2E_LOG_DIR}"
echo "  Stop:       bash scripts/e2e-stack-stop.sh"
if [[ "$FOR_PLAYWRIGHT" == "true" ]]; then
  echo "  Playwright: cd portal && npm run test:e2e"
fi
