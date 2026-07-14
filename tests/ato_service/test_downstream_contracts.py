"""Tests for preflight, export readiness, revision delta, search, and chat."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from ato_service.export_readiness import evaluate_export_readiness
from ato_service.package_chat import chat_with_package
from ato_service.package_search import search_revision_content
from ato_service.preflight import PreflightContext, evaluate_preflight
from ato_service.revision_delta import compute_revision_delta

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)


def _minimal_document(*, assessor: bool = False, privacy: bool = False) -> dict:
    return {
        "package": {"profile_id": "fisma_agency_security", "title": "t", "prepared_for": "a", "reporting_period": None},
        "system": {
            "display_name": "Demo",
            "authorization_boundary": "vpc",
            "mission_summary": "demo",
            "impact_level": "moderate",
            "authorization_path": "agency",
        },
        "contacts": {
            "system_owner": [],
            "isso": [],
            "issm": [],
            "control_owners": [],
            "assessors": [],
            "approvers": [],
        },
        "control_set": {"source": {}, "tailoring": [], "organization_defined_parameters": {}, "inheritance": []},
        "security_controls": {
            "AC-1": {
                "implementation_status": "implemented",
                "implementation_statement": "policy",
                "responsible_parties": [],
                "evidence_links": [],
            }
        },
        "evidence": {},
        "findings": {},
        "poam_candidates": {},
        "assessor_inputs": {"sar": {"owner": "assessor"}} if assessor else {},
        "privacy": {
            "artifacts_present": privacy,
            "scope_notice": "Privacy review is external to this product.",
        },
        "fedramp_20x": None,
        "fedramp_rev5_transition": None,
        "fisma_agency_security": {"sections": {}},
        "extensions": {},
    }


def test_preflight_blocks_export_without_assessor_and_privacy() -> None:
    result = evaluate_preflight(
        PreflightContext(
            package_revision_id=uuid.uuid4(),
            profile_id="fisma_agency_security",
            status="ready",
            sealed_document=_minimal_document(),
            authority_manifest_id="ato-authorities-2026-07-10-draft",
            authority_manifest_sha256="a" * 64,
            project_root=ROOT,
            evaluated_at=NOW,
        )
    )
    assert result["analysis_eligible"] is True
    assert result["export_eligible"] is False
    assert "assessor.inputs_present" in result["export_blockers"]
    assert "privacy.artifacts_present" in result["export_blockers"]


def test_preflight_export_eligible_when_requirements_present() -> None:
    result = evaluate_preflight(
        PreflightContext(
            package_revision_id=uuid.uuid4(),
            profile_id="fisma_agency_security",
            status="ready",
            sealed_document=_minimal_document(assessor=True, privacy=True),
            authority_manifest_id="ato-authorities-2026-07-10-draft",
            authority_manifest_sha256="a" * 64,
            project_root=ROOT,
            evaluated_at=NOW,
        )
    )
    assert result["export_eligible"] is True


def test_export_readiness_reports_missing_assessor_inputs_for_fedramp() -> None:
    document = _minimal_document()
    document["package"]["profile_id"] = "fedramp_20x_program"
    document["fedramp_20x"] = {"cpo": {}, "sdr": {}, "ocr": {}, "scg": {}, "ksi_methods": [], "metric_history": [], "independent_assessment": {}}
    result = evaluate_export_readiness(
        profile_id="fedramp_20x_program",
        sealed_document=document,
        project_root=ROOT,
    )
    assert "missing_assessor_inputs" in result.blockers


def test_export_readiness_fisma_security_only_does_not_require_privacy_execution() -> None:
    result = evaluate_export_readiness(
        profile_id="fisma_agency_security",
        sealed_document=_minimal_document(),
        project_root=ROOT,
        runtime_config_document=None,
    )
    assert result.structural_checks_passed is True
    assert "missing_privacy_artifacts" not in result.blockers
    assert "hs002_template_pack_unavailable" in result.warnings


def test_revision_delta_detects_changed_controls() -> None:
    parent_doc = _minimal_document()
    child_doc = _minimal_document()
    child_doc["security_controls"]["AC-1"]["implementation_statement"] = "updated"
    delta = compute_revision_delta(
        parent_revision_id=uuid.uuid4(),
        child_revision_id=uuid.uuid4(),
        parent_artifacts=[],
        child_artifacts=[],
        parent_document=parent_doc,
        child_document=child_doc,
        parent_content_sha256="a" * 64,
        child_content_sha256="b" * 64,
        now=NOW,
    )
    assert "AC-1" in delta.changed_control_ids
    assert delta.content_digest_changed is True


def test_search_is_revision_scoped() -> None:
    document = _minimal_document()
    hits = search_revision_content(query="policy", sealed_document=document, artifacts=[])
    assert hits["items"]


def test_chat_refuses_authorization_decision_request() -> None:
    response = chat_with_package(
        question="Please grant ATO for this package",
        sealed_document=_minimal_document(),
        search_hits=[],
    )
    assert response["refused"] is True
    assert response["refusal_code"] == "authorization_decision"
