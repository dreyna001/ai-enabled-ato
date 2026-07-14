"""Operator CLI tests for AI evaluation record commands."""

from __future__ import annotations

import json
from pathlib import Path

from ato_operator.cli import main

ROOT = Path(__file__).resolve().parents[2]
FAILED_FIXTURE = (
    ROOT / "docs" / "contracts" / "fixtures" / "ai-evaluation-record.valid.failed-gates.json"
)
INVALID_SCHEMA_FIXTURE = (
    ROOT
    / "docs"
    / "contracts"
    / "fixtures"
    / "ai-evaluation-record.invalid.missing-required-fields.json"
)


def test_validate_evaluation_record_accepts_failed_fixture() -> None:
    assert (
        main(
            [
                "validate-evaluation-record",
                "--record",
                str(FAILED_FIXTURE),
                "--json",
            ]
        )
        == 0
    )


def test_validate_evaluation_record_rejects_invalid_fixture() -> None:
    rc = main(
        [
            "validate-evaluation-record",
            "--record",
            str(INVALID_SCHEMA_FIXTURE),
        ]
    )
    assert rc == 1


def test_write_evaluation_record_requires_records_root() -> None:
    rc = main(
        [
            "write-evaluation-record",
            "--record",
            str(FAILED_FIXTURE),
        ]
    )
    assert rc == 2


def test_write_evaluation_record_persists_under_safe_root(tmp_path: Path) -> None:
    records_root = tmp_path / "records"
    rc = main(
        [
            "write-evaluation-record",
            "--record",
            str(FAILED_FIXTURE),
            "--records-root",
            str(records_root),
            "--json",
        ]
    )
    assert rc == 0
    document = json.loads(FAILED_FIXTURE.read_text(encoding="utf-8"))
    stored = records_root / "evaluations" / f"{document['evaluation_id']}.json"
    assert stored.is_file()

    rc = main(
        [
            "validate-evaluation-record",
            "--record",
            str(stored),
        ]
    )
    assert rc == 0
