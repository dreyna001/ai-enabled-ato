"""Behavior-focused tests for qualification corpus validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ato_operator.cli import main
from ato_operator.qualification_check import run_qualification_check

ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOT = ROOT / "data" / "qualification"
MANIFEST_PATH = CORPUS_ROOT / "manifest.json"
SCHEMA_PATH = ROOT / "docs" / "contracts" / "qualification-manifest.schema.json"


def test_happy_path_validates_repository_corpus() -> None:
    report = run_qualification_check(project_root=ROOT)
    assert report.passed, report.errors
    assert report.fixture_count >= 20
    assert set(report.profiles_covered) == {
        "fedramp_20x_program",
        "fedramp_rev5_transition",
        "fisma_agency_security",
    }
    assert report.hostile_fixture_count >= 3
    assert report.replay_fixture_count >= 1
    assert "HS-001" in report.hard_stops_governed
    assert "HS-006" in report.hard_stops_governed


def test_qualification_fixtures_use_lf_bytes_only() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    corpus_root = (ROOT / manifest["corpus_root"]).resolve()
    for fixture in manifest["fixtures"]:
        fixture_path = (corpus_root / fixture["relative_path"]).resolve()
        content = fixture_path.read_bytes()
        assert b"\r\n" not in content, (
            f"{fixture['fixture_id']} must remain LF-normalized for digest verification"
        )


def test_cli_qualification_check_passes() -> None:
    assert main(["qualification-check"]) == 0


def test_malformed_manifest_fails_schema_validation(tmp_path: Path) -> None:
    bad_manifest = tmp_path / "manifest.json"
    bad_manifest.write_text('{"schema_version":"9.9.9"}', encoding="utf-8")
    report = run_qualification_check(project_root=ROOT, manifest_path=bad_manifest)
    assert not report.passed
    assert any("schema validation" in error for error in report.errors)


def test_path_traversal_relative_path_rejected(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["fixtures"][0]["relative_path"] = "../outside.json"
    bad_manifest = tmp_path / "manifest.json"
    bad_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    report = run_qualification_check(project_root=ROOT, manifest_path=bad_manifest)
    assert not report.passed
    assert any(
        "path traversal" in error
        or "escapes corpus_root" in error
        or "schema validation" in error
        for error in report.errors
    )


def test_missing_fixture_file_reported(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["fixtures"][0]["relative_path"] = "missing/fixture.json"
    bad_manifest = tmp_path / "manifest.json"
    bad_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    report = run_qualification_check(project_root=ROOT, manifest_path=bad_manifest)
    assert not report.passed
    assert any("missing fixture file" in error for error in report.errors)


def test_duplicate_relative_path_rejected(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["fixtures"][1]["relative_path"] = manifest["fixtures"][0]["relative_path"]
    bad_manifest = tmp_path / "manifest.json"
    bad_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    report = run_qualification_check(project_root=ROOT, manifest_path=bad_manifest)
    assert not report.passed
    assert any("duplicate relative_path" in error for error in report.errors)


def test_digest_mismatch_reported(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["fixtures"][0]["sha256"] = "0" * 64
    bad_manifest = tmp_path / "manifest.json"
    bad_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    report = run_qualification_check(project_root=ROOT, manifest_path=bad_manifest)
    assert not report.passed
    assert any("sha256 mismatch" in error for error in report.errors)


def test_empty_corpus_rejected(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["fixtures"] = []
    bad_manifest = tmp_path / "manifest.json"
    bad_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    report = run_qualification_check(project_root=ROOT, manifest_path=bad_manifest)
    assert not report.passed
    assert any(
        "minItems" in error or "no fixtures" in error or "should be non-empty" in error
        for error in report.errors
    )


def test_profile_completeness_requires_all_three_profiles(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["fixtures"] = [
        fixture
        for fixture in manifest["fixtures"]
        if fixture.get("profile_id") != "fisma_agency_security"
    ]
    bad_manifest = tmp_path / "manifest.json"
    bad_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    report = run_qualification_check(project_root=ROOT, manifest_path=bad_manifest)
    assert not report.passed
    assert any(
        "missing qualification fixture for profile fisma_agency_security" in error
        for error in report.errors
    )


def test_hostile_coverage_requires_injection_xxe_and_parse_reject(
    tmp_path: Path,
) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["fixtures"] = [
        fixture for fixture in manifest["fixtures"] if fixture.get("split") != "hostile"
    ]
    bad_manifest = tmp_path / "manifest.json"
    bad_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    report = run_qualification_check(project_root=ROOT, manifest_path=bad_manifest)
    assert not report.passed
    assert any("missing hostile fixture" in error for error in report.errors)


def test_scenario_replay_coverage_required(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["fixtures"] = [
        fixture
        for fixture in manifest["fixtures"]
        if fixture.get("split") != "scenario"
    ]
    bad_manifest = tmp_path / "manifest.json"
    bad_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    report = run_qualification_check(project_root=ROOT, manifest_path=bad_manifest)
    assert not report.passed
    assert any("missing scenario fixture" in error for error in report.errors)


@pytest.mark.parametrize(
    "fixture_path",
    [
        ROOT / "docs/contracts/fixtures/qualification-manifest.valid.minimal.json",
        MANIFEST_PATH,
    ],
)
def test_valid_manifest_fixtures_validate_against_schema(fixture_path: Path) -> None:
    from jsonschema import Draft202012Validator, FormatChecker

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(fixture_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
