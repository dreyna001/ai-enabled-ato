"""CLI tests for validation drill commands."""

from __future__ import annotations

import json
from pathlib import Path

from ato_operator.cli import main

ROOT = Path(__file__).resolve().parents[2]
DEV_CONFIG = ROOT / "deployment" / "config" / "runtime-config.dev_local.json"


def test_list_drills_json() -> None:
    assert main(["list-drills", "--json"]) == 0


def test_run_drill_dry_run_model_routing(tmp_path: Path) -> None:
    records_root = tmp_path / "records"
    rc = main(
        [
            "run-drill",
            "model-routing-policy-block",
            "--config",
            str(DEV_CONFIG),
            "--write-record",
            "--records-root",
            str(records_root),
            "--operator-id",
            "operator@example.local",
            "--json",
        ]
    )
    assert rc == 0
    record_dirs = list((records_root / "records").glob("*/*.json"))
    assert record_dirs
    document = json.loads(record_dirs[0].read_text(encoding="utf-8"))
    assert document["drill_id"] == "model-routing-policy-block"
    assert document["execution_mode"] == "dry_run"


def test_validate_drill_record_rejects_tampered_digest(tmp_path: Path) -> None:
    source = ROOT / "docs" / "contracts" / "fixtures" / "validation-drill-record.valid.minimal-dry-run.json"
    tampered = json.loads(source.read_text(encoding="utf-8"))
    tampered["record_digest"] = "0" * 64
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    assert main(["validate-drill-record", str(path), "--json"]) == 1
