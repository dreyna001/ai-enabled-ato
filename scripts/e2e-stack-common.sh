#!/usr/bin/env bash
# Shared helpers for the bounded portal E2E stack (Linux/WSL; no Docker/public network).
if grep -q $'\r' "$0" 2>/dev/null; then
  exec /usr/bin/env bash <(sed 's/\r$//' "$0") "$@"
fi

set -euo pipefail
IFS=$'\n\t'

E2E_STACK_DIR="${ATO_E2E_STACK_DIR:-}"
E2E_REPO_ROOT="${E2E_REPO_ROOT:-}"
E2E_API_PORT="${ATO_E2E_API_PORT:-8000}"
E2E_PORTAL_PORT="${ATO_E2E_PORTAL_PORT:-5173}"
E2E_API_URL="http://127.0.0.1:${E2E_API_PORT}"
E2E_PORTAL_URL="http://127.0.0.1:${E2E_PORTAL_PORT}"
E2E_DB_NAME="${ATO_E2E_DB_NAME:-ato_e2e}"
E2E_DB_USER="${ATO_E2E_DB_USER:-ato_e2e}"
E2E_DB_HOST="${ATO_E2E_DB_HOST:-127.0.0.1}"
E2E_DB_PORT="${ATO_E2E_DB_PORT:-5432}"
E2E_STARTUP_TIMEOUT_SECONDS="${ATO_E2E_STARTUP_TIMEOUT_SECONDS:-120}"

e2e_err() {
  echo "ERROR: $*" >&2
  exit 1
}

e2e_info() {
  echo "  $*"
}

e2e_resolve_repo_root() {
  if [[ -n "${E2E_REPO_ROOT}" ]]; then
    return 0
  fi
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}")" && pwd)"
  E2E_REPO_ROOT="$(cd "$script_dir/.." && pwd)"
}

e2e_resolve_stack_dir() {
  e2e_resolve_repo_root
  if [[ -z "${E2E_STACK_DIR}" ]]; then
    E2E_STACK_DIR="${E2E_REPO_ROOT}/.e2e-stack"
  fi
}

e2e_export_paths() {
  e2e_resolve_stack_dir
  export E2E_REPO_ROOT E2E_STACK_DIR
  export E2E_API_PORT E2E_PORTAL_PORT E2E_API_URL E2E_PORTAL_URL
  export E2E_CREDENTIALS_DIR="${E2E_STACK_DIR}/credentials"
  export E2E_STORAGE_DIR="${E2E_STACK_DIR}/storage"
  export E2E_RUNTIME_CONFIG="${E2E_STACK_DIR}/runtime-config.resolved.json"
  export E2E_DATABASE_DSN_FILE="${E2E_CREDENTIALS_DIR}/database-dsn"
  export E2E_PID_DIR="${E2E_STACK_DIR}/pids"
  export E2E_LOG_DIR="${E2E_STACK_DIR}/logs"
  export E2E_STOP_FILE="${E2E_STACK_DIR}/stop"
  export E2E_READY_FILE="${E2E_STACK_DIR}/ready"
}

e2e_require_commands() {
  local missing=()
  for cmd in "$@"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      missing+=("$cmd")
    fi
  done
  if ((${#missing[@]} > 0)); then
    e2e_err "Missing required commands: ${missing[*]}"
  fi
}

e2e_require_python_env() {
  e2e_resolve_repo_root
  if [[ -x "${E2E_REPO_ROOT}/.venv/bin/python" ]]; then
    export E2E_PYTHON="${E2E_REPO_ROOT}/.venv/bin/python"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    export E2E_PYTHON="$(command -v python3)"
    return 0
  fi
  e2e_err "Python 3.12+ is required. Create .venv with: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
}

e2e_wait_for_url() {
  local url="$1"
  local label="${2:-$url}"
  local timeout="${3:-$E2E_STARTUP_TIMEOUT_SECONDS}"
  local attempt=1
  local max_attempts=$((timeout / 2))
  e2e_info "Waiting for ${label} at ${url}"
  while (( attempt <= max_attempts )); do
    if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
      e2e_info "${label} ready (attempt ${attempt}/${max_attempts})"
      return 0
    fi
    sleep 2
    attempt=$((attempt + 1))
  done
  e2e_err "${label} did not become ready at ${url} within ${timeout}s"
}

e2e_resolve_database_url() {
  if [[ -n "${ATO_E2E_DATABASE_URL:-}" ]]; then
    printf '%s' "$ATO_E2E_DATABASE_URL"
    return 0
  fi
  if [[ -n "${ATO_TEST_DATABASE_URL:-}" ]]; then
    printf '%s' "$ATO_TEST_DATABASE_URL"
    return 0
  fi
  local password="${ATO_E2E_DB_PASSWORD:-ato_e2e_dev}"
  printf 'postgresql+asyncpg://%s:%s@%s:%s/%s' \
    "$E2E_DB_USER" "$password" "$E2E_DB_HOST" "$E2E_DB_PORT" "$E2E_DB_NAME"
}

e2e_require_postgresql() {
  e2e_require_commands psql pg_isready curl
  if ! pg_isready -h "$E2E_DB_HOST" -p "$E2E_DB_PORT" >/dev/null 2>&1; then
    e2e_err "PostgreSQL is not reachable at ${E2E_DB_HOST}:${E2E_DB_PORT}. Start PostgreSQL locally or set ATO_E2E_DATABASE_URL to a reachable asyncpg DSN."
  fi
}

e2e_ensure_database() {
  local dsn
  dsn="$(e2e_resolve_database_url)"
  local password
  password="$(printf '%s' "$dsn" | sed -n 's#^postgresql+asyncpg://[^:]*:\([^@]*\)@.*#\1#p')"
  if [[ -z "$password" ]]; then
    password="${ATO_E2E_DB_PASSWORD:-ato_e2e_dev}"
  fi

  if psql -h "$E2E_DB_HOST" -p "$E2E_DB_PORT" -U postgres -tAc "SELECT 1" >/dev/null 2>&1; then
    psql -h "$E2E_DB_HOST" -p "$E2E_DB_PORT" -U postgres -v ON_ERROR_STOP=1 <<SQL >/dev/null
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${E2E_DB_USER}') THEN
    CREATE ROLE ${E2E_DB_USER} LOGIN PASSWORD '${password}';
  ELSE
    ALTER ROLE ${E2E_DB_USER} WITH LOGIN PASSWORD '${password}';
  END IF;
END
\$\$;
SQL
    if ! psql -h "$E2E_DB_HOST" -p "$E2E_DB_PORT" -U postgres -tAc \
      "SELECT 1 FROM pg_database WHERE datname='${E2E_DB_NAME}'" | grep -q 1; then
      createdb -h "$E2E_DB_HOST" -p "$E2E_DB_PORT" -U postgres -O "$E2E_DB_USER" "$E2E_DB_NAME"
      e2e_info "Created database ${E2E_DB_NAME}"
    fi
    return 0
  fi

  if psql -h "$E2E_DB_HOST" -p "$E2E_DB_PORT" -U "$E2E_DB_USER" -d "$E2E_DB_NAME" -tAc "SELECT 1" >/dev/null 2>&1; then
    return 0
  fi

  e2e_err "Could not provision ${E2E_DB_NAME}. Ensure PostgreSQL accepts connections and provide ATO_E2E_DATABASE_URL if using a custom role/database."
}

e2e_write_credentials() {
  mkdir -p "$E2E_CREDENTIALS_DIR" "$E2E_STORAGE_DIR" "$E2E_PID_DIR" "$E2E_LOG_DIR"
  chmod 700 "$E2E_CREDENTIALS_DIR"

  local dsn
  dsn="$(e2e_resolve_database_url)"
  printf '%s' "$dsn" >"$E2E_DATABASE_DSN_FILE"
  chmod 600 "$E2E_DATABASE_DSN_FILE"

  if [[ ! -f "${E2E_CREDENTIALS_DIR}/oidc-client-secret" ]]; then
    openssl rand -hex 16 >"${E2E_CREDENTIALS_DIR}/oidc-client-secret"
    chmod 600 "${E2E_CREDENTIALS_DIR}/oidc-client-secret"
  fi
  if [[ ! -f "${E2E_CREDENTIALS_DIR}/audit-hmac-key" ]]; then
    openssl rand -hex 32 >"${E2E_CREDENTIALS_DIR}/audit-hmac-key"
    chmod 600 "${E2E_CREDENTIALS_DIR}/audit-hmac-key"
  fi
}

e2e_write_runtime_config() {
  local template="${E2E_REPO_ROOT}/deployment/config/runtime-config.dev_local.e2e.json"
  [[ -f "$template" ]] || e2e_err "Missing E2E runtime config template: $template"

  "${E2E_PYTHON}" - <<'PY'
import json
import os
from pathlib import Path

template = Path(os.environ["E2E_TEMPLATE"])
output = Path(os.environ["E2E_RUNTIME_CONFIG"])
stack_dir = Path(os.environ["E2E_STACK_DIR"])
credentials = stack_dir / "credentials"

document = json.loads(template.read_text(encoding="utf-8"))
document["STORAGE_DATA_PATH"] = str((stack_dir / "storage").resolve())
document["PORTAL_PUBLIC_ORIGIN"] = os.environ["E2E_PORTAL_URL"]
document["OIDC_ISSUER_URL"] = f"{os.environ['E2E_API_URL'].rstrip('/')}/dev-oidc"
document["OIDC_CLIENT_CREDENTIAL_REFERENCE"] = {
    "source": "root_owned_file",
    "path": str((credentials / "oidc-client-secret").resolve()),
}
document["AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE"] = {
    "source": "root_owned_file",
    "path": str((credentials / "audit-hmac-key").resolve()),
}
output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
PY
}

e2e_run_migrations() {
  (
    cd "$E2E_REPO_ROOT"
    ATO_DATABASE_DSN_FILE="$E2E_DATABASE_DSN_FILE" \
      "${E2E_PYTHON}" -m alembic -c alembic.ini upgrade head
  )
  e2e_info "Database migrations completed"
}

e2e_record_pid() {
  local name="$1"
  local pid="$2"
  printf '%s\n' "$pid" >"${E2E_PID_DIR}/${name}.pid"
}

e2e_read_pid() {
  local name="$1"
  local file="${E2E_PID_DIR}/${name}.pid"
  if [[ -f "$file" ]]; then
    cat "$file"
  fi
}

e2e_stop_pidfile() {
  local name="$1"
  local pid
  pid="$(e2e_read_pid "$name" || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid" >/dev/null 2>&1 || true
    wait "$pid" 2>/dev/null || true
  fi
  rm -f "${E2E_PID_DIR}/${name}.pid"
}

e2e_is_running() {
  local name="$1"
  local pid
  pid="$(e2e_read_pid "$name" || true)"
  [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1
}

e2e_mark_ready() {
  date -u +%Y-%m-%dT%H:%M:%SZ >"$E2E_READY_FILE"
}

e2e_clear_ready() {
  rm -f "$E2E_READY_FILE" "$E2E_STOP_FILE"
}
