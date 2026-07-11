#!/usr/bin/env bash
# Verify ATO API liveness and readiness over loopback (and optional nginx edge).
# Health-only scope: no model calls, no secret logging.
set -euo pipefail
IFS=$'\n\t'

API_BASE_URL="${API_BASE_URL:-http://127.0.0.1:8000}"
NGINX_BASE_URL="${NGINX_BASE_URL:-}"
HTTP_TIMEOUT_SECONDS="${HTTP_TIMEOUT_SECONDS:-10}"
READY_RETRIES="${READY_RETRIES:-6}"
READY_RETRY_SECONDS="${READY_RETRY_SECONDS:-5}"
ALLOW_DEGRADED_READY="${ALLOW_DEGRADED_READY:-false}"
SMOKE_PYTHON="${SMOKE_PYTHON:-}"

DEGRADED_READY_ACCEPTED=false

usage() {
    cat <<'EOF'
Usage: smoke_service_chain.sh [options]

Options:
  --api-base-url URL       Loopback API base URL (default: http://127.0.0.1:8000)
  --nginx-base-url URL     Optional HTTPS nginx base URL for edge health checks
  -h, --help               Show this help

Environment overrides:
  API_BASE_URL, NGINX_BASE_URL, HTTP_TIMEOUT_SECONDS,
  READY_RETRIES, READY_RETRY_SECONDS, ALLOW_DEGRADED_READY (true or false),
  SMOKE_PYTHON (default: python3.12 when available, else python3)

Readiness requires HTTP 200 by default. Set ALLOW_DEGRADED_READY=true to accept
HTTP 503 as degraded (not ready) while HS-001 remains open.
EOF
}

err() {
    echo "ERROR: $*" >&2
    exit 1
}

warn() {
    echo "WARN: $*" >&2
}

info() {
    echo "  $*"
}

require_arg_value() {
    local option="$1"
    local value="${2:-}"
    [[ -n "$value" && "$value" != --* ]] || err "Missing value for $option"
}

validate_boolean() {
    local label="$1"
    local value="$2"
    case "$value" in
        true|false) ;;
        *) err "$label must be true or false (got: $value)" ;;
    esac
}

validate_positive_integer() {
    local label="$1"
    local value="$2"
    [[ "$value" =~ ^[1-9][0-9]*$ ]] || err "$label must be a positive integer (got: $value)"
}

validate_base_url() {
    local label="$1"
    local value="$2"
    local trimmed="$value"

    trimmed="${trimmed#"${trimmed%%[![:space:]]*}"}"
    trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"
    [[ -n "$trimmed" ]] || err "$label must not be empty"
    [[ "$value" == "$trimmed" ]] || err "$label must not include leading or trailing whitespace"

    case "$trimmed" in
        *'?'*|*'#'*|*'@'*)
            err "$label must not include userinfo, query, or fragment"
            ;;
    esac

    case "$trimmed" in
        http://127.0.0.1:*|http://localhost:*)
            ;;
        https://)
            err "$label has an empty host"
            ;;
        https://*)
            ;;
        *)
            err "$label must be loopback HTTP or verified HTTPS"
            ;;
    esac
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --api-base-url)
            require_arg_value "$1" "${2:-}"
            API_BASE_URL="$2"
            shift 2
            ;;
        --nginx-base-url)
            require_arg_value "$1" "${2:-}"
            NGINX_BASE_URL="$2"
            shift 2
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

require_command() {
    command -v "$1" >/dev/null 2>&1 || err "Missing required command: $1"
}

resolve_smoke_python() {
    if [[ -n "$SMOKE_PYTHON" ]]; then
        require_command "$SMOKE_PYTHON"
        return 0
    fi
    if command -v python3.12 >/dev/null 2>&1; then
        SMOKE_PYTHON=python3.12
        return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        SMOKE_PYTHON=python3
        return 0
    fi
    err "Missing required command: python3.12 or python3"
}

require_command curl
resolve_smoke_python
validate_boolean "ALLOW_DEGRADED_READY" "$ALLOW_DEGRADED_READY"
validate_positive_integer "HTTP_TIMEOUT_SECONDS" "$HTTP_TIMEOUT_SECONDS"
validate_positive_integer "READY_RETRIES" "$READY_RETRIES"
validate_positive_integer "READY_RETRY_SECONDS" "$READY_RETRY_SECONDS"
validate_base_url "API_BASE_URL" "$API_BASE_URL"
if [[ -n "$NGINX_BASE_URL" ]]; then
    validate_base_url "NGINX_BASE_URL" "$NGINX_BASE_URL"
fi

strip_trailing_slash() {
    local value="$1"
    while [[ "$value" == */ ]]; do
        value="${value%/}"
    done
    printf '%s' "$value"
}

API_BASE_URL="$(strip_trailing_slash "$API_BASE_URL")"
if [[ -n "$NGINX_BASE_URL" ]]; then
    NGINX_BASE_URL="$(strip_trailing_slash "$NGINX_BASE_URL")"
fi

SMOKE_BODY_FILE=""

cleanup_smoke_body_file() {
    if [[ -n "$SMOKE_BODY_FILE" && -f "$SMOKE_BODY_FILE" ]]; then
        rm -f "$SMOKE_BODY_FILE"
    fi
    SMOKE_BODY_FILE=""
}

trap cleanup_smoke_body_file EXIT

make_temp_body_file() {
    cleanup_smoke_body_file
    SMOKE_BODY_FILE="$(
        mktemp "${TMPDIR:-/tmp}/ato-smoke.XXXXXX" 2>/dev/null \
            || mktemp /tmp/ato-smoke.XXXXXX \
            || err "Failed to create temporary response file"
    )"
}

release_smoke_body_file() {
    cleanup_smoke_body_file
}

validate_live_json() {
    local body_file="$1"
    local label="$2"
    "$SMOKE_PYTHON" - "$body_file" <<'PY' || err "Liveness JSON validation failed for $label"
import json
import sys
from pathlib import Path

EXPECTED = {"status": "ok", "checks": {"process": "ok"}}

body_path = Path(sys.argv[1])
try:
    payload = json.loads(body_path.read_text(encoding="utf-8"))
except json.JSONDecodeError:
    raise SystemExit(1)

if payload != EXPECTED:
    raise SystemExit(1)
PY
}

validate_ready_ok_json() {
    local body_file="$1"
    local label="$2"
    "$SMOKE_PYTHON" - "$body_file" <<'PY' || err "Readiness JSON validation failed for $label"
import json
import sys
from pathlib import Path

READINESS_CHECK_NAMES = (
    "database",
    "storage",
    "authority_manifest",
    "jobs",
    "configuration",
)

body_path = Path(sys.argv[1])
try:
    payload = json.loads(body_path.read_text(encoding="utf-8"))
except json.JSONDecodeError:
    raise SystemExit(1)

if payload.get("status") != "ok":
    raise SystemExit(1)

checks = payload.get("checks")
if not isinstance(checks, dict):
    raise SystemExit(1)

if set(checks.keys()) != set(READINESS_CHECK_NAMES):
    raise SystemExit(1)

for name in READINESS_CHECK_NAMES:
    if checks.get(name) != "ok":
        raise SystemExit(1)
PY
}

validate_ready_problem_json() {
    local body_file="$1"
    local label="$2"
    "$SMOKE_PYTHON" - "$body_file" <<'PY' || err "Readiness problem JSON validation failed for $label"
import json
import re
import sys
from pathlib import Path

UUID_V4_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
ALLOWED_READINESS_ERROR_CODES = frozenset(
    {
        "reconciliation_required",
        "database_unavailable",
        "storage_unavailable",
    }
)

body_path = Path(sys.argv[1])
try:
    payload = json.loads(body_path.read_text(encoding="utf-8"))
except json.JSONDecodeError:
    raise SystemExit(1)

required_fields = (
    "error_code",
    "status",
    "detail",
    "title",
    "type",
    "instance",
    "field_errors",
    "retryable",
    "request_id",
)
for field in required_fields:
    if field not in payload:
        raise SystemExit(1)

if payload.get("status") != 503:
    raise SystemExit(1)

error_code = payload.get("error_code")
if error_code not in ALLOWED_READINESS_ERROR_CODES:
    raise SystemExit(1)

if payload.get("instance") != "/health/ready":
    raise SystemExit(1)

if not isinstance(payload.get("field_errors"), list):
    raise SystemExit(1)

if payload.get("retryable") is not True:
    raise SystemExit(1)

request_id = payload.get("request_id")
if not isinstance(request_id, str) or not UUID_V4_PATTERN.fullmatch(request_id):
    raise SystemExit(1)
PY
}

check_live() {
    local base_url="$1"
    local label="$2"
    local live_url="$base_url/health/live"
    local body_file=""

    make_temp_body_file
    body_file="$SMOKE_BODY_FILE"
    info "Checking liveness ($label): $live_url"
    if ! curl -fsS --max-time "$HTTP_TIMEOUT_SECONDS" -o "$body_file" "$live_url"; then
        err "Liveness transport check failed for $label"
    fi
    validate_live_json "$body_file" "$label"
    release_smoke_body_file
}

check_ready_with_retries() {
    local base_url="$1"
    local label="$2"
    local ready_url="$base_url/health/ready"
    local attempt=1
    local http_code=""
    local body_file=""

    info "Checking readiness ($label): $ready_url"
    while (( attempt <= READY_RETRIES )); do
        make_temp_body_file
        body_file="$SMOKE_BODY_FILE"
        http_code="$(
            curl -sS --max-time "$HTTP_TIMEOUT_SECONDS" \
                -o "$body_file" \
                -w '%{http_code}' \
                "$ready_url" || true
        )"
        case "$http_code" in
            200)
                validate_ready_ok_json "$body_file" "$label"
                release_smoke_body_file
                info "Readiness probe passed ($label): HTTP 200"
                return 0
                ;;
            503)
                validate_ready_problem_json "$body_file" "$label"
                release_smoke_body_file
                if [[ "$ALLOW_DEGRADED_READY" == "true" ]]; then
                    warn "Readiness probe degraded ($label): HTTP 503 (not ready; ALLOW_DEGRADED_READY=true)"
                    DEGRADED_READY_ACCEPTED=true
                    return 0
                fi
                err "Readiness probe returned HTTP 503 for $label (set ALLOW_DEGRADED_READY=true to permit degraded readiness while HS-001 is open)"
                ;;
            000)
                release_smoke_body_file
                info "Readiness probe attempt $attempt/$READY_RETRIES: transport failure"
                ;;
            *)
                err "Readiness probe returned unexpected HTTP $http_code for $label"
                ;;
        esac
        if (( attempt >= READY_RETRIES )); then
            err "Readiness probe timed out for $label after $READY_RETRIES attempts"
        fi
        sleep "$READY_RETRY_SECONDS"
        attempt=$((attempt + 1))
    done
}

if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet ato-api; then
        info "ato-api.service is active"
    else
        info "ato-api.service is not active"
    fi
fi

check_live "$API_BASE_URL" "api-loopback"
check_ready_with_retries "$API_BASE_URL" "api-loopback"

if [[ -n "$NGINX_BASE_URL" ]]; then
    check_live "$NGINX_BASE_URL" "nginx-edge"
    check_ready_with_retries "$NGINX_BASE_URL" "nginx-edge"
fi

if [[ "$DEGRADED_READY_ACCEPTED" == "true" ]]; then
    warn "ATO API smoke checks completed with degraded readiness; not release-ready"
    exit 0
fi

info "ATO API smoke checks passed"
