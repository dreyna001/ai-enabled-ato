"""Profile catalog loading tests for sufficiency_matrix."""

from __future__ import annotations

from pathlib import Path

import pytest

from ato_service.analysis_profile import AnalysisProfileError, load_pinned_profile
from ato_service.sufficiency_matrix.profile_catalog import (
    SUPPORTED_PROFILE_IDS,
    assessment_items_for_prompt,
    load_profile_catalog,
)
from tests.ato_service.test_analysis_profile import dev_local_runtime_config, fisma_runtime_config, write_digest_pinned_fisma_profile

ROOT = Path(__file__).resolve().parents[2]


def test_supported_profile_catalogs_load_and_validate(tmp_path: Path) -> None:
    assert SUPPORTED_PROFILE_IDS == frozenset(
        {
            "fisma_agency_security",
            "fedramp_20x_program",
            "fedramp_rev5_transition",
        }
    )
    profile_file, digest, _profile, impact_level = write_digest_pinned_fisma_profile(tmp_path)
    fisma_config = fisma_runtime_config(
        tmp_path,
        profile_path=profile_file,
        expected_sha256=digest,
    )
    fedramp_config = dev_local_runtime_config(tmp_path)
    cases = (
        ("fisma_agency_security", None, impact_level, fisma_config),
        ("fedramp_20x_program", "C", None, fedramp_config),
        ("fedramp_rev5_transition", None, "low", fedramp_config),
    )
    for profile_id, certification_class, impact_level, config in cases:
        profile = load_profile_catalog(
            profile_id=profile_id,
            certification_class=certification_class,
            impact_level=impact_level,
            project_root=ROOT,
            config=config,
        )
        assert profile["profile_id"] == profile_id
        items = assessment_items_for_prompt(
            profile=profile,
            assessment_item_ids=tuple(
                item["assessment_item_id"] for item in profile["assessment_items"]
            ),
        )
        assert items


def test_load_profile_catalog_requires_fisma_runtime_reference(tmp_path: Path) -> None:
    with pytest.raises(
        AnalysisProfileError,
        match="requires FISMA_ANALYSIS_PROFILE_FILE_REFERENCE",
    ):
        load_profile_catalog(
            profile_id="fisma_agency_security",
            certification_class=None,
            impact_level="moderate",
            project_root=ROOT,
            config=dev_local_runtime_config(tmp_path),
        )


def test_load_pinned_profile_rejects_unknown_id() -> None:
    try:
        load_pinned_profile(profile_id="unknown_profile", project_root=ROOT)
    except Exception as exc:
        assert "unsupported" in str(exc).casefold()
    else:
        raise AssertionError("expected unsupported profile error")
