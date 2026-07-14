"""Pinned profile catalog loading for sufficiency_matrix assessment items."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ato_service.analysis_profile import (
    AnalysisProfileError,
    assessment_item_type_for_id,
    expected_assessment_item_ids,
    load_pinned_profile,
)

SUPPORTED_PROFILE_IDS: frozenset[str] = frozenset(
    {
        "fisma_agency_security",
        "fedramp_20x_program",
        "fedramp_rev5_transition",
    }
)


def require_supported_profile_id(profile_id: str) -> str:
    if profile_id not in SUPPORTED_PROFILE_IDS:
        raise AnalysisProfileError(f"unsupported analysis profile id: {profile_id}")
    return profile_id


def load_profile_catalog(*, profile_id: str, project_root: Path) -> dict[str, Any]:
    """Load and validate the pinned catalog for one supported profile."""
    require_supported_profile_id(profile_id)
    return load_pinned_profile(profile_id=profile_id, project_root=project_root)


def assessment_items_for_prompt(
    *,
    profile: dict[str, Any],
    assessment_item_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Return bounded assessment-item metadata for model prompts."""
    items_by_id = {
        item["assessment_item_id"]: item
        for item in profile.get("assessment_items", [])
        if isinstance(item, dict) and isinstance(item.get("assessment_item_id"), str)
    }
    entries: list[dict[str, Any]] = []
    for assessment_item_id in assessment_item_ids:
        item = items_by_id.get(assessment_item_id)
        if item is None:
            raise AnalysisProfileError(f"unknown assessment item id: {assessment_item_id}")
        entries.append(
            {
                "assessment_item_id": assessment_item_id,
                "assessment_item_type": assessment_item_type_for_id(
                    profile,
                    assessment_item_id,
                ),
                "title": item.get("title"),
                "requirement_text": item.get("requirement_text"),
                "model_analysis_allowed": bool(item.get("model_analysis_allowed")),
                "required_evidence_kinds": list(item.get("required_evidence_kinds") or []),
            }
        )
    return entries


def expected_ids_for_profile(profile: dict[str, Any]) -> tuple[str, ...]:
    return expected_assessment_item_ids(profile)
