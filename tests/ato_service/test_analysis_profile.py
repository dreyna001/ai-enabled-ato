"""Unit tests for compiled analysis profile loading."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ato_service.analysis_profile import (
    AnalysisProfileError,
    analysis_profile_sha256,
    bundled_profile_path,
    expected_assessment_item_ids,
    load_fisma_analysis_profile_reference,
    load_pinned_fisma_synthetic_profile,
    load_pinned_profile,
    load_runtime_profile,
    profile_fixture_path,
)
from ato_service.fisma_control_inventory import load_fisma_control_inventory
from ato_service.fisma_profile import compile_fisma_agency_security_profile

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "docs" / "contracts" / "authority-manifest.json"
VALID_INVENTORY_PATH = (
    ROOT
    / "docs"
    / "contracts"
    / "fixtures"
    / "fisma-control-inventory.valid.example.json"
)
GENERATED_AT = datetime(2026, 7, 10, 22, 33, 12, tzinfo=timezone.utc)


def write_digest_pinned_fisma_profile(
    tmp_path: Path,
) -> tuple[Path, str, dict[str, object], str | None]:
    """Compile one FISMA profile, write it to disk, and return path, digest, document, impact."""
    inventory = load_fisma_control_inventory(path=VALID_INVENTORY_PATH)
    profile = compile_fisma_agency_security_profile(
        inventory=inventory,
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
        generated_at=GENERATED_AT,
    )
    profile_bytes = json.dumps(profile).encode("utf-8")
    digest = hashlib.sha256(profile_bytes).hexdigest()
    profile_file = tmp_path / "customer-fisma-profile.json"
    profile_file.write_bytes(profile_bytes)
    return profile_file, digest, profile, inventory.impact_level


def fisma_runtime_config(
    tmp_path: Path,
    *,
    profile_path: Path,
    expected_sha256: str,
    runtime_profile: str = "dev_local",
) -> MagicMock:
    config = MagicMock()
    config.runtime_profile = runtime_profile
    config.storage_data_path = tmp_path / "storage"
    config.document = {
        "FISMA_ANALYSIS_PROFILE_FILE_REFERENCE": {
            "path": str(profile_path),
            "expected_sha256": expected_sha256,
        }
    }
    budget = MagicMock()
    budget.input_budget_tokens = 8192
    budget.max_output_tokens = 1024
    config.resolve_text_model_context_budget.return_value = budget
    return config


def dev_local_runtime_config(tmp_path: Path) -> MagicMock:
    config = MagicMock()
    config.runtime_profile = "dev_local"
    config.storage_data_path = tmp_path / "storage"
    config.document = {}
    return config

BUNDLED_PROFILE_CASES = (
    pytest.param(
        "fedramp_20x_program",
        "C",
        None,
        "fedramp-20x-program-class-c.json",
        id="fedramp-20x-class-c",
    ),
    pytest.param(
        "fedramp_rev5_transition",
        None,
        "low",
        "fedramp-rev5-transition-low.json",
        id="fedramp-rev5-low",
    ),
    pytest.param(
        "fedramp_rev5_transition",
        None,
        "moderate",
        "fedramp-rev5-transition-moderate.json",
        id="fedramp-rev5-moderate",
    ),
    pytest.param(
        "fedramp_rev5_transition",
        None,
        "high",
        "fedramp-rev5-transition-high.json",
        id="fedramp-rev5-high",
    ),
)


@pytest.mark.parametrize(
    ("profile_id", "certification_class", "impact_level", "filename"),
    BUNDLED_PROFILE_CASES,
)
def test_bundled_profile_path_maps_known_candidates(
    profile_id: str,
    certification_class: str | None,
    impact_level: str | None,
    filename: str,
) -> None:
    path = bundled_profile_path(
        profile_id=profile_id,
        certification_class=certification_class,
        impact_level=impact_level,
        project_root=ROOT,
    )
    assert path == (ROOT / "reference" / "profiles" / filename).resolve()


def test_bundled_profile_path_rejects_fisma_without_explicit_path() -> None:
    with pytest.raises(
        AnalysisProfileError,
        match="requires an explicit customer profile path",
    ):
        bundled_profile_path(
            profile_id="fisma_agency_security",
            certification_class=None,
            impact_level="moderate",
            project_root=ROOT,
        )


def test_bundled_profile_path_rejects_fedramp_20x_class_b() -> None:
    with pytest.raises(AnalysisProfileError, match="Class B"):
        bundled_profile_path(
            profile_id="fedramp_20x_program",
            certification_class="B",
            impact_level=None,
            project_root=ROOT,
        )


def test_bundled_profile_path_rejects_invalid_20x_combinations() -> None:
    with pytest.raises(AnalysisProfileError, match="certification_class C"):
        bundled_profile_path(
            profile_id="fedramp_20x_program",
            certification_class=None,
            impact_level=None,
            project_root=ROOT,
        )


def test_bundled_profile_path_rejects_invalid_rev5_combinations() -> None:
    with pytest.raises(AnalysisProfileError, match="impact_level"):
        bundled_profile_path(
            profile_id="fedramp_rev5_transition",
            certification_class=None,
            impact_level=None,
            project_root=ROOT,
        )


def _require_committed_bundled_profiles() -> None:
    candidates = (
        ("fedramp_20x_program", "C", None),
        ("fedramp_rev5_transition", None, "low"),
        ("fedramp_rev5_transition", None, "moderate"),
        ("fedramp_rev5_transition", None, "high"),
    )
    for profile_id, certification_class, impact_level in candidates:
        path = bundled_profile_path(
            profile_id=profile_id,
            certification_class=certification_class,
            impact_level=impact_level,
            project_root=ROOT,
        )
        if not path.is_file():
            pytest.skip("compiled reference profiles are not yet generated")


@pytest.mark.parametrize(
    ("profile_id", "certification_class", "impact_level", "filename"),
    BUNDLED_PROFILE_CASES,
)
def test_load_pinned_profile_loads_committed_bundled_profiles(
    profile_id: str,
    certification_class: str | None,
    impact_level: str | None,
    filename: str,
) -> None:
    _require_committed_bundled_profiles()
    profile = load_pinned_profile(
        profile_id=profile_id,
        project_root=ROOT,
        certification_class=certification_class,
        impact_level=impact_level,
    )
    assert profile["profile_id"] == profile_id
    assert profile["certification_class"] == certification_class
    assert profile["impact_level"] == impact_level
    assert profile["authority_manifest_id"] == "ato-authorities-2026-07-10-draft"
    assert profile["qualification_status"] == "draft"
    assert len(profile["assessment_items"]) > 1


def test_load_pinned_profile_rejects_fisma_without_explicit_path() -> None:
    with pytest.raises(
        AnalysisProfileError,
        match="requires an explicit customer profile path",
    ):
        load_pinned_profile(
            profile_id="fisma_agency_security",
            project_root=ROOT,
            impact_level="moderate",
        )


def test_load_pinned_profile_rejects_fedramp_20x_class_b_without_explicit_path() -> None:
    with pytest.raises(AnalysisProfileError, match="Class B"):
        load_pinned_profile(
            profile_id="fedramp_20x_program",
            project_root=ROOT,
            certification_class="B",
        )


def test_load_pinned_profile_accepts_explicit_fisma_customer_path(tmp_path: Path) -> None:
    profile_file, digest, profile, impact_level = write_digest_pinned_fisma_profile(tmp_path)

    loaded = load_pinned_profile(
        profile_id="fisma_agency_security",
        project_root=ROOT,
        certification_class=None,
        impact_level=impact_level,
        profile_path=profile_file,
        expected_sha256=digest,
    )
    assert loaded["profile_id"] == "fisma_agency_security"
    assert expected_assessment_item_ids(loaded) == ("AC-1", "AC-2", "IA-5")


def test_load_pinned_profile_requires_expected_sha256_for_explicit_path(tmp_path: Path) -> None:
    profile_file, digest, _profile, impact_level = write_digest_pinned_fisma_profile(tmp_path)

    with pytest.raises(
        AnalysisProfileError,
        match="expected_sha256 is required for explicit profile paths",
    ):
        load_pinned_profile(
            profile_id="fisma_agency_security",
            project_root=ROOT,
            certification_class=None,
            impact_level=impact_level,
            profile_path=profile_file,
        )


def test_load_pinned_profile_rejects_digest_mismatch_for_explicit_path(tmp_path: Path) -> None:
    profile_file, _digest, _profile, impact_level = write_digest_pinned_fisma_profile(tmp_path)

    with pytest.raises(AnalysisProfileError, match="digest mismatch"):
        load_pinned_profile(
            profile_id="fisma_agency_security",
            project_root=ROOT,
            certification_class=None,
            impact_level=impact_level,
            profile_path=profile_file,
            expected_sha256="0" * 64,
        )


def test_load_pinned_profile_rejects_draft_when_require_qualified(tmp_path: Path) -> None:
    profile_file, digest, _profile, impact_level = write_digest_pinned_fisma_profile(tmp_path)

    with pytest.raises(
        AnalysisProfileError,
        match="qualification_status must be qualified",
    ):
        load_pinned_profile(
            profile_id="fisma_agency_security",
            project_root=ROOT,
            certification_class=None,
            impact_level=impact_level,
            profile_path=profile_file,
            expected_sha256=digest,
            require_qualified=True,
        )


def test_load_pinned_profile_requires_approved_manifest_for_qualified_profile(
    tmp_path: Path,
) -> None:
    profile_file, _digest, profile, impact_level = write_digest_pinned_fisma_profile(
        tmp_path
    )
    profile["qualification_status"] = "qualified"
    profile_file.write_text(json.dumps(profile), encoding="utf-8")
    digest = hashlib.sha256(profile_file.read_bytes()).hexdigest()

    with pytest.raises(
        AnalysisProfileError,
        match="approved authority manifest",
    ):
        load_pinned_profile(
            profile_id="fisma_agency_security",
            project_root=ROOT,
            certification_class=None,
            impact_level=impact_level,
            profile_path=profile_file,
            expected_sha256=digest,
            require_qualified=True,
        )


def test_load_pinned_profile_rejects_legacy_placeholder_authority_fixture(
    tmp_path: Path,
) -> None:
    fixture_path = profile_fixture_path(
        profile_id="fedramp_20x_program",
        project_root=ROOT,
    )
    with pytest.raises(AnalysisProfileError, match="authority.v2"):
        load_pinned_profile(
            profile_id="fedramp_20x_program",
            project_root=ROOT,
            certification_class="C",
            impact_level=None,
            profile_path=fixture_path,
            expected_sha256=hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        )


def test_load_pinned_profile_rejects_identity_mismatch(tmp_path: Path) -> None:
    profile_file, digest, profile, impact_level = write_digest_pinned_fisma_profile(tmp_path)
    profile["profile_id"] = "fedramp_20x_program"
    profile_file.write_text(json.dumps(profile), encoding="utf-8")
    mismatched_digest = hashlib.sha256(profile_file.read_bytes()).hexdigest()

    with pytest.raises(AnalysisProfileError, match="profile id does not match"):
        load_pinned_profile(
            profile_id="fisma_agency_security",
            project_root=ROOT,
            certification_class=None,
            impact_level=impact_level,
            profile_path=profile_file,
            expected_sha256=mismatched_digest,
        )


def test_load_pinned_profile_rejects_nonfile_explicit_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing-profile.json"
    with pytest.raises(AnalysisProfileError, match="must be a regular file"):
        load_pinned_profile(
            profile_id="fisma_agency_security",
            project_root=ROOT,
            certification_class=None,
            impact_level="moderate",
            profile_path=missing,
            expected_sha256="0" * 64,
        )


@pytest.mark.skipif(os.name == "nt", reason="symlink creation requires elevated privileges")
def test_load_pinned_profile_rejects_symlink_explicit_path(tmp_path: Path) -> None:
    profile_file, digest, _profile, impact_level = write_digest_pinned_fisma_profile(tmp_path)
    link = tmp_path / "linked-profile.json"
    link.symlink_to(profile_file)

    with pytest.raises(AnalysisProfileError, match="must not be a symlink"):
        load_pinned_profile(
            profile_id="fisma_agency_security",
            project_root=ROOT,
            certification_class=None,
            impact_level=impact_level,
            profile_path=link,
            expected_sha256=digest,
        )


def test_load_pinned_profile_semantic_manifest_binding_uses_project_root_authorities(
    tmp_path: Path,
) -> None:
    profile_file, digest, _profile, impact_level = write_digest_pinned_fisma_profile(tmp_path)

    real_manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    truncated_manifest = {
        **real_manifest,
        "sources": [real_manifest["sources"][0]],
    }
    alternate_manifest = tmp_path / "truncated-manifest.json"
    alternate_manifest.write_text(json.dumps(truncated_manifest), encoding="utf-8")

    with pytest.raises(
        AnalysisProfileError,
        match=r"references unknown authority_id 'nist-sp800-53-release-5.2.0'",
    ):
        load_pinned_profile(
            profile_id="fisma_agency_security",
            project_root=ROOT,
            certification_class=None,
            impact_level=impact_level,
            profile_path=profile_file,
            expected_sha256=digest,
            authority_manifest_path=alternate_manifest,
        )


def test_load_runtime_profile_requires_fisma_reference(tmp_path: Path) -> None:
    with pytest.raises(
        AnalysisProfileError,
        match="requires FISMA_ANALYSIS_PROFILE_FILE_REFERENCE",
    ):
        load_runtime_profile(
            profile_id="fisma_agency_security",
            certification_class=None,
            impact_level="moderate",
            project_root=ROOT,
            config=dev_local_runtime_config(tmp_path),
        )


def test_load_runtime_profile_loads_digest_pinned_fisma_profile(tmp_path: Path) -> None:
    profile_file, digest, _profile, impact_level = write_digest_pinned_fisma_profile(tmp_path)
    config = fisma_runtime_config(
        tmp_path,
        profile_path=profile_file,
        expected_sha256=digest,
    )
    loaded = load_runtime_profile(
        profile_id="fisma_agency_security",
        certification_class=None,
        impact_level=impact_level,
        project_root=ROOT,
        config=config,
    )
    assert loaded["profile_id"] == "fisma_agency_security"


def test_load_runtime_profile_rejects_draft_bundled_profiles_in_onprem_production(
    tmp_path: Path,
) -> None:
    _require_committed_bundled_profiles()
    config = MagicMock()
    config.runtime_profile = "onprem_production"
    config.document = {}
    with pytest.raises(
        AnalysisProfileError,
        match="qualification_status must be qualified",
    ):
        load_runtime_profile(
            profile_id="fedramp_20x_program",
            certification_class="C",
            impact_level=None,
            project_root=ROOT,
            config=config,
        )


def test_load_fisma_analysis_profile_reference_parses_runtime_json(tmp_path: Path) -> None:
    profile_file, digest, _profile, _impact_level = write_digest_pinned_fisma_profile(tmp_path)
    reference = load_fisma_analysis_profile_reference(
        {
            "FISMA_ANALYSIS_PROFILE_FILE_REFERENCE": {
                "path": str(profile_file),
                "expected_sha256": digest,
            }
        }
    )
    assert reference is not None
    assert reference.path == profile_file
    assert reference.expected_sha256 == digest


def test_load_pinned_fisma_synthetic_profile_legacy_helper_still_loads_fixture() -> None:
    profile = load_pinned_fisma_synthetic_profile(project_root=ROOT)
    assert profile["profile_id"] == "fisma_agency_security"
    assert expected_assessment_item_ids(profile) == ("AC-1", "AC-2", "IA-5")


def test_profile_fixture_path_legacy_helper_still_resolves_contract_fixture() -> None:
    path = profile_fixture_path(profile_id="fisma_agency_security", project_root=ROOT)
    assert path.name == "analysis-profile.valid.fisma-synthetic.json"
    assert path.is_file()


def test_analysis_profile_sha256_is_stable() -> None:
    profile = load_pinned_fisma_synthetic_profile(project_root=ROOT)
    first = analysis_profile_sha256(profile)
    second = analysis_profile_sha256(profile)
    assert first == second
    assert len(first) == 64
