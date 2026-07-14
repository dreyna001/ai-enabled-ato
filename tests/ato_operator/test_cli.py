"""Tests for the bounded ato-operator CLI."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from ato_operator.checklist import build_operator_checklist, format_checklist
from ato_operator.cli import main
from ato_operator.preflight import OperatorPreflightReport, PreflightCheckResult
from ato_service.runtime_config import load_runtime_config_from_dict

ROOT = Path(__file__).resolve().parents[2]
ONPREM_EXAMPLE = ROOT / "deployment" / "config" / "runtime-config.onprem.example.json"
DEV_CONFIG = ROOT / "deployment" / "config" / "runtime-config.dev_local.json"
INVALID_MISSING_CAPS = (
    ROOT
    / "docs"
    / "contracts"
    / "fixtures"
    / "runtime-config.invalid.missing-process-capabilities.json"
)


def _run_cli(args: list[str]) -> tuple[int, str]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = main(args)
    return exit_code, buffer.getvalue()


def test_validate_config_accepts_dev_local(tmp_path: Path) -> None:
    config = load_runtime_config_from_dict(
        json.loads(DEV_CONFIG.read_text(encoding="utf-8")),
        base_dir=tmp_path,
    )
    assert config.runtime_profile == "dev_local"
    assert main(["validate-config", "--config", str(DEV_CONFIG)]) == 0


def test_validate_config_rejects_invalid_onprem_fixture() -> None:
    assert INVALID_MISSING_CAPS.is_file()
    assert main(["validate-config", "--config", str(INVALID_MISSING_CAPS)]) == 2


def test_validate_config_onprem_json_serializes_slotted_process_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "is_absolute", lambda self: True)
    exit_code, output = _run_cli(
        [
            "validate-config",
            "--config",
            str(ONPREM_EXAMPLE),
            "--json",
        ]
    )
    assert exit_code == 0
    payload = json.loads(output)
    assert payload["runtime_profile"] == "onprem_production"
    assert payload["process_capabilities"]["api"] is True
    assert payload["process_capabilities"]["portal_static"] is True


def test_validate_config_onprem_text_lists_active_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "is_absolute", lambda self: True)
    exit_code, output = _run_cli(
        [
            "validate-config",
            "--config",
            str(ONPREM_EXAMPLE),
        ]
    )
    assert exit_code == 0
    assert "Runtime configuration is valid." in output
    assert "active capabilities:" in output
    assert "api" in output


def test_print_checklist_includes_hard_stops() -> None:
    items = build_operator_checklist(project_root=ROOT)
    assert any(item.item_id == "HS-001" for item in items)
    text = format_checklist(items)
    assert "AIR-001" in text
    assert main(["print-checklist"]) == 0


def test_print_checklist_json_serializes_slotted_checklist_items() -> None:
    exit_code, output = _run_cli(["print-checklist", "--json"])
    assert exit_code == 0
    payload = json.loads(output)
    assert isinstance(payload, list)
    assert payload[0]["item_id"] == "CFG-001"
    assert payload[0]["category"] == "configuration"
    assert any(item["item_id"] == "HS-001" for item in payload)


def test_validate_credentials_onprem_returns_nonzero_when_checks_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "is_absolute", lambda self: True)
    exit_code, output = _run_cli(
        [
            "validate-credentials",
            "--config",
            str(ONPREM_EXAMPLE),
        ]
    )
    assert exit_code == 1
    assert "database_dsn: fail" in output
    assert "audit_hmac_key: fail" in output


def test_validate_credentials_onprem_json_reports_failure_without_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "is_absolute", lambda self: True)
    exit_code, output = _run_cli(
        [
            "validate-credentials",
            "--config",
            str(ONPREM_EXAMPLE),
            "--json",
        ]
    )
    assert exit_code == 1
    payload = json.loads(output)
    assert payload["passed"] is False
    assert any(check["status"] == "fail" for check in payload["checks"])
    serialized = json.dumps(payload)
    assert "postgresql://" not in serialized
    assert "password" not in serialized.lower()
    for check in payload["checks"]:
        assert " bytes readable" not in check["detail"]


def test_validate_credentials_returns_zero_when_required_checks_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = OperatorPreflightReport(
        checks=(
            PreflightCheckResult(
                name="database_dsn",
                status="ok",
                detail="root_owned_file:/tmp/database-dsn: 32 bytes readable",
            ),
            PreflightCheckResult(
                name="audit_hmac_key",
                status="ok",
                detail="root_owned_file:/tmp/audit-hmac-key: 32 bytes readable",
            ),
            PreflightCheckResult(
                name="database_connection",
                status="fail",
                detail="connectivity probe failed",
            ),
        ),
        passed=False,
    )
    monkeypatch.setattr(
        "ato_operator.cli.run_operator_preflight_sync",
        lambda config, project_root: report,
    )
    exit_code, output = _run_cli(
        [
            "validate-credentials",
            "--config",
            str(DEV_CONFIG),
            "--json",
        ]
    )
    assert exit_code == 0
    payload = json.loads(output)
    assert payload["passed"] is True
    assert {check["name"] for check in payload["checks"]} == {
        "database_dsn",
        "audit_hmac_key",
    }


def test_verify_migrations_dry_run_reports_head() -> None:
    config_path = DEV_CONFIG
    rc = main(["verify-migrations", "--config", str(config_path), "--dry-run"])
    assert rc == 0


def test_print_checklist_with_onprem_config() -> None:
    assert (
        main(
            [
                "print-checklist",
                "--config",
                str(ONPREM_EXAMPLE),
            ]
        )
        == 0
    )


def test_qualification_check_reports_fixture_presence() -> None:
    assert main(["qualification-check"]) == 0
    assert main(["qualification-check", "--json"]) == 0


def test_onprem_example_loads_with_process_capabilities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "is_absolute", lambda self: True)
    document = json.loads(ONPREM_EXAMPLE.read_text(encoding="utf-8"))
    config = load_runtime_config_from_dict(document, base_dir=None)
    capabilities = config.document["PROCESS_CAPABILITIES"]
    assert capabilities["api"] is True
    assert capabilities["text_model_calls"] is False
    assert "INTERNAL_EGRESS_ALLOWLIST" in config.document


def test_pyproject_declares_ato_operator_entrypoint() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'ato-operator = "ato_operator.cli:main"' in pyproject
