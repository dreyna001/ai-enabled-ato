"""Unit tests for pinned FISMA synthetic analysis profile loading."""

from __future__ import annotations

from pathlib import Path

from ato_service.analysis_profile import (
    analysis_profile_sha256,
    expected_assessment_item_ids,
    load_pinned_fisma_synthetic_profile,
)

ROOT = Path(__file__).resolve().parents[2]


def test_pinned_fisma_synthetic_profile_has_three_controls() -> None:
    profile = load_pinned_fisma_synthetic_profile(project_root=ROOT)
    assert profile["profile_id"] == "fisma_agency_security"
    assert expected_assessment_item_ids(profile) == ("AC-1", "AC-2", "IA-5")


def test_analysis_profile_sha256_is_stable() -> None:
    profile = load_pinned_fisma_synthetic_profile(project_root=ROOT)
    first = analysis_profile_sha256(profile)
    second = analysis_profile_sha256(profile)
    assert first == second
    assert len(first) == 64
