"""Deployment asset contract tests for the ATO API operator surface."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SYSTEMD_UNIT = ROOT / "deployment" / "systemd" / "ato-api.service"
WSL_SYSTEMD_UNIT = ROOT / "deployment" / "systemd" / "ato-api.wsl-local.service"
SYNTHETIC_WORKER_UNIT = (
    ROOT / "deployment" / "systemd" / "ato-synthetic-intake-worker.service"
)
SYNTHETIC_WORKER_TIMER = (
    ROOT / "deployment" / "systemd" / "ato-synthetic-intake-worker.timer"
)
NGINX_CONF = ROOT / "deployment" / "nginx" / "ato-api.conf"
INSTALL_SCRIPT = ROOT / "scripts" / "install.sh"
WSL_DEPLOY_SCRIPT = ROOT / "scripts" / "wsl-local-deploy.sh"
SMOKE_SCRIPT = ROOT / "scripts" / "smoke_service_chain.sh"
WSL_RUNTIME_CONFIG = ROOT / "deployment" / "config" / "runtime-config.wsl_local.json"
WSL_PORTAL_RUNTIME_CONFIG = ROOT / "deployment" / "config" / "runtime-config.wsl_portal.json"
WSL_PORTAL_ENABLE_SCRIPT = ROOT / "scripts" / "wsl-portal-enable.sh"
DEPLOYMENT_README = ROOT / "deployment" / "README.md"
PYPROJECT = ROOT / "pyproject.toml"
MAIN_MODULE = ROOT / "src" / "ato_service" / "main.py"
RUNTIME_CONFIG_EXAMPLE_SRC = (
    ROOT / "deployment" / "config" / "runtime-config.onprem.example.json"
)

DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8000
INSTALL_DIR = "/opt/ato-analyzer"
CONFIG_DIR = "/etc/ato-analyzer"
DATA_DIR = "/var/ato-packages"
SVC_HOME = "/var/lib/ato"
RUNTIME_CONFIG_PATH = f"{CONFIG_DIR}/runtime-config.json"
RUNTIME_CONFIG_EXAMPLE_PATH = f"{CONFIG_DIR}/runtime-config.onprem.example.json"
DATABASE_DSN_CREDENTIAL_PATH = f"{CONFIG_DIR}/credentials/database-dsn"
DATABASE_DSN_IDENTIFIER = "database-dsn"
AUDIT_HMAC_CREDENTIAL_PATH = f"{CONFIG_DIR}/credentials/audit-hmac-key"
AUDIT_HMAC_IDENTIFIER = "audit-hmac-key"
NGINX_EXAMPLE_DEST = "/etc/nginx/conf.d/ato-api.conf.example"

FORBIDDEN_SECRET_PATTERNS = (
    re.compile(r"postgresql://", re.IGNORECASE),
    re.compile(r"postgres://", re.IGNORECASE),
    re.compile(r"password\s*=", re.IGNORECASE),
    re.compile(r"secret\s*=", re.IGNORECASE),
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def systemd_text() -> str:
    return _read(SYSTEMD_UNIT)


@pytest.fixture(scope="module")
def nginx_text() -> str:
    return _read(NGINX_CONF)


@pytest.fixture(scope="module")
def install_text() -> str:
    return _read(INSTALL_SCRIPT)


@pytest.fixture(scope="module")
def smoke_text() -> str:
    return _read(SMOKE_SCRIPT)


@pytest.fixture(scope="module")
def deployment_readme_text() -> str:
    return _read(DEPLOYMENT_README)


@pytest.mark.parametrize(
    "path",
    [
        SYSTEMD_UNIT,
        NGINX_CONF,
        INSTALL_SCRIPT,
        SMOKE_SCRIPT,
        RUNTIME_CONFIG_EXAMPLE_SRC,
        WSL_SYSTEMD_UNIT,
        SYNTHETIC_WORKER_UNIT,
        SYNTHETIC_WORKER_TIMER,
        WSL_DEPLOY_SCRIPT,
        WSL_RUNTIME_CONFIG,
        WSL_PORTAL_RUNTIME_CONFIG,
        WSL_PORTAL_ENABLE_SCRIPT,
    ],
)
def test_deployment_assets_exist(path: Path) -> None:
    assert path.is_file(), f"missing deployment asset: {path}"


@pytest.mark.parametrize(
    "path",
    [
        SYSTEMD_UNIT,
        NGINX_CONF,
        INSTALL_SCRIPT,
        SMOKE_SCRIPT,
        WSL_DEPLOY_SCRIPT,
        WSL_RUNTIME_CONFIG,
        WSL_PORTAL_RUNTIME_CONFIG,
        WSL_PORTAL_ENABLE_SCRIPT,
    ],
)
def test_deployment_assets_contain_no_secret_like_values(path: Path) -> None:
    text = _read(path)
    for pattern in FORBIDDEN_SECRET_PATTERNS:
        assert not pattern.search(text), (
            f"{path.name} must not contain secret-like material matching {pattern.pattern}"
        )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
@pytest.mark.parametrize(
    "script",
    [INSTALL_SCRIPT, SMOKE_SCRIPT, WSL_DEPLOY_SCRIPT, WSL_PORTAL_ENABLE_SCRIPT],
)
def test_shell_scripts_pass_bash_syntax_check(script: Path) -> None:
    script_text = _read(script).replace("\r\n", "\n").replace("\r", "\n")
    result = subprocess.run(
        ["bash", "-n", "-s"],
        input=script_text.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def test_pyproject_declares_service_and_worker_entrypoints() -> None:
    text = _read(PYPROJECT)
    assert 'ato-service = "ato_service.main:main"' in text
    assert 'ato-intake-worker = "ato_service.intake_worker:main"' in text
    assert (
        'ato-analyzer-worker = "ato_service.deterministic_analyzer_worker:main"'
        in text
    )


def test_pyproject_declares_approved_extraction_dependencies() -> None:
    """Approved extraction libraries are pinned per PACKAGE_EDITOR_PLAN Section 5."""
    text = _read(PYPROJECT)
    required_pins = (
        "pypdf==6.14.2",
        "pypdfium2==5.11.0",
        "python-docx==1.2.0",
        "openpyxl==3.1.5",
        "defusedxml==0.7.1",
        "lxml==6.0.2",
        "Pillow==12.2.0",
        "authlib==1.6.1",
    )
    for pin in required_pins:
        assert pin in text, f"missing approved extraction dependency pin: {pin}"
    forbidden = ("pdfminer", "tika")
    lowered = text.lower()
    for name in forbidden:
        assert name not in lowered, f"forbidden extraction dependency present: {name}"
    assert "stdlib cannot decode PDF" not in text
    assert "primary PDF text-layer extraction" in text
    assert "python-docx: mature DOCX" in text
    assert "defusedxml: hostile-XML" in text


def test_main_module_defaults_to_loopback(systemd_text: str) -> None:
    main_text = _read(MAIN_MODULE)
    assert 'DEFAULT_HOST = "127.0.0.1"' in main_text
    assert "DEFAULT_PORT = 8000" in main_text
    assert f"Environment=ATO_HOST={DEFAULT_API_HOST}" in systemd_text
    assert f"Environment=ATO_PORT={DEFAULT_API_PORT}" in systemd_text


def test_systemd_unit_labels_api_not_portal(systemd_text: str) -> None:
    assert "Portal" not in systemd_text
    assert "ATO Evidence Analysis API" in systemd_text


def test_systemd_unit_runs_unprivileged_ato_service(systemd_text: str) -> None:
    assert "User=ato" in systemd_text
    assert "Group=ato" in systemd_text
    assert f"ExecStart={INSTALL_DIR}/venv/bin/ato-service" in systemd_text
    assert "Restart=on-failure" in systemd_text
    assert "TimeoutStopSec=30" in systemd_text
    assert "StandardOutput=journal" in systemd_text
    assert "StandardError=journal" in systemd_text
    assert "SyslogIdentifier=ato-api" in systemd_text


def test_systemd_unit_points_to_canonical_runtime_config(systemd_text: str) -> None:
    assert (
        f"Environment=ATO_RUNTIME_CONFIG_PATH={RUNTIME_CONFIG_PATH}" in systemd_text
    )


def test_systemd_unit_wires_api_consumed_credential_references(
    systemd_text: str,
) -> None:
    assert (
        f"LoadCredential={DATABASE_DSN_IDENTIFIER}:{DATABASE_DSN_CREDENTIAL_PATH}"
        in systemd_text
    )
    assert (
        f"LoadCredential={AUDIT_HMAC_IDENTIFIER}:{AUDIT_HMAC_CREDENTIAL_PATH}"
        in systemd_text
    )
    assert "CREDENTIALS_DIRECTORY" not in systemd_text
    assert "LoadCredential=oidc" not in systemd_text.lower()
    assert "LoadCredential=backup" not in systemd_text.lower()


def test_systemd_unit_orders_after_network_online_without_postgresql_unit(
    systemd_text: str,
) -> None:
    assert "After=network-online.target" in systemd_text
    assert "Wants=network-online.target" in systemd_text
    assert "postgresql.service" not in systemd_text


def test_systemd_unit_disables_bytecode_for_root_owned_code(systemd_text: str) -> None:
    assert "Environment=PYTHONDONTWRITEBYTECODE=1" in systemd_text


def test_systemd_unit_hardens_service_and_declares_writable_storage(
    systemd_text: str,
) -> None:
    for directive in (
        "NoNewPrivileges=yes",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "PrivateTmp=yes",
        f"ReadWritePaths={DATA_DIR}",
    ):
        assert directive in systemd_text


def test_systemd_units_include_production_api_intake_and_wsl_local_assets() -> None:
    systemd_dir = ROOT / "deployment" / "systemd"
    service_names = {path.name for path in systemd_dir.glob("*.service")}
    timer_names = {path.name for path in systemd_dir.glob("*.timer")}
    assert service_names == {
        "ato-api.service",
        "ato-api.wsl-local.service",
        "ato-intake-worker.service",
        "ato-synthetic-intake-worker.service",
    }
    assert timer_names == {"ato-synthetic-intake-worker.timer"}
    for forbidden in ("portal", "model", "nginx"):
        for name in service_names:
            assert forbidden not in name


def test_wsl_systemd_unit_uses_dev_local_runtime_config_under_opt() -> None:
    text = _read(WSL_SYSTEMD_UNIT)
    assert (
        "Environment=ATO_RUNTIME_CONFIG_PATH=/opt/ato-analyzer/runtime-config.json"
        in text
    )
    assert f"LoadCredential={DATABASE_DSN_IDENTIFIER}:{DATABASE_DSN_CREDENTIAL_PATH}" in text
    assert f"LoadCredential={AUDIT_HMAC_IDENTIFIER}:{AUDIT_HMAC_CREDENTIAL_PATH}" in text
    assert "LoadCredential=oidc-client-secret:" in text
    assert "EnvironmentFile=-/etc/ato-analyzer/credentials/ato-local.env" in text
    assert f"ReadWritePaths={DATA_DIR} /opt/ato-analyzer/data/ato-storage" in text


def test_wsl_runtime_config_is_dev_local_with_systemd_credentials() -> None:
    text = _read(WSL_RUNTIME_CONFIG)
    assert '"runtime_profile": "dev_local"' in text
    assert '"STORAGE_DATA_PATH": "/data/ato-storage"' in text
    assert '"source": "systemd_credential"' in text
    assert '"identifier": "database-dsn"' in text
    assert '"identifier": "audit-hmac-key"' in text


def test_wsl_portal_runtime_config_declares_openai_text_model() -> None:
    text = _read(WSL_PORTAL_RUNTIME_CONFIG)
    assert '"TEXT_MODEL_PROVIDER": "openai_compatible"' in text
    assert '"TEXT_MODEL_NAME": "gpt-4.1"' in text
    assert '"TEXT_MODEL_ENDPOINT_URL": "https://api.openai.com/v1"' in text
    assert '"TEXT_MODEL_ENDPOINT_PROFILE": "external_openai"' in text
    assert '"TEXT_MODEL_TEMPERATURE": 0' in text
    assert '"TEXT_MODEL_ENDPOINT_POLICY_APPROVED": false' in text
    assert '"CUI_MODEL_BOUNDARY_APPROVED": false' in text
    assert '"TEXT_MODEL_CREDENTIAL_REFERENCE"' not in text


def test_wsl_portal_openai_example_config_not_shipped() -> None:
    """OpenAI settings live in runtime-config.wsl_portal.json only."""
    redundant = (
        ROOT / "deployment" / "config" / "runtime-config.wsl_portal.openai.example.json"
    )
    assert not redundant.is_file(), "use runtime-config.wsl_portal.json instead"


def test_wsl_portal_enable_script_installs_local_env_file() -> None:
    text = _read(WSL_PORTAL_ENABLE_SCRIPT)
    assert "runtime-config.wsl_portal.json" in text
    assert "config.local.env" in text
    assert "ato-local.env" in text
    assert "install_local_env_file" in text
    assert "ATO_TEXT_MODEL_API_KEY" in text
    assert "bind_package_storage" in text


def test_wsl_deploy_script_reuses_installer_and_smoke_chain() -> None:
    text = _read(WSL_DEPLOY_SCRIPT)
    assert "scripts/install.sh" in text
    assert "--skip-nginx" in text
    assert "--skip-systemd" in text
    assert "runtime-config.wsl_local.json" in text
    assert "mount --bind" in text
    assert "ALLOW_DEGRADED_READY=true" in text
    assert "wait_for_api_loopback" in text
    assert "ato-synthetic-intake-worker.timer" in text


def test_nginx_template_is_tls_edge_with_loopback_proxy(nginx_text: str) -> None:
    assert "listen 443 ssl" in nginx_text
    assert "ssl_certificate " in nginx_text
    assert "ssl_certificate_key " in nginx_text
    assert f"proxy_pass http://{DEFAULT_API_HOST}:{DEFAULT_API_PORT}" in nginx_text
    assert "ato-api.internal.example.com" in nginx_text


def test_nginx_template_exposes_health_endpoints_only(nginx_text: str) -> None:
    assert "location = /health/live" in nginx_text
    assert "location = /health/ready" in nginx_text
    assert "return 404" in nginx_text
    assert "location /api/" not in nginx_text


def test_nginx_template_sets_fixed_upstream_host(nginx_text: str) -> None:
    assert "proxy_set_header Host 127.0.0.1:8000;" in nginx_text
    assert "proxy_set_header Host $http_host;" not in nginx_text


def test_nginx_template_has_no_port_80_listener(nginx_text: str) -> None:
    assert "listen 80" not in nginx_text


def test_nginx_template_does_not_imply_oidc_or_basic_auth(nginx_text: str) -> None:
    lowered = nginx_text.lower()
    assert "oidc" not in lowered
    assert "auth_basic" not in lowered
    assert "identity-proxy" not in lowered


def test_nginx_template_sets_security_headers(nginx_text: str) -> None:
    assert "Strict-Transport-Security" in nginx_text
    assert "X-Content-Type-Options" in nginx_text
    assert "X-Frame-Options" in nginx_text
    assert "Referrer-Policy" in nginx_text
    assert "Content-Security-Policy" in nginx_text


def test_install_script_is_idempotent_and_explicit_about_side_effects(
    install_text: str,
) -> None:
    assert "set -euo pipefail" in install_text
    assert 'SVC_USER="ato"' in install_text
    assert "create_user_if_missing" in install_text
    assert "not overwritten" in install_text
    assert "--start" in install_text
    assert "--smoke" in install_text
    assert "--migrate" in install_text
    assert "START_SERVICE=false" in install_text
    assert "RUN_SMOKE=false" in install_text
    assert "RUN_MIGRATE=false" in install_text
    assert "systemctl daemon-reload" in install_text
    assert "validate_safe_path" in install_text
    assert CONFIG_DIR in install_text
    assert "runtime-config.json" in install_text
    assert "database-dsn" in install_text
    assert "deployment/systemd/" in install_text
    assert "ato-api.service" in install_text
    assert "deployment/nginx/ato-api.conf" in install_text


def test_install_script_does_not_auto_start_migrate_or_smoke_by_default(
    install_text: str,
) -> None:
    assert "AUTO_START" not in install_text
    assert 'if [[ "$START_SERVICE" == "true" ]]' in install_text
    assert 'if [[ "$RUN_SMOKE" == "true" ]]' in install_text
    assert 'if [[ "$RUN_MIGRATE" == "true" ]]' in install_text


def test_install_script_does_not_shell_source_config_or_log_secrets(
    install_text: str,
) -> None:
    assert 'source "' not in install_text
    assert "echo \"$" not in install_text


def test_install_script_keeps_application_code_root_owned(install_text: str) -> None:
    assert "set_install_tree_permissions" in install_text
    assert 'chown -R root:root "$INSTALL_DIR"' in install_text
    assert f'chown -R "$SVC_USER:$SVC_USER" "$INSTALL_DIR"' not in install_text
    assert f'ensure_dir "$INSTALL_DIR" "root:root" 755' in install_text
    assert f'ensure_dir "$DATA_DIR" "$SVC_USER:$SVC_USER" 750' in install_text
    assert f'create_user_if_missing "$SVC_USER" "$SVC_HOME"' in install_text


def test_install_script_uses_normal_pip_install_without_upgrade(install_text: str) -> None:
    assert 'pip" install "$INSTALL_DIR"' in install_text
    assert 'pip" install --force-reinstall --no-deps "$INSTALL_DIR"' in install_text
    assert "pip install -e" not in install_text
    assert "upgrade pip" not in install_text
    assert "--upgrade" not in install_text


def test_install_script_rejects_symlinks_for_fixed_layout_paths(install_text: str) -> None:
    assert "reject_unsafe_existing_path" in install_text
    assert "must be a directory, not a symlink" in install_text
    for path_fragment in (
        f'ensure_dir "$INSTALL_DIR"',
        f'ensure_dir "$CONFIG_DIR"',
        f'ensure_dir "$DATA_DIR"',
        f'ensure_dir "$DATA_DIR/_tmp"',
        f'ensure_dir "$CREDENTIALS_DIR"',
        f'ensure_dir "$SVC_HOME"',
    ):
        assert path_fragment in install_text


def test_install_script_validates_runtime_config_and_dsn_before_start(
    install_text: str,
) -> None:
    assert "validate_runtime_config_semantics" in install_text
    assert "validate_database_dsn_format" in install_text
    assert "load_runtime_config" in install_text
    assert "read_database_dsn_from_file" in install_text
    assert "contents not logged" in install_text
    assert install_text.index("validate_runtime_config_semantics") < install_text.index(
        "start_service_best_effort"
    )


def test_install_script_requires_start_when_smoke_requested(install_text: str) -> None:
    assert "--smoke requires --start in the same invocation" in install_text
    assert "--migrate --start --smoke" in install_text


def test_install_script_copies_required_runtime_assets(install_text: str) -> None:
    for fragment in (
        "README.md",
        "alembic.ini",
        "migrations",
        "docs/contracts",
        "docs/OPERATIONS_AND_RECOVERY.md",
        "reference",
    ):
        assert fragment in install_text


def test_install_script_normalizes_crlf_on_destinations_only(install_text: str) -> None:
    assert "normalize_dest_file_crlf" in install_text
    assert "strip_crlf_in_file_best_effort" not in install_text
    assert 'normalize_dest_file_crlf "$dest"' in install_text
    assert 'normalize_dest_file_crlf "$src"' not in install_text


def test_install_script_installs_inactive_runtime_config_example(
    install_text: str,
) -> None:
    assert "runtime-config.onprem.example.json" in install_text
    assert 'RUNTIME_CONFIG_EXAMPLE_PATH="$CONFIG_DIR/runtime-config.onprem.example.json"' in install_text
    assert "enforce_existing_regular_file" in install_text
    assert 'enforce_existing_regular_file "$RUNTIME_CONFIG_PATH" "root:$SVC_USER" 640' in install_text
    assert (
        'enforce_existing_regular_file "$RUNTIME_CONFIG_EXAMPLE_PATH" "root:$SVC_USER" 640'
        in install_text
    )
    assert (
        'enforce_existing_regular_file "$DATABASE_DSN_CREDENTIAL_PATH" "root:root" 600'
        in install_text
    )
    assert f'cp "$src" "$RUNTIME_CONFIG_PATH"' not in install_text


def test_install_script_rejects_symlink_or_non_regular_destination_files(
    install_text: str,
) -> None:
    assert "reject_non_regular_existing_file" in install_text
    assert '[[ -e "$path" || -L "$path" ]]' in install_text
    assert 'reject_non_regular_existing_file "$dest"' in install_text
    assert 'reject_non_regular_existing_file "$RUNTIME_CONFIG_PATH"' in install_text
    assert 'reject_non_regular_existing_file "$RUNTIME_CONFIG_EXAMPLE_PATH"' in install_text
    assert 'reject_non_regular_existing_file "$DATABASE_DSN_CREDENTIAL_PATH"' in install_text
    assert 'enforce_existing_regular_file "$dest" "root:root" 644' in install_text


def test_install_script_installs_nginx_example_not_active_conf(
    install_text: str,
) -> None:
    assert NGINX_EXAMPLE_DEST in install_text
    assert "install_nginx_example" in install_text
    assert "/etc/nginx/conf.d/ato-api.conf\"" not in install_text


def test_install_script_runs_migrations_only_with_explicit_flag(
    install_text: str,
) -> None:
    assert "run_database_migrations" in install_text
    assert "ATO_DATABASE_DSN_FILE" in install_text
    assert "alembic" in install_text
    assert "upgrade head" in install_text
    assert "validate_migration_prerequisites" in install_text


def test_install_script_rejects_start_when_systemd_skipped(install_text: str) -> None:
    assert (
        'err "--start requires systemd unit installation; do not pass --skip-systemd"'
        in install_text
    )
    assert 'if [[ "$INSTALL_SYSTEMD_UNITS" != "true" ]]; then' in install_text


def test_install_script_validates_boolean_environment_overrides(
    install_text: str,
) -> None:
    assert 'INSTALL_SYSTEMD_UNITS="${INSTALL_SYSTEMD_UNITS:-true}"' in install_text
    assert 'INSTALL_NGINX_SITE="${INSTALL_NGINX_SITE:-true}"' in install_text
    assert 'validate_boolean "INSTALL_SYSTEMD_UNITS"' in install_text
    assert 'validate_boolean "INSTALL_NGINX_SITE"' in install_text


def test_smoke_script_targets_health_endpoints_with_bounded_retries(
    smoke_text: str,
) -> None:
    assert "/health/live" in smoke_text
    assert "/health/ready" in smoke_text
    assert "HTTP_TIMEOUT_SECONDS" in smoke_text
    assert "READY_RETRIES" in smoke_text
    assert "READY_RETRY_SECONDS" in smoke_text
    assert "--max-time" in smoke_text
    assert "gemma" not in smoke_text.lower()
    assert "vllm" not in smoke_text.lower()
    assert "litellm" not in smoke_text.lower()
    assert "chat/completions" not in smoke_text
    assert "Authorization" not in smoke_text


def test_smoke_script_requires_ready_http_200_by_default(smoke_text: str) -> None:
    assert "ALLOW_DEGRADED_READY" in smoke_text
    assert 'ALLOW_DEGRADED_READY="${ALLOW_DEGRADED_READY:-false}"' in smoke_text
    assert "Readiness probe passed" in smoke_text
    assert "completed with degraded readiness; not release-ready" in smoke_text
    assert "200|503" not in smoke_text


def test_smoke_script_validates_health_json_without_logging_bodies(
    smoke_text: str,
) -> None:
    assert "validate_live_json" in smoke_text
    assert "validate_ready_ok_json" in smoke_text
    assert "validate_ready_problem_json" in smoke_text
    assert 'EXPECTED = {"status": "ok", "checks": {"process": "ok"}}' in smoke_text
    assert "if payload != EXPECTED:" in smoke_text
    assert 'checks.get(name) != "ok"' in smoke_text
    assert "set(checks.keys()) != set(READINESS_CHECK_NAMES)" in smoke_text
    assert "ALLOWED_READINESS_ERROR_CODES" in smoke_text
    assert "reconciliation_required" in smoke_text
    assert 'payload.get("instance") != "/health/ready"' in smoke_text
    assert "error_code" in smoke_text
    assert "request_id" in smoke_text
    assert "echo \"$body" not in smoke_text
    assert "cat \"$body" not in smoke_text


def test_smoke_script_uses_python_stdlib_for_json_validation(smoke_text: str) -> None:
    assert "resolve_smoke_python" in smoke_text
    assert "import json" in smoke_text
    assert "python3.12" in smoke_text


def test_smoke_script_validates_urls_and_numeric_overrides(smoke_text: str) -> None:
    assert "validate_base_url" in smoke_text
    assert "validate_positive_integer" in smoke_text
    assert "userinfo, query, or fragment" in smoke_text
    assert "must not include leading or trailing whitespace" in smoke_text
    assert "has an empty host" in smoke_text
    assert 'validate_boolean "ALLOW_DEGRADED_READY"' in smoke_text
    assert "must not include userinfo, query, or fragment: $" not in smoke_text
    assert "must be loopback HTTP or HTTPS: $" not in smoke_text


def test_smoke_script_cleans_up_temp_response_files_on_exit(smoke_text: str) -> None:
    assert "SMOKE_BODY_FILE" in smoke_text
    assert "cleanup_smoke_body_file" in smoke_text
    assert "trap cleanup_smoke_body_file EXIT" in smoke_text
    assert "release_smoke_body_file" in smoke_text
    assert 'body_file="$(make_temp_body_file)"' not in smoke_text
    assert 'body_file="$SMOKE_BODY_FILE"' in smoke_text


def test_smoke_script_does_not_disable_tls_verification(smoke_text: str) -> None:
    assert "curl -k" not in smoke_text
    assert "--insecure" not in smoke_text


def test_smoke_script_defaults_to_loopback_api(smoke_text: str) -> None:
    assert "http://127.0.0.1:8000" in smoke_text


def test_deployment_readme_matches_current_installer_contract(
    deployment_readme_text: str,
) -> None:
    assert "`ato-intake-worker`" in deployment_readme_text
    assert "scripts/wsl-local-deploy.sh" in deployment_readme_text
    assert (
        "the installer does not deploy, configure, start, or credential"
        in deployment_readme_text
    )
    assert NGINX_EXAMPLE_DEST in deployment_readme_text
    assert "/etc/nginx/conf.d/ato-api.conf\n" not in deployment_readme_text
    assert "location /api/" not in deployment_readme_text
    assert "--migrate --start --smoke" in deployment_readme_text
    assert "requires `--start`" in deployment_readme_text
    assert "alembic.ini" in deployment_readme_text
    assert "migrations/" in deployment_readme_text
    assert "`database-dsn` and `audit-hmac-key`" in deployment_readme_text
    assert '"checks":{"process":"ok"}' in deployment_readme_text
    assert "reconciliation_required" in deployment_readme_text
    assert "instance: /health/ready" in deployment_readme_text
    assert "completed with degraded readiness; not release-ready" in deployment_readme_text
    assert "ALLOW_DEGRADED_READY=true" in deployment_readme_text
    assert "not a release gate" in deployment_readme_text
    assert "alembic upgrade head` manually" not in deployment_readme_text
    assert "future `--migrate`" not in deployment_readme_text
    assert "# 3. Apply database migrations explicitly" not in deployment_readme_text
    assert "# 4. Start API when prerequisites exist" not in deployment_readme_text
    assert "Production-readiness:" in deployment_readme_text
    assert "bash scripts/smoke_service_chain.sh" in deployment_readme_text
    assert "docs/WSL_LOCAL_DEPLOY.md" in deployment_readme_text
    assert "scripts/wsl-local-deploy.sh" in deployment_readme_text
