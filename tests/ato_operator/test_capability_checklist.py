"""Tests for per-capability onboarding checklist and topology projection."""

from __future__ import annotations

import json
from pathlib import Path

from ato_operator.capability_topology import build_capability_topology
from ato_operator.checklist import (
    build_capability_checklist_report,
    format_capability_checklist,
)
from ato_operator.cli import main
from ato_operator.migration_contract import EXPECTED_ALEMBIC_HEAD

ROOT = Path(__file__).resolve().parents[2]
ONPREM_EXAMPLE = ROOT / "deployment" / "config" / "runtime-config.onprem.example.json"


def test_capability_topology_reports_active_api_and_portal() -> None:
    from ato_service.runtime_config import load_runtime_config

    config = load_runtime_config(ONPREM_EXAMPLE, base_dir=None)
    topology = build_capability_topology(
        config,
        config_path="/etc/ato-analyzer/runtime-config.json",
    )
    by_name = {item.capability: item for item in topology}
    assert by_name["api"].enabled is True
    assert by_name["portal_static"].enabled is True
    assert by_name["intake_worker"].enabled is False
    assert by_name["api"].systemd_unit == "ato-api.service"
    assert any(
        cred.identifier == "database-dsn"
        for cred in by_name["api"].credentials
    )


def test_capability_checklist_report_is_machine_readable_without_secrets() -> None:
    from ato_service.runtime_config import load_runtime_config

    config = load_runtime_config(ONPREM_EXAMPLE, base_dir=None)
    report = build_capability_checklist_report(
        config,
        project_root=ROOT,
        config_path=str(ONPREM_EXAMPLE),
    )
    assert report["expected_migration_head"] == EXPECTED_ALEMBIC_HEAD
    assert report["migration_head_matches_contract"] is True
    assert report["repository_migration_head"] == EXPECTED_ALEMBIC_HEAD
    assert "postgresql://" not in json.dumps(report)
    assert "password" not in json.dumps(report).lower()

    text = format_capability_checklist(report)
    assert "per-capability onboarding checklist" in text
    assert "database-dsn" in text
    assert "HS-008" in text
    assert "ato-operator validate-config" in text


def test_print_checklist_with_config_emits_json_topology() -> None:
    rc = main(
        [
            "print-checklist",
            "--config",
            str(ONPREM_EXAMPLE),
            "--json",
        ]
    )
    assert rc == 0


def test_verify_migrations_dry_run_requires_expected_head() -> None:
    rc = main(
        [
            "verify-migrations",
            "--config",
            str(ONPREM_EXAMPLE),
            "--dry-run",
            "--json",
        ]
    )
    assert rc == 0
