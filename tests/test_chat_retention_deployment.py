"""Offline contracts for the opt-in chat-retention deployment assets."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from ato_service.runtime_config import (
    RuntimeConfig,
    resolve_runtime_audit_hmac_key,
    resolve_runtime_database_dsn,
)


ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_DIR = ROOT / "deployment" / "systemd"
RETENTION_SERVICE = SYSTEMD_DIR / "ato-chat-retention.service"
RETENTION_TIMER = SYSTEMD_DIR / "ato-chat-retention.timer"
WSL_RETENTION_SERVICE = SYSTEMD_DIR / "ato-chat-retention.wsl-local.service"
INSTALL_SCRIPT = ROOT / "scripts" / "install.sh"
UPGRADE_SCRIPT = ROOT / "scripts" / "upgrade.sh"
CHAT_POLICY = ROOT / "docs" / "CHAT_MEMORY_POLICY.md"
ORGANIZATION_INPUTS = ROOT / "docs" / "ORGANIZATION_INPUTS.md"
BASH = shutil.which("bash")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _assert_hardened_oneshot(unit_text: str) -> None:
    assert "Type=oneshot" in unit_text
    assert "User=ato" in unit_text
    assert "Group=ato" in unit_text
    assert "NoNewPrivileges=yes" in unit_text
    assert "CapabilityBoundingSet=" in unit_text
    assert "AmbientCapabilities=" in unit_text
    assert "ProtectSystem=strict" in unit_text
    assert "ProtectHome=yes" in unit_text
    assert "PrivateDevices=yes" in unit_text
    assert "PrivateTmp=yes" in unit_text
    assert "RestrictSUIDSGID=yes" in unit_text
    assert "RestrictRealtime=yes" in unit_text
    assert "MemoryMax=512M" in unit_text
    assert "CPUQuota=50%" in unit_text
    assert "TimeoutStartSec=15min" in unit_text
    assert "Restart=no" in unit_text
    assert "ExecStart=-" not in unit_text
    assert "SuccessExitStatus=" not in unit_text
    assert "ReadWritePaths=" not in unit_text
    assert "EnvironmentFile=" not in unit_text
    assert "StandardOutput=journal" in unit_text
    assert "StandardError=journal" in unit_text
    assert "SyslogIdentifier=ato-chat-retention" in unit_text
    assert "postgresql://" not in unit_text.lower()
    assert "password=" not in unit_text.lower()
    assert "bearer " not in unit_text.lower()


def test_retention_deployment_assets_exist() -> None:
    for path in (RETENTION_SERVICE, RETENTION_TIMER, WSL_RETENTION_SERVICE):
        assert path.is_file(), f"missing retention deployment asset: {path}"


def test_onprem_retention_service_is_bounded_and_uses_only_required_credentials() -> None:
    text = _read(RETENTION_SERVICE)

    _assert_hardened_oneshot(text)
    assert "Environment=ATO_RUNTIME_CONFIG_PATH=/etc/ato-analyzer/runtime-config.json" in text
    assert (
        "ExecStart=/opt/ato-analyzer/venv/bin/ato-operator purge-chat "
        "--config /etc/ato-analyzer/runtime-config.json --json"
    ) in text
    assert (
        "LoadCredential=database-dsn:/etc/ato-analyzer/credentials/database-dsn"
        in text
    )
    assert (
        "LoadCredential=audit-hmac-key:/etc/ato-analyzer/credentials/audit-hmac-key"
        in text
    )
    for forbidden in ("oidc-client-secret", "text-model", "backup-encryption-key"):
        assert forbidden not in text.lower()


def test_wsl_retention_service_overrides_only_the_runtime_config_path() -> None:
    text = _read(WSL_RETENTION_SERVICE)

    _assert_hardened_oneshot(text)
    assert "Environment=ATO_RUNTIME_CONFIG_PATH=/opt/ato-analyzer/runtime-config.json" in text
    assert (
        "ExecStart=/opt/ato-analyzer/venv/bin/ato-operator purge-chat "
        "--config /opt/ato-analyzer/runtime-config.json --json"
    ) in text
    assert "/etc/ato-analyzer/runtime-config.json" not in text
    assert "LoadCredential=database-dsn:/etc/ato-analyzer/credentials/database-dsn" in text
    assert "LoadCredential=audit-hmac-key:/etc/ato-analyzer/credentials/audit-hmac-key" in text
    for forbidden in ("oidc-client-secret", "text-model", "backup-encryption-key"):
        assert forbidden not in text.lower()


def test_timer_runs_one_bounded_service_daily_and_is_not_a_second_scheduler() -> None:
    text = _read(RETENTION_TIMER)

    assert "OnCalendar=daily" in text
    assert "Persistent=true" in text
    assert "Unit=ato-chat-retention.service" in text
    assert "OnUnitActiveSec=" not in text
    assert "ato-chat-retention.service" in text
    assert "WantedBy=timers.target" in text

    service_text = _read(RETENTION_SERVICE)
    assert "Type=oneshot" in service_text
    assert "RemainAfterExit=true" not in service_text
    assert "[Install]" not in service_text


def test_install_and_upgrade_preserve_opt_in_and_drain_before_reinstall() -> None:
    install_text = _read(INSTALL_SCRIPT)
    upgrade_text = _read(UPGRADE_SCRIPT)

    assert '"ato-chat-retention.service"' in install_text
    assert '"ato-chat-retention.timer"' in install_text
    assert 'systemctl disable "$unit"' in install_text
    assert 'retention_timer_was_enabled=false' in install_text
    assert 'systemctl enable "$unit"' in install_text
    assert 'systemctl disable --now "$unit"' in install_text
    assert 'reject_non_regular_existing_file "$dest"' in install_text

    assert "ato-chat-retention.wsl-local.service" in upgrade_text
    assert 'install_wsl_chat_retention_units' in upgrade_text
    assert "ato-chat-retention.timer" in upgrade_text
    assert "chat_retention_unit_has_job" in upgrade_text
    assert "chat_retention_timer_was_enabled=false" in upgrade_text
    assert "chat_retention_timer_was_active=false" in upgrade_text
    assert "systemctl stop ato-chat-retention.timer" in upgrade_text
    assert "systemctl stop ato-chat-retention.service" in upgrade_text
    assert 'Failed to stop ato-chat-retention.timer before upgrade' in upgrade_text
    assert 'Failed to stop ato-chat-retention.service before upgrade' in upgrade_text
    assert "restore_chat_retention_state" in upgrade_text
    assert "systemctl enable ato-chat-retention.timer" in upgrade_text
    assert "systemctl disable ato-chat-retention.timer" in upgrade_text
    assert "systemctl disable ato-chat-retention.timer 2>/dev/null || true" not in upgrade_text
    assert "systemctl enable ato-chat-retention.service" not in upgrade_text

    upgrade_body_start = upgrade_text.index('api_was_active=false')
    stop_call = upgrade_text.index(
        "stop_chat_retention_before_upgrade\n", upgrade_body_start
    )
    reinstall_call = upgrade_text.index('bash "$SCRIPT_DIR/install.sh"', stop_call)
    assert stop_call < reinstall_call

    wsl_restore = upgrade_text.index("install_wsl_chat_retention_units", reinstall_call)
    wsl_reload = upgrade_text.index(
        "systemctl daemon-reload || err \"Failed to reload systemd after WSL unit restore\"",
        wsl_restore,
    )
    wsl_state_restore = upgrade_text.index("restore_chat_retention_state", wsl_reload)
    assert wsl_reload < wsl_state_restore


def test_systemd_credentials_resolve_through_purge_runtime_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_dir = tmp_path / "credentials"
    credentials_dir.mkdir()
    dsn_path = credentials_dir / "database-dsn"
    audit_path = credentials_dir / "audit-hmac-key"
    dsn_path.write_text("postgresql+psycopg://ato@127.0.0.1:5432/ato\n", encoding="utf-8")
    audit_path.write_bytes(b"k" * 32)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credentials_dir))

    config = RuntimeConfig(
        runtime_profile="dev_local",
        storage_data_path=tmp_path,
        document={
            "DATABASE_DSN_CREDENTIAL_REFERENCE": {
                "source": "systemd_credential",
                "identifier": "database-dsn",
            },
            "AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE": {
                "source": "systemd_credential",
                "identifier": "audit-hmac-key",
            },
        },
    )

    assert resolve_runtime_database_dsn(config) == (
        "postgresql+psycopg://ato@127.0.0.1:5432/ato"
    )
    assert resolve_runtime_audit_hmac_key(config) == b"k" * 32


@pytest.mark.skipif(BASH is None, reason="bash is required for shell contract checks")
@pytest.mark.parametrize("script", [INSTALL_SCRIPT, UPGRADE_SCRIPT])
def test_install_and_upgrade_dry_run_are_side_effect_free(script: Path) -> None:
    candidates = [
        sys.executable,
        shutil.which("python3.12"),
    ]
    python_bin = next(
        (
            candidate
            for candidate in candidates
            if candidate
            and subprocess.run(
                [candidate, "-c", "import sys; raise SystemExit(sys.version_info < (3, 12))"],
                capture_output=True,
                check=False,
            ).returncode
            == 0
        ),
        None,
    )
    if python_bin is None:
        pytest.skip("Python 3.12+ is unavailable for installer dry-run")

    env = os.environ.copy()
    env["PYTHON_BIN"] = python_bin
    result = subprocess.run(
        [BASH, str(script), "--dry-run"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout.lower()
    assert "ato-chat-retention" in result.stdout


@pytest.mark.skipif(BASH is None, reason="bash is required for shell syntax checks")
@pytest.mark.parametrize("script", [INSTALL_SCRIPT, UPGRADE_SCRIPT])
def test_install_and_upgrade_pass_bash_syntax_check(script: Path) -> None:
    result = subprocess.run(
        [BASH, "-n", str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_policy_separates_code_ready_behavior_from_organization_inputs() -> None:
    policy_text = _read(CHAT_POLICY)
    inputs_text = _read(ORGANIZATION_INPUTS)

    assert "## Delivery boundary" in policy_text
    assert "Code-ready in this repository" in policy_text
    assert "Needs organization input or host validation" in policy_text
    assert "ORGANIZATION_INPUTS.md" in policy_text
    for required in (
        "authority",
        "model data approval",
        "data-classification decision",
        "accepted labels",
        "assert or change",
        "provenance/evidence",
        "audit requirements",
        "governed classification implementation",
        "issuer and key roots",
        "key rotation",
        "publisher allowlist",
        "SME dataset",
        "IdP issuer",
        "ClamAV",
        "template acceptance",
        "backup, restore",
        "retention and legal holds",
        "customer retention",
        "FedRAMP assessor-owned inputs",
    ):
        assert required.lower() in inputs_text.lower()
    assert "not an approval record" in inputs_text.lower()
