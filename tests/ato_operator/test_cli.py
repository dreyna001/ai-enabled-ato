"""Tests for the bounded ato-operator CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ato_operator.checklist import build_operator_checklist, format_checklist
from ato_operator.cli import main
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


def test_print_checklist_includes_hard_stops() -> None:
    items = build_operator_checklist(project_root=ROOT)
    assert any(item.item_id == "HS-001" for item in items)
    text = format_checklist(items)
    assert "AIR-001" in text
    assert main(["print-checklist"]) == 0


def test_verify_migrations_dry_run_reports_head() -> None:
    config_path = DEV_CONFIG
    rc = main(["verify-migrations", "--config", str(config_path), "--dry-run"])
    assert rc == 0


def test_qualification_check_reports_fixture_presence() -> None:
    assert main(["qualification-check"]) == 0


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
