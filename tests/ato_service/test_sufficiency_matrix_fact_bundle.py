"""Focused tests for sufficiency_matrix fact-bundle context budgeting."""

from __future__ import annotations

import copy
import hashlib
import uuid
from pathlib import Path
from typing import Any

import pytest

from ato_service.analysis_profile import load_pinned_profile
from ato_service.db.models import SealedPackageContent
from ato_service.idempotency import canonical_json_bytes
from ato_service.sufficiency_matrix.constants import MINIMUM_BUNDLE_RESERVE_TOKENS
from ato_service.sufficiency_matrix.fact_bundle import (
    ContextLimitExceededError,
    build_fact_bundle,
)
from ato_service.sufficiency_matrix.profile_catalog import assessment_items_for_prompt
from ato_service.sufficiency_matrix.tokens import estimate_tokens_from_object

ROOT = Path(__file__).resolve().parents[2]
SOURCE_A = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
SOURCE_B = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
STATEMENT_A = "Alpha access control policy covers privileged users."
STATEMENT_B = "Beta logging policy retains audit records for review."


def _source_sha256(pointer: str, text: str) -> str:
    return hashlib.sha256(f"{pointer}:{text}".encode("utf-8")).hexdigest()


def _sealed(*, statements: tuple[tuple[str, str, uuid.UUID], ...]) -> SealedPackageContent:
    controls: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    for control_id, statement, artifact_id in statements:
        pointer = f"/security_controls/{control_id}/implementation_statement"
        controls[control_id] = {"implementation_statement": statement}
        provenance[pointer] = {
            "source_artifact_id": str(artifact_id).lower(),
            "source_sha256": _source_sha256(pointer, statement),
        }
    document = {
        "package": {"profile_id": "fedramp_20x_program", "title": "Demo"},
        "security_controls": controls,
        "evidence": {},
    }
    digest = hashlib.sha256(
        canonical_json_bytes({"document": document, "field_provenance": provenance})
    ).hexdigest()
    return SealedPackageContent(
        package_revision_id=uuid.uuid4(),
        document=document,
        field_provenance=provenance,
        content_sha256=digest,
    )


def test_fact_bundle_includes_all_sources_when_budget_allows() -> None:
    profile = load_pinned_profile(profile_id="fedramp_20x_program", project_root=ROOT)
    sealed = _sealed(statements=(("FR-1", STATEMENT_A, SOURCE_A),))

    bundle = build_fact_bundle(
        profile=profile,
        assessment_item_ids=("FR-1",),
        sealed=sealed,
        input_budget_tokens=8192,
    )

    assert bundle.context_complete is True
    assert bundle.omitted_source_ids == ()
    assert len(bundle.prompt_payload["evidence_sources"]) == 2


def test_fact_bundle_omits_sources_in_rank_order_when_budget_tight() -> None:
    profile = load_pinned_profile(profile_id="fedramp_20x_program", project_root=ROOT)
    sealed = _sealed(
        statements=(
            ("FR-1", STATEMENT_A, SOURCE_A),
            ("FR-2", "x" * 12_000, SOURCE_B),
        )
    )

    bundle = build_fact_bundle(
        profile=profile,
        assessment_item_ids=("FR-1",),
        sealed=sealed,
        input_budget_tokens=500,
    )

    included_ids = [
        entry["source_id"] for entry in bundle.prompt_payload["evidence_sources"]
    ]
    assert str(SOURCE_B).lower() in bundle.omitted_source_ids
    assert included_ids == sorted(included_ids, key=str.lower)
    assert bundle.context_complete is False


def test_minimum_bundle_context_limit_exceeded() -> None:
    profile = load_pinned_profile(profile_id="fedramp_20x_program", project_root=ROOT)
    sealed = _sealed(statements=(("FR-1", STATEMENT_A, SOURCE_A),))

    with pytest.raises(ContextLimitExceededError, match="minimum sufficiency_matrix"):
        build_fact_bundle(
            profile=profile,
            assessment_item_ids=("FR-1",),
            sealed=sealed,
            input_budget_tokens=128,
        )


def test_fixed_metadata_context_limit_exceeded() -> None:
    profile = copy.deepcopy(
        load_pinned_profile(profile_id="fedramp_20x_program", project_root=ROOT)
    )
    for item in profile["assessment_items"]:
        if isinstance(item, dict) and item.get("assessment_item_id") == "FR-1":
            item["requirement_text"] = "x" * 5000
    sealed = _sealed(statements=(("FR-1", STATEMENT_A, SOURCE_A),))
    assessment_item_ids = ("FR-1",)
    assessment_items = assessment_items_for_prompt(
        profile=profile,
        assessment_item_ids=assessment_item_ids,
    )
    fixed_payload = {
        "profile_id": profile["profile_id"],
        "assessment_item_ids": list(assessment_item_ids),
        "assessment_items": list(assessment_items),
        "evidence_sources": [],
        "omitted_source_ids": [],
    }
    fixed_tokens = estimate_tokens_from_object(fixed_payload)
    assert fixed_tokens >= MINIMUM_BUNDLE_RESERVE_TOKENS

    with pytest.raises(ContextLimitExceededError, match="assessment item metadata"):
        build_fact_bundle(
            profile=profile,
            assessment_item_ids=assessment_item_ids,
            sealed=sealed,
            input_budget_tokens=fixed_tokens,
        )
