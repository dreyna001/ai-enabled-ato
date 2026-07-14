"""Profile catalog loading tests for sufficiency_matrix."""

from __future__ import annotations

from pathlib import Path

from ato_service.analysis_profile import load_pinned_profile
from ato_service.sufficiency_matrix.profile_catalog import (
    SUPPORTED_PROFILE_IDS,
    assessment_items_for_prompt,
    load_profile_catalog,
)

ROOT = Path(__file__).resolve().parents[2]


def test_supported_profile_catalogs_load_and_validate() -> None:
    assert SUPPORTED_PROFILE_IDS == frozenset(
        {
            "fisma_agency_security",
            "fedramp_20x_program",
            "fedramp_rev5_transition",
        }
    )
    for profile_id in sorted(SUPPORTED_PROFILE_IDS):
        profile = load_profile_catalog(profile_id=profile_id, project_root=ROOT)
        assert profile["profile_id"] == profile_id
        items = assessment_items_for_prompt(
            profile=profile,
            assessment_item_ids=tuple(
                item["assessment_item_id"] for item in profile["assessment_items"]
            ),
        )
        assert items


def test_load_pinned_profile_rejects_unknown_id() -> None:
    try:
        load_pinned_profile(profile_id="unknown_profile", project_root=ROOT)
    except Exception as exc:
        assert "unsupported" in str(exc).casefold()
    else:
        raise AssertionError("expected unsupported profile error")
