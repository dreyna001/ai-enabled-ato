"""Tests for the deterministic draft analysis profile compiler."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ato_service.analysis_profile import analysis_profile_sha256
from ato_service.analysis_profile_compiler import (
    AnalysisProfileCompileError,
    ProfileIdentity,
    compile_draft_analysis_profile,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "docs" / "contracts" / "authority-manifest.json"
MANIFEST_ID = "ato-authorities-2026-07-10-draft"
FEDRAMP_AUTHORITY_ID = "fedramp-consolidated-rules-2026"
FEDRAMP_RULE_POINTER = "/FRR/AFC/data/all/CSO/AFC-CSO-INB"
CPO_RULE_POINTER = "/FRR/CPO/data/all/CSO/CPO-CSO-OVR"
CPO_SCHEMA_AUTHORITY_ID = "fedramp-schema-cpo-2026-06-24"
GENERATED_AT = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)


def _fedramp_class_c_identity() -> ProfileIdentity:
    return ProfileIdentity(
        profile_id="fedramp_20x_program",
        profile_version="2.0.0",
        certification_class="C",
        impact_level=None,
    )


def _status_policy() -> dict[str, object]:
    return {
        "allowed_statuses": [
            "supported",
            "partial",
            "unsupported",
            "insufficient_evidence",
        ],
        "no_evidence_status": "insufficient_evidence",
        "incomplete_context_ceiling": "partial",
        "all_stale_ceiling": "partial",
        "system_may_be_more_favorable_than_model": False,
        "exact_row_coverage_required": True,
        "repair_attempts": 1,
    }


def _assessment_item(
    *,
    assessment_item_id: str = "AFC-CSO-INB",
    source_pointer: str = FEDRAMP_RULE_POINTER,
) -> dict[str, object]:
    return {
        "assessment_item_type": "fedramp_rule",
        "assessment_item_id": assessment_item_id,
        "title": "Maintain a FedRAMP Security Inbox",
        "requirement_text": (
            "Providers MUST establish and maintain an email address to receive "
            "messages from FedRAMP; this inbox is a FedRAMP Security Inbox (FSI)."
        ),
        "force": "MUST",
        "owner": "provider",
        "applicability": {
            "paths": ["program"],
            "classes": ["C"],
            "impact_levels": [],
            "affects": ["provider"],
        },
        "authority_refs": [
            {
                "authority_id": FEDRAMP_AUTHORITY_ID,
                "source_pointer": source_pointer,
            }
        ],
        "required_evidence_kinds": ["evidence_document"],
        "model_analysis_allowed": True,
    }


def _artifact_requirement(
    *,
    artifact_id: str = "cpo",
    source_pointer: str = CPO_RULE_POINTER,
    official_schema_authority_id: str | None = CPO_SCHEMA_AUTHORITY_ID,
) -> dict[str, object]:
    return {
        "artifact_id": artifact_id,
        "display_name": "Certification Package Overview",
        "required": True,
        "owner": "provider",
        "official_schema_authority_id": official_schema_authority_id,
        "human_readable_required": True,
        "machine_readable_required": True,
        "export_paths": ["human/cpo.md", "machine/cpo.json"],
        "authority_refs": [
            {
                "authority_id": FEDRAMP_AUTHORITY_ID,
                "source_pointer": source_pointer,
            }
        ],
    }


def _compile_inputs(
    *,
    assessment_items: list[dict[str, object]] | None = None,
    artifact_requirements: list[dict[str, object]] | None = None,
    cadence_rules: list[dict[str, object]] | None = None,
    status_policy: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "identity": _fedramp_class_c_identity(),
        "generated_at": GENERATED_AT,
        "assessment_items": assessment_items
        if assessment_items is not None
        else [_assessment_item()],
        "artifact_requirements": artifact_requirements
        if artifact_requirements is not None
        else [_artifact_requirement()],
        "cadence_rules": cadence_rules if cadence_rules is not None else [],
        "status_policy": status_policy if status_policy is not None else _status_policy(),
        "manifest_path": MANIFEST_PATH,
        "project_root": ROOT,
    }


def _compile(**overrides: object) -> dict[str, object]:
    inputs = _compile_inputs()
    inputs.update(overrides)
    return compile_draft_analysis_profile(**inputs)


def test_compile_draft_analysis_profile_success() -> None:
    profile = _compile()

    assert profile["schema_version"] == "2.0.0"
    assert profile["profile_id"] == "fedramp_20x_program"
    assert profile["profile_version"] == "2.0.0"
    assert profile["authority_manifest_id"] == MANIFEST_ID
    assert profile["generated_at"] == "2026-07-14T16:00:00Z"
    assert profile["qualification_status"] == "draft"
    assert profile["certification_class"] == "C"
    assert profile["impact_level"] is None
    assert profile["assessment_items"][0]["assessment_item_id"] == "AFC-CSO-INB"
    assert profile["artifact_requirements"][0]["artifact_id"] == "cpo"
    assert (
        profile["artifact_requirements"][0]["official_schema_authority_id"]
        == CPO_SCHEMA_AUTHORITY_ID
    )
    assert profile["cadence_rules"] == []


def test_compile_draft_analysis_profile_always_emits_draft_status() -> None:
    profile = _compile()
    assert profile["qualification_status"] == "draft"


def test_compile_draft_analysis_profile_binds_verified_manifest_id() -> None:
    profile = _compile()
    assert profile["authority_manifest_id"] == MANIFEST_ID


def test_compile_draft_analysis_profile_sorts_arrays_deterministically() -> None:
    profile = _compile(
        assessment_items=[
            _assessment_item(assessment_item_id="Z-RULE"),
            _assessment_item(assessment_item_id="A-RULE"),
        ],
        artifact_requirements=[
            _artifact_requirement(artifact_id="z-artifact"),
            _artifact_requirement(artifact_id="a-artifact"),
        ],
        cadence_rules=[
            {
                "cadence_rule_id": "z.rule",
                "description": "Later rule",
                "date_field": "/evidence/updated_at",
                "comparison": "age_at_most",
                "duration_days": 30,
                "severity": "warning",
                "authority_refs": [
                    {
                        "authority_id": FEDRAMP_AUTHORITY_ID,
                        "source_pointer": FEDRAMP_RULE_POINTER,
                    }
                ],
            },
            {
                "cadence_rule_id": "a.rule",
                "description": "Earlier rule",
                "date_field": "/evidence/updated_at",
                "comparison": "age_at_most",
                "duration_days": 30,
                "severity": "warning",
                "authority_refs": [
                    {
                        "authority_id": FEDRAMP_AUTHORITY_ID,
                        "source_pointer": FEDRAMP_RULE_POINTER,
                    }
                ],
            },
        ],
    )

    assert [item["assessment_item_id"] for item in profile["assessment_items"]] == [
        "A-RULE",
        "Z-RULE",
    ]
    assert [
        artifact["artifact_id"] for artifact in profile["artifact_requirements"]
    ] == ["a-artifact", "z-artifact"]
    assert [rule["cadence_rule_id"] for rule in profile["cadence_rules"]] == [
        "a.rule",
        "z.rule",
    ]


def test_compile_draft_analysis_profile_digest_is_stable_for_sorted_output() -> None:
    first = _compile(
        assessment_items=[
            _assessment_item(assessment_item_id="Z-RULE"),
            _assessment_item(assessment_item_id="A-RULE"),
        ],
        artifact_requirements=[
            _artifact_requirement(artifact_id="z-artifact"),
            _artifact_requirement(artifact_id="a-artifact"),
        ],
    )
    second = _compile(
        assessment_items=[
            _assessment_item(assessment_item_id="A-RULE"),
            _assessment_item(assessment_item_id="Z-RULE"),
        ],
        artifact_requirements=[
            _artifact_requirement(artifact_id="a-artifact"),
            _artifact_requirement(artifact_id="z-artifact"),
        ],
    )

    assert first == second
    assert analysis_profile_sha256(first) == analysis_profile_sha256(second)


def test_compile_draft_analysis_profile_deep_copies_inputs() -> None:
    assessment_items = [_assessment_item()]
    artifact_requirements = [_artifact_requirement()]
    cadence_rules: list[dict[str, object]] = []
    status_policy = _status_policy()
    assessment_snapshot = copy.deepcopy(assessment_items)
    artifact_snapshot = copy.deepcopy(artifact_requirements)
    status_snapshot = copy.deepcopy(status_policy)

    profile = compile_draft_analysis_profile(
        identity=_fedramp_class_c_identity(),
        generated_at=GENERATED_AT,
        assessment_items=assessment_items,
        artifact_requirements=artifact_requirements,
        cadence_rules=cadence_rules,
        status_policy=status_policy,
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
    )

    assert assessment_items == assessment_snapshot
    assert artifact_requirements == artifact_snapshot
    assert status_policy == status_snapshot

    assessment_items[0]["title"] = "mutated title"
    artifact_requirements[0]["display_name"] = "mutated artifact"
    status_policy["repair_attempts"] = 99

    assert profile["assessment_items"][0]["title"] == "Maintain a FedRAMP Security Inbox"
    assert profile["artifact_requirements"][0]["display_name"] == (
        "Certification Package Overview"
    )
    assert profile["status_policy"]["repair_attempts"] == 1


def test_compile_draft_analysis_profile_rejects_naive_generated_at() -> None:
    with pytest.raises(
        AnalysisProfileCompileError,
        match="generated_at must be timezone-aware",
    ):
        _compile(generated_at=datetime(2026, 7, 14, 16, 0))


def test_compile_draft_analysis_profile_rejects_malformed_sortable_entries() -> None:
    with pytest.raises(
        AnalysisProfileCompileError,
        match="assessment_items entry at index 0 must declare assessment_item_id",
    ):
        _compile(assessment_items=[{"title": "missing id"}])

    with pytest.raises(
        AnalysisProfileCompileError,
        match="artifact_requirements entry at index 0 must declare artifact_id",
    ):
        _compile(artifact_requirements=[{"display_name": "missing id"}])

    with pytest.raises(
        AnalysisProfileCompileError,
        match="cadence_rules entry at index 0 must declare cadence_rule_id",
    ):
        _compile(cadence_rules=[{"description": "missing id"}])


def test_compile_draft_analysis_profile_rejects_schema_invalid_profile() -> None:
    with pytest.raises(
        AnalysisProfileCompileError,
        match="analysis profile failed schema validation at assessment_items: ",
    ):
        _compile(assessment_items=[])


def test_compile_draft_analysis_profile_rejects_unresolved_authority_pointer() -> None:
    with pytest.raises(
        AnalysisProfileCompileError,
        match=r"authority_ref 'fedramp-consolidated-rules-2026' '/requirements/missing'",
    ):
        _compile(
            assessment_items=[
                _assessment_item(source_pointer="/requirements/missing"),
            ]
        )


def test_compile_draft_analysis_profile_rejects_tampered_authority_bytes(
    tmp_path: Path,
) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    artifact_dir = tmp_path / "reference" / "authorities" / "fedramp"
    artifact_dir.mkdir(parents=True)
    artifact_path = artifact_dir / "fedramp-consolidated-rules-2026.json"
    source_path = ROOT / manifest["sources"][0]["local_path"]
    artifact_path.write_bytes(source_path.read_bytes())
    artifact_path.write_bytes(b"x" * artifact_path.stat().st_size)

    manifest_path = tmp_path / "authority-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        AnalysisProfileCompileError,
        match="sha256 does not match local artifact",
    ):
        _compile(manifest_path=manifest_path, project_root=tmp_path)


def test_compile_draft_analysis_profile_rejects_missing_authority_file(
    tmp_path: Path,
) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest_path = tmp_path / "authority-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        AnalysisProfileCompileError,
        match="missing authority file for fedramp-consolidated-rules-2026",
    ):
        _compile(manifest_path=manifest_path, project_root=tmp_path)


def test_compile_draft_analysis_profile_normalizes_generated_at_to_utc_z() -> None:
    profile = _compile(
        generated_at=datetime(
            2026,
            7,
            14,
            12,
            0,
            tzinfo=timezone(timedelta(hours=-4)),
        )
    )
    assert profile["generated_at"] == "2026-07-14T16:00:00Z"
