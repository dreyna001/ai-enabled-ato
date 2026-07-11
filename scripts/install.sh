#!/usr/bin/env bash
# install.sh -- ATO API on-prem installation for RHEL 9-compatible hosts.
# API-only scope: no portal, worker, timers, or model hosting.
# Run as root.
set -euo pipefail
IFS=$'\n\t'

trap 'err "Failed at line ${LINENO}: ${BASH_COMMAND}"' ERR

readonly INSTALL_DIR="/opt/ato-analyzer"
readonly CONFIG_DIR="/etc/ato-analyzer"
readonly DATA_DIR="/var/ato-packages"
readonly SVC_HOME="/var/lib/ato"
readonly CREDENTIALS_DIR="$CONFIG_DIR/credentials"
readonly RUNTIME_CONFIG_PATH="$CONFIG_DIR/runtime-config.json"
readonly RUNTIME_CONFIG_EXAMPLE_PATH="$CONFIG_DIR/runtime-config.onprem.example.json"
readonly DATABASE_DSN_CREDENTIAL_PATH="$CREDENTIALS_DIR/database-dsn"
readonly SVC_USER="ato"
readonly PYTHON_BIN="${PYTHON_BIN:-python3.12}"
readonly MIN_PYTHON_MAJOR=3
readonly MIN_PYTHON_MINOR=12

INSTALL_SYSTEMD_UNITS="${INSTALL_SYSTEMD_UNITS:-true}"
INSTALL_NGINX_SITE="${INSTALL_NGINX_SITE:-true}"
START_SERVICE=false
RUN_SMOKE=false
RUN_MIGRATE=false

usage() {
    cat <<'EOF'
Usage: install.sh [options]

Install the ATO API package, deployment assets, and least-privilege host layout.
Does not start services unless --start is supplied. Does not run smoke checks
unless --smoke is supplied. Does not run database migrations unless --migrate
is supplied.

Options:
  --start              Enable and start ato-api.service after install
  --smoke              Run scripts/smoke_service_chain.sh after install (requires --start)
  --migrate            Run alembic upgrade head using the protected DSN file
  --skip-systemd       Skip systemd unit installation and daemon-reload
  --skip-nginx         Skip nginx example template installation
  -h, --help           Show this help

Environment overrides:
  PYTHON_BIN           Python interpreter for the service venv (default: python3.12)
  INSTALL_SYSTEMD_UNITS, INSTALL_NGINX_SITE (true or false)
EOF
}

err() { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARN: $*" >&2; }
info() { echo "  $*"; }

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

while [[ $# -gt 0 ]]; do
    case "$1" in
        --start)
            START_SERVICE=true
            shift
            ;;
        --smoke)
            RUN_SMOKE=true
            shift
            ;;
        --migrate)
            RUN_MIGRATE=true
            shift
            ;;
        --skip-systemd)
            INSTALL_SYSTEMD_UNITS=false
            shift
            ;;
        --skip-nginx)
            INSTALL_NGINX_SITE=false
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

validate_boolean "INSTALL_SYSTEMD_UNITS" "$INSTALL_SYSTEMD_UNITS"
validate_boolean "INSTALL_NGINX_SITE" "$INSTALL_NGINX_SITE"

if [[ "$RUN_SMOKE" == "true" && "$START_SERVICE" != "true" ]]; then
    err "--smoke requires --start in the same invocation (for example: --migrate --start --smoke)"
fi

check_root() {
    [[ "$(id -u)" -eq 0 ]] || err "Run as root (sudo)"
}

check_command() {
    command -v "$1" >/dev/null 2>&1 || err "Missing required command: $1"
}

validate_safe_path() {
    local label="$1"
    local value="$2"
    [[ "$value" == /* ]] || err "$label must be an absolute path"
    case "$value" in
        *[![:alnum:]/._+-]*)
            err "$label contains unsupported characters: $value"
            ;;
    esac
}

normalize_dest_file_crlf() {
    local file="$1"
    if [[ -f "$file" ]]; then
        sed -i 's/\r$//' "$file" 2>/dev/null || true
    fi
}

check_python_version() {
    local pybin="$1"
    check_command "$pybin"
    local ver major minor
    ver="$("$pybin" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" \
        || err "Python interpreter not usable: $pybin"
    major="${ver%%.*}"
    minor="${ver##*.}"
    if [[ "$major" -lt "$MIN_PYTHON_MAJOR" ]] \
        || { [[ "$major" -eq "$MIN_PYTHON_MAJOR" ]] && [[ "$minor" -lt "$MIN_PYTHON_MINOR" ]]; }; then
        err "Requires Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ (found $ver at $pybin)"
    fi
    info "Python version: $ver ($pybin)"
}

create_user_if_missing() {
    local user="$1" home="$2"
    if id "$user" &>/dev/null; then
        info "User exists: $user"
    else
        useradd --system --shell /sbin/nologin --home-dir "$home" --no-create-home "$user" \
            || err "Failed to create user: $user"
        info "Created user: $user"
    fi
}

ensure_dir() {
    local dir="$1" owner="$2" mode="$3"
    reject_unsafe_existing_path "$dir" "Directory"
    mkdir -p "$dir" || err "Failed to create directory: $dir"
    chown "$owner" "$dir" || err "Failed to set owner on: $dir"
    chmod "$mode" "$dir" || err "Failed to set permissions on: $dir"
}

reject_unsafe_existing_path() {
    local path="$1"
    local label="$2"

    if [[ -L "$path" ]]; then
        err "$label must be a directory, not a symlink: $path"
    fi
    if [[ -e "$path" && ! -d "$path" ]]; then
        err "$label must be a directory: $path"
    fi
}

enforce_existing_regular_file() {
    local path="$1"
    local owner="$2"
    local mode="$3"

    [[ -e "$path" || -L "$path" ]] || return 0

    if [[ -L "$path" ]]; then
        err "$path must be a regular file, not a symlink"
    fi
    if [[ ! -f "$path" ]]; then
        err "$path must be a regular file"
    fi

    chown "$owner" "$path" || err "Failed to set owner on: $path"
    chmod "$mode" "$path" || err "Failed to set permissions on: $path"
}

reject_non_regular_existing_file() {
    local path="$1"

    [[ -e "$path" || -L "$path" ]] || return 0

    if [[ -L "$path" ]]; then
        err "$path must be a regular file, not a symlink"
    fi
    if [[ ! -f "$path" ]]; then
        err "$path must be a regular file"
    fi
}

install_runtime_config_example() {
    local src="$REPO_DIR/deployment/config/runtime-config.onprem.example.json"
    [[ -f "$src" ]] || err "Missing runtime config example: $src"

    reject_non_regular_existing_file "$RUNTIME_CONFIG_PATH"
    if [[ -f "$RUNTIME_CONFIG_PATH" ]]; then
        info "Runtime config exists: $RUNTIME_CONFIG_PATH (contents not modified)"
        enforce_existing_regular_file "$RUNTIME_CONFIG_PATH" "root:$SVC_USER" 640
    else
        info "Runtime config is not present: $RUNTIME_CONFIG_PATH"
        info "Copy runtime-config.onprem.example.json to runtime-config.json before --start"
    fi

    reject_non_regular_existing_file "$RUNTIME_CONFIG_EXAMPLE_PATH"
    if [[ -f "$RUNTIME_CONFIG_EXAMPLE_PATH" ]]; then
        info "Runtime config example exists: $RUNTIME_CONFIG_EXAMPLE_PATH (not overwritten)"
        enforce_existing_regular_file "$RUNTIME_CONFIG_EXAMPLE_PATH" "root:$SVC_USER" 640
        return 0
    fi

    cp "$src" "$RUNTIME_CONFIG_EXAMPLE_PATH" || err "Failed to install runtime config example"
    normalize_dest_file_crlf "$RUNTIME_CONFIG_EXAMPLE_PATH"
    chown root:"$SVC_USER" "$RUNTIME_CONFIG_EXAMPLE_PATH" || err "Failed to set owner on runtime config example"
    chmod 640 "$RUNTIME_CONFIG_EXAMPLE_PATH" || err "Failed to set permissions on runtime config example"
    info "Installed inactive runtime config example: $RUNTIME_CONFIG_EXAMPLE_PATH"
}

install_database_dsn_credential_layout() {
    ensure_dir "$CREDENTIALS_DIR" "root:root" 700
    reject_non_regular_existing_file "$DATABASE_DSN_CREDENTIAL_PATH"
    if [[ -f "$DATABASE_DSN_CREDENTIAL_PATH" ]]; then
        info "Database DSN credential file exists: $DATABASE_DSN_CREDENTIAL_PATH (contents not modified)"
        enforce_existing_regular_file "$DATABASE_DSN_CREDENTIAL_PATH" "root:root" 600
        return 0
    fi
    info "Database DSN credential file is not present: $DATABASE_DSN_CREDENTIAL_PATH"
    info "Provision a UTF-8 file containing only the SQLAlchemy PostgreSQL DSN before --migrate or --start"
}

copy_repo_payload() {
    local src="$1" dest="$2"
    [[ -e "$src" ]] || err "Missing repository path: $src"
    mkdir -p "$(dirname "$dest")"
    cp -a "$src" "$dest" || err "Failed to copy $src to $dest"
}

set_install_tree_permissions() {
    chown -R root:root "$INSTALL_DIR" || err "Failed to set ownership on $INSTALL_DIR"
    find "$INSTALL_DIR" -type d -exec chmod 755 {} + || err "Failed to set directory permissions under $INSTALL_DIR"
    find "$INSTALL_DIR" -type f -exec chmod 644 {} + || err "Failed to set file permissions under $INSTALL_DIR"
    if [[ -d "$INSTALL_DIR/venv/bin" ]]; then
        chmod 755 "$INSTALL_DIR/venv/bin/"* || err "Failed to set venv bin permissions"
    fi
}

install_application_tree() {
    local venv_dir="$INSTALL_DIR/venv"
    local pyproject="$REPO_DIR/pyproject.toml"
    local src_dir="$REPO_DIR/src"
    local readme="$REPO_DIR/README.md"
    local alembic_ini="$REPO_DIR/alembic.ini"
    local migrations_dir="$REPO_DIR/migrations"
    local operations_doc="$REPO_DIR/docs/OPERATIONS_AND_RECOVERY.md"

    [[ -f "$pyproject" ]] || err "Missing pyproject.toml: $pyproject"
    [[ -f "$readme" ]] || err "Missing README required by pyproject.toml: $readme"
    [[ -d "$src_dir/ato_service" ]] || err "Missing package directory: $src_dir/ato_service"
    [[ -d "$REPO_DIR/docs/contracts" ]] || err "Missing contracts directory: $REPO_DIR/docs/contracts"
    [[ -d "$REPO_DIR/reference" ]] || err "Missing reference directory: $REPO_DIR/reference"
    [[ -f "$alembic_ini" ]] || err "Missing alembic.ini: $alembic_ini"
    [[ -d "$migrations_dir" ]] || err "Missing migrations directory: $migrations_dir"
    [[ -f "$operations_doc" ]] || err "Missing operations doc: $operations_doc"

    copy_repo_payload "$pyproject" "$INSTALL_DIR/pyproject.toml"
    copy_repo_payload "$readme" "$INSTALL_DIR/README.md"
    copy_repo_payload "$alembic_ini" "$INSTALL_DIR/alembic.ini"
    rm -rf "$INSTALL_DIR/migrations"
    copy_repo_payload "$migrations_dir" "$INSTALL_DIR/migrations"
    rm -rf "$INSTALL_DIR/src"
    copy_repo_payload "$src_dir" "$INSTALL_DIR/src"
    rm -rf "$INSTALL_DIR/docs/contracts"
    mkdir -p "$INSTALL_DIR/docs"
    copy_repo_payload "$REPO_DIR/docs/contracts" "$INSTALL_DIR/docs/contracts"
    copy_repo_payload "$operations_doc" "$INSTALL_DIR/docs/OPERATIONS_AND_RECOVERY.md"
    rm -rf "$INSTALL_DIR/reference"
    copy_repo_payload "$REPO_DIR/reference" "$INSTALL_DIR/reference"

    if [[ ! -x "$venv_dir/bin/python" ]]; then
        "$PYTHON_BIN" -m venv "$venv_dir" || err "Failed to create virtual environment"
    fi
    "$venv_dir/bin/pip" install "$INSTALL_DIR" \
        || err "Failed to install ato_service package dependencies"
    "$venv_dir/bin/pip" install --force-reinstall --no-deps "$INSTALL_DIR" \
        || err "Failed to reinstall ato_service package from install tree"
    set_install_tree_permissions
    info "Installed application tree under $INSTALL_DIR (root-owned, service-readable)"
}

install_systemd_unit() {
    local unit="ato-api.service"
    local src="$REPO_DIR/deployment/systemd/$unit"
    local dest="/etc/systemd/system/$unit"
    [[ -f "$src" ]] || err "Missing systemd unit: $src"
    reject_non_regular_existing_file "$dest"
    cp "$src" "$dest" || err "Failed to copy $unit"
    normalize_dest_file_crlf "$dest"
    chown root:root "$dest" || err "Failed to set owner on $dest"
    chmod 644 "$dest" || err "Failed to set permissions on $dest"
    systemctl daemon-reload || err "Failed to reload systemd"
    info "Installed systemd unit: $unit"
}

install_nginx_example() {
    local src="$REPO_DIR/deployment/nginx/ato-api.conf"
    local dest="/etc/nginx/conf.d/ato-api.conf.example"
    [[ -f "$src" ]] || err "Missing nginx example template: $src"
    reject_non_regular_existing_file "$dest"
    if [[ -f "$dest" ]]; then
        info "Nginx example exists: $dest (not overwritten)"
        enforce_existing_regular_file "$dest" "root:root" 644
        return 0
    fi
    cp "$src" "$dest" || err "Failed to copy nginx example template"
    normalize_dest_file_crlf "$dest"
    chown root:root "$dest" || err "Failed to set owner on $dest"
    chmod 644 "$dest" || err "Failed to set permissions on $dest"
    info "Installed inactive nginx example: $dest"
    warn "Replace TLS placeholders and rename to ato-api.conf before enabling the site"
}

validate_migration_prerequisites() {
    [[ -f "$DATABASE_DSN_CREDENTIAL_PATH" ]] \
        || err "Missing database DSN credential file: $DATABASE_DSN_CREDENTIAL_PATH"
    [[ -s "$DATABASE_DSN_CREDENTIAL_PATH" ]] \
        || err "Database DSN credential file is empty: $DATABASE_DSN_CREDENTIAL_PATH"
    [[ -x "$INSTALL_DIR/venv/bin/alembic" ]] \
        || err "Alembic entrypoint missing: $INSTALL_DIR/venv/bin/alembic"
    [[ -f "$INSTALL_DIR/alembic.ini" ]] \
        || err "Alembic config missing: $INSTALL_DIR/alembic.ini"
}

run_database_migrations() {
    validate_migration_prerequisites
    info "Running database migrations via ATO_DATABASE_DSN_FILE"
    (
        cd "$INSTALL_DIR" || err "Failed to enter install directory: $INSTALL_DIR"
        ATO_DATABASE_DSN_FILE="$DATABASE_DSN_CREDENTIAL_PATH" \
            "$INSTALL_DIR/venv/bin/alembic" -c "$INSTALL_DIR/alembic.ini" upgrade head
    ) || err "Database migration failed"
    info "Database migrations completed"
}

validate_runtime_config_semantics() {
    info "Validating runtime config semantics via installed ato_service"
    "$INSTALL_DIR/venv/bin/python" -c "
from pathlib import Path
from ato_service.runtime_config import load_runtime_config
load_runtime_config(Path('${RUNTIME_CONFIG_PATH}'))
" || err "Runtime config failed semantic validation: $RUNTIME_CONFIG_PATH"
}

validate_database_dsn_format() {
    info "Validating database DSN credential file format (contents not logged)"
    ATO_DATABASE_DSN_FILE="$DATABASE_DSN_CREDENTIAL_PATH" \
        "$INSTALL_DIR/venv/bin/python" -c "
from pathlib import Path
from ato_service.db.dsn import read_database_dsn_from_file
read_database_dsn_from_file(Path('${DATABASE_DSN_CREDENTIAL_PATH}'))
" || err "Database DSN credential file failed format validation"
}

validate_start_prerequisites() {
    if [[ "$INSTALL_SYSTEMD_UNITS" != "true" ]]; then
        err "--start requires systemd unit installation; do not pass --skip-systemd"
    fi
    [[ -f /etc/systemd/system/ato-api.service ]] \
        || err "Missing systemd unit: /etc/systemd/system/ato-api.service"
    [[ -f "$RUNTIME_CONFIG_PATH" ]] \
        || err "Missing runtime config: $RUNTIME_CONFIG_PATH (provision before --start)"
    [[ -f "$DATABASE_DSN_CREDENTIAL_PATH" ]] \
        || err "Missing database DSN credential file: $DATABASE_DSN_CREDENTIAL_PATH"
    [[ -s "$DATABASE_DSN_CREDENTIAL_PATH" ]] \
        || err "Database DSN credential file is empty: $DATABASE_DSN_CREDENTIAL_PATH"
    [[ -x "$INSTALL_DIR/venv/bin/ato-service" ]] \
        || err "Service entrypoint missing: $INSTALL_DIR/venv/bin/ato-service"
    validate_runtime_config_semantics
    validate_database_dsn_format
}

start_service_best_effort() {
    validate_start_prerequisites
    systemctl enable ato-api.service || err "Failed to enable ato-api.service"
    systemctl restart ato-api.service || systemctl start ato-api.service \
        || err "Failed to start ato-api.service (check journalctl -u ato-api -n 200 --no-pager)"
    info "ato-api.service started"
}

run_smoke_checks() {
    local smoke_script="$REPO_DIR/scripts/smoke_service_chain.sh"
    [[ -f "$smoke_script" ]] || err "Missing smoke script: $smoke_script"
    chmod +x "$smoke_script" 2>/dev/null || true
    bash "$smoke_script" || err "Smoke checks failed"
}

echo "=== ATO API Installation ==="
echo ""

check_root

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

validate_safe_path "INSTALL_DIR" "$INSTALL_DIR"
validate_safe_path "CONFIG_DIR" "$CONFIG_DIR"
validate_safe_path "DATA_DIR" "$DATA_DIR"

check_command systemctl
check_python_version "$PYTHON_BIN"

echo "[1/7] Creating service identity..."
create_user_if_missing "$SVC_USER" "$SVC_HOME"
ensure_dir "$SVC_HOME" "$SVC_USER:$SVC_USER" 700

echo "[2/7] Creating directories..."
ensure_dir "$INSTALL_DIR" "root:root" 755
ensure_dir "$CONFIG_DIR" "root:$SVC_USER" 750
ensure_dir "$DATA_DIR" "$SVC_USER:$SVC_USER" 750
ensure_dir "$DATA_DIR/_tmp" "$SVC_USER:$SVC_USER" 750

echo "[3/7] Installing application package..."
install_application_tree

echo "[4/7] Installing configuration layout..."
install_runtime_config_example
install_database_dsn_credential_layout

echo "[5/7] Installing deployment assets..."
if [[ "$INSTALL_SYSTEMD_UNITS" == "true" ]]; then
    install_systemd_unit
else
    warn "INSTALL_SYSTEMD_UNITS=false; skipping systemd unit installation"
fi

if [[ "$INSTALL_NGINX_SITE" == "true" ]]; then
    install_nginx_example
else
    warn "INSTALL_NGINX_SITE=false; skipping nginx example installation"
fi

echo "[6/7] Post-install actions..."
if [[ "$RUN_MIGRATE" == "true" ]]; then
    run_database_migrations
else
    info "Skipped database migrations (pass --migrate to run alembic upgrade head)"
fi

if [[ "$START_SERVICE" == "true" ]]; then
    start_service_best_effort
else
    info "Skipped service start (pass --start to enable and start ato-api.service)"
fi

if [[ "$RUN_SMOKE" == "true" ]]; then
    run_smoke_checks
else
    info "Skipped smoke checks (pass --smoke to run scripts/smoke_service_chain.sh)"
fi

echo ""
echo "Installation complete."
echo "  Runtime config: $RUNTIME_CONFIG_PATH"
echo "  Runtime example: $RUNTIME_CONFIG_EXAMPLE_PATH"
echo "  Database DSN credential: $DATABASE_DSN_CREDENTIAL_PATH"
echo "  API loopback: 127.0.0.1:8000 (nginx is the external listener)"
echo "  Migrations:     sudo bash scripts/install.sh --migrate"
echo "  Start service:  sudo bash scripts/install.sh --start"
echo "  Combined flow:  sudo bash scripts/install.sh --migrate --start --smoke"
