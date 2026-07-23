"""Tests for the real FedRAMP Rev. 5 transition profile compiler."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ato_service.analysis_profile import analysis_profile_sha256
from ato_service.analysis_profile_validation import validate_analysis_profile_semantics
from ato_service.authority_catalog import (
    load_json_authority_archive_member,
    load_json_authority_source,
    resolve_json_pointer,
)
from ato_service.authority_manifest import verify_authority_manifest
from ato_service.rev5_profile import (
    BASELINE_MEMBER_SUFFIX_BY_IMPACT,
    CATALOG_MEMBER_SUFFIX,
    EXPECTED_BASELINE_CONTROL_COUNTS,
    Rev5ProfileError,
    compile_fedramp_rev5_transition_profile,
    extract_fedramp_rev5_transition_assessment_items,
    fedramp_rev5_transition_artifact_requirements,
    normalize_oscal_control_id,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "docs" / "contracts" / "authority-manifest.json"
MANIFEST_ID = "ato-authorities-2026-07-10-draft"
NIST_AUTHORITY_ID = "nist-sp800-53-release-5.2.0"
FEDRAMP_AUTHORITY_ID = "fedramp-consolidated-rules-2026"
GENERATED_AT = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)

STABLE_MODERATE_PROFILE_DIGEST = (
    "41db43b130cf9eede5128ab718003eadd3809a09895e786dd6a3c01802698e98"
)

AC_2_1_REQUIREMENT_TEXT = (
    "Support the management of system accounts using "
    "{{ insert: param, ac-02.01_odp }}."
)

ARTIFACT_EXPECTATIONS: dict[str, dict[str, object]] = {
    "ssp": {
        "display_name": "Imported System Security Plan",
        "owner": "provider",
        "required": True,
        "human_readable_required": True,
        "machine_readable_required": True,
        "export_paths": ["machine/ssp.json", "human/ssp.md"],
        "source_pointers": [
            "/FRR/SDR/data/rev5/CSF/SDR-CSF-CTF",
            "/FRR/FRC/data/rev5/CSF/FRC-CSF-ACP",
        ],
    },
    "sap": {
        "display_name": "Imported Security Assessment Plan",
        "owner": "provider",
        "required": True,
        "human_readable_required": True,
        "machine_readable_required": True,
        "export_paths": ["machine/sap.json", "human/sap.md"],
        "source_pointers": ["/FRR/IVV/data/rev5/CSF/IVV-CSF-MCA"],
    },
    "sar": {
        "display_name": "Imported Security Assessment Results",
        "owner": "assessor",
        "required": True,
        "human_readable_required": True,
        "machine_readable_required": True,
        "export_paths": ["machine/sar.json", "human/sar.md"],
        "source_pointers": [
            "/FRR/IVV/data/all/IAS/IVV-IAS-SUM",
            "/FRR/IVV/data/all/IAS/IVV-IAS-OSA",
        ],
    },
    "poam": {
        "display_name": "Imported Plan of Action and Milestones",
        "owner": "provider",
        "required": True,
        "human_readable_required": True,
        "machine_readable_required": True,
        "export_paths": ["machine/poam.json", "human/poam.md"],
        "source_pointers": ["/FRR/IVV/data/rev5/CSF/IVV-CSF-ACF"],
    },
    "oscal": {
        "display_name": "Imported OSCAL Package",
        "owner": "provider",
        "required": False,
        "human_readable_required": True,
        "machine_readable_required": True,
        "export_paths": ["machine/oscal.json", "human/oscal.md"],
        "source_pointers": ["/FRR/FRC/data/rev5/CSF/FRC-CSF-FFG"],
    },
}


@pytest.fixture
def authority_manifest() -> dict[str, object]:
    return verify_authority_manifest(MANIFEST_PATH, project_root=ROOT)


@pytest.fixture
def fedramp_document(authority_manifest: dict[str, object]) -> dict[str, object]:
    return load_json_authority_source(
        manifest=authority_manifest,
        authority_id=FEDRAMP_AUTHORITY_ID,
        project_root=ROOT,
    )


@pytest.fixture
def nist_catalog_bundle(
    authority_manifest: dict[str, object],
) -> tuple[str, dict[str, object]]:
    member_name, document = load_json_authority_archive_member(
        manifest=authority_manifest,
        authority_id=NIST_AUTHORITY_ID,
        project_root=ROOT,
        member_suffix=CATALOG_MEMBER_SUFFIX,
    )
    return member_name, document


@pytest.fixture
def nist_moderate_baseline_bundle(
    authority_manifest: dict[str, object],
) -> tuple[str, dict[str, object]]:
    member_name, document = load_json_authority_archive_member(
        manifest=authority_manifest,
        authority_id=NIST_AUTHORITY_ID,
        project_root=ROOT,
        member_suffix=BASELINE_MEMBER_SUFFIX_BY_IMPACT["moderate"],
    )
    return member_name, document


def _item_by_id(items: list[dict[str, object]], item_id: str) -> dict[str, object]:
    return next(item for item in items if item["assessment_item_id"] == item_id)


def _resolve_nist_archive_ref(
    *,
    authority_manifest: dict[str, object],
    ref: dict[str, object],
) -> object:
    archive_member = ref["archive_member"]
    assert isinstance(archive_member, str)
    _member_name, document = load_json_authority_archive_member(
        manifest=authority_manifest,
        authority_id=NIST_AUTHORITY_ID,
        project_root=ROOT,
        member_suffix=archive_member,
    )
    return resolve_json_pointer(document, ref["source_pointer"])


def _resolve_profile_authority_refs(
    profile: dict[str, object],
    *,
    authority_manifest: dict[str, object],
    fedramp_document: dict[str, object],
) -> None:
    for section_name in ("assessment_items", "artifact_requirements", "cadence_rules"):
        for entry in profile.get(section_name, []):
            for ref in entry.get("authority_refs", []):
                authority_id = ref["authority_id"]
                if authority_id == NIST_AUTHORITY_ID:
                    _resolve_nist_archive_ref(
                        authority_manifest=authority_manifest,
                        ref=ref,
                    )
                elif authority_id == FEDRAMP_AUTHORITY_ID:
                    resolve_json_pointer(fedramp_document, ref["source_pointer"])
                else:
                    raise AssertionError(f"unexpected authority_id {authority_id!r}")


def test_normalize_oscal_control_id_uppercases_only() -> None:
    assert normalize_oscal_control_id("ac-2.1") == "AC-2.1"
    assert normalize_oscal_control_id(" CM-2 ") == "CM-2"


@pytest.mark.parametrize(
    ("impact_level", "expected_count"),
    sorted(EXPECTED_BASELINE_CONTROL_COUNTS.items()),
)
def test_extract_yields_expected_unique_control_counts(
    impact_level: str,
    expected_count: int,
    authority_manifest: dict[str, object],
) -> None:
    _, catalog_document = load_json_authority_archive_member(
        manifest=authority_manifest,
        authority_id=NIST_AUTHORITY_ID,
        project_root=ROOT,
        member_suffix=CATALOG_MEMBER_SUFFIX,
    )
    _, baseline_document = load_json_authority_archive_member(
        manifest=authority_manifest,
        authority_id=NIST_AUTHORITY_ID,
        project_root=ROOT,
        member_suffix=BASELINE_MEMBER_SUFFIX_BY_IMPACT[impact_level],
    )

    items = extract_fedramp_rev5_transition_assessment_items(
        impact_level=impact_level,
        catalog_document=catalog_document,
        baseline_document=baseline_document,
    )

    item_ids = [item["assessment_item_id"] for item in items]
    assert len(items) == expected_count
    assert len(set(item_ids)) == expected_count


def test_extract_representative_control_and_enhancement_fidelity(
    nist_catalog_bundle: tuple[str, dict[str, object]],
    nist_moderate_baseline_bundle: tuple[str, dict[str, object]],
) -> None:
    _, catalog_document = nist_catalog_bundle
    _, baseline_document = nist_moderate_baseline_bundle
    items = extract_fedramp_rev5_transition_assessment_items(
        impact_level="moderate",
        catalog_document=catalog_document,
        baseline_document=baseline_document,
    )

    ac2 = _item_by_id(items, "AC-2")
    assert ac2["title"] == "Account Management"
    assert ac2["requirement_text"].startswith(
        "a. Define and document the types of accounts allowed"
    )
    assert "b. Assign account managers;" in ac2["requirement_text"]

    ac21 = _item_by_id(items, "AC-2.1")
    assert ac21["title"] == "Automated System Account Management"
    assert ac21["requirement_text"] == AC_2_1_REQUIREMENT_TEXT

    cm2 = _item_by_id(items, "CM-2")
    assert cm2["title"] == "Baseline Configuration"
    assert "a. Develop, document, and maintain under configuration control" in cm2[
        "requirement_text"
    ]


def test_extract_assessment_items_include_catalog_and_baseline_authority_refs(
    nist_catalog_bundle: tuple[str, dict[str, object]],
    nist_moderate_baseline_bundle: tuple[str, dict[str, object]],
    authority_manifest: dict[str, object],
) -> None:
    _, catalog_document = nist_catalog_bundle
    _, baseline_document = nist_moderate_baseline_bundle
    items = extract_fedramp_rev5_transition_assessment_items(
        impact_level="moderate",
        catalog_document=catalog_document,
        baseline_document=baseline_document,
    )

    ac21 = _item_by_id(items, "AC-2.1")
    assert ac21["authority_refs"] == [
        {
            "authority_id": NIST_AUTHORITY_ID,
            "archive_member": CATALOG_MEMBER_SUFFIX,
            "source_pointer": "/catalog/groups/0/controls/1/controls/0",
        },
        {
            "authority_id": NIST_AUTHORITY_ID,
            "archive_member": BASELINE_MEMBER_SUFFIX_BY_IMPACT["moderate"],
            "source_pointer": "/profile/imports/0/include-controls/0/with-ids/2",
        },
    ]

    for item in items:
        assert item["force"] == "MUST"
        assert item["owner"] == "provider"
        assert item["required_evidence_kinds"] == []
        assert item["model_analysis_allowed"] is True
        assert item["applicability"] == {
            "paths": ["rev5_transition"],
            "classes": [],
            "impact_levels": ["moderate"],
            "affects": ["provider"],
        }
        for ref in item["authority_refs"]:
            _resolve_nist_archive_ref(
                authority_manifest=authority_manifest,
                ref=ref,
            )


def test_fedramp_rev5_transition_artifact_requirements_declares_five_artifacts() -> None:
    artifacts = fedramp_rev5_transition_artifact_requirements()

    assert [artifact["artifact_id"] for artifact in artifacts] == [
        "ssp",
        "sap",
        "sar",
        "poam",
        "oscal",
    ]


@pytest.mark.parametrize("artifact_id", tuple(ARTIFACT_EXPECTATIONS))
def test_fedramp_rev5_transition_artifact_requirements_match_contract(
    artifact_id: str,
    fedramp_document: dict[str, object],
) -> None:
    expected = ARTIFACT_EXPECTATIONS[artifact_id]
    artifact = next(
        item
        for item in fedramp_rev5_transition_artifact_requirements()
        if item["artifact_id"] == artifact_id
    )

    assert artifact["display_name"] == expected["display_name"]
    assert artifact["required"] == expected["required"]
    assert artifact["owner"] == expected["owner"]
    assert artifact["official_schema_authority_id"] is None
    assert artifact["human_readable_required"] == expected["human_readable_required"]
    assert artifact["machine_readable_required"] == expected["machine_readable_required"]
    assert artifact["export_paths"] == expected["export_paths"]

    pointers = [ref["source_pointer"] for ref in artifact["authority_refs"]]
    assert pointers == expected["source_pointers"]
    for ref in artifact["authority_refs"]:
        assert ref["authority_id"] == FEDRAMP_AUTHORITY_ID
        resolve_json_pointer(fedramp_document, ref["source_pointer"])


def test_sar_artifact_requirement_is_assessor_owned() -> None:
    sar = next(
        item
        for item in fedramp_rev5_transition_artifact_requirements()
        if item["artifact_id"] == "sar"
    )
    assert sar["owner"] == "assessor"


def test_compile_fedramp_rev5_transition_profile_is_schema_and_semantically_valid(
    authority_manifest: dict[str, object],
    fedramp_document: dict[str, object],
) -> None:
    profile = compile_fedramp_rev5_transition_profile(
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
        generated_at=GENERATED_AT,
        impact_level="moderate",
        profile_version="1.0.0",
    )

    assert profile["schema_version"] == "2.0.0"
    assert profile["profile_id"] == "fedramp_rev5_transition"
    assert profile["profile_version"] == "1.0.0"
    assert profile["authority_manifest_id"] == MANIFEST_ID
    assert profile["generated_at"] == "2026-07-14T16:00:00Z"
    assert profile["qualification_status"] == "draft"
    assert profile["certification_class"] is None
    assert profile["impact_level"] == "moderate"
    assert profile["cadence_rules"] == []
    assert len(profile["assessment_items"]) == EXPECTED_BASELINE_CONTROL_COUNTS["moderate"]
    compiled_ids = [
        item["assessment_item_id"] for item in profile["assessment_items"]
    ]
    assert compiled_ids == sorted(compiled_ids)
    assert profile["status_policy"]["exact_row_coverage_required"] is True
    assert profile["status_policy"]["system_may_be_more_favorable_than_model"] is False

    validate_analysis_profile_semantics(
        profile,
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
    )
    _resolve_profile_authority_refs(
        profile,
        authority_manifest=authority_manifest,
        fedramp_document=fedramp_document,
    )


def test_compile_fedramp_rev5_transition_profile_moderate_digest_is_stable() -> None:
    profile = compile_fedramp_rev5_transition_profile(
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
        generated_at=GENERATED_AT,
        impact_level="moderate",
        profile_version="1.0.0",
    )

    assert analysis_profile_sha256(profile) == STABLE_MODERATE_PROFILE_DIGEST

    second = compile_fedramp_rev5_transition_profile(
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
        generated_at=GENERATED_AT,
        impact_level="moderate",
        profile_version="1.0.0",
    )
    assert analysis_profile_sha256(second) == STABLE_MODERATE_PROFILE_DIGEST


def test_compile_fedramp_rev5_transition_profile_enforces_impact_boundary() -> None:
    low_profile = compile_fedramp_rev5_transition_profile(
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
        generated_at=GENERATED_AT,
        impact_level="low",
    )
    moderate_profile = compile_fedramp_rev5_transition_profile(
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
        generated_at=GENERATED_AT,
        impact_level="moderate",
    )
    high_profile = compile_fedramp_rev5_transition_profile(
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
        generated_at=GENERATED_AT,
        impact_level="high",
    )

    low_ids = {item["assessment_item_id"] for item in low_profile["assessment_items"]}
    moderate_ids = {
        item["assessment_item_id"] for item in moderate_profile["assessment_items"]
    }
    high_ids = {item["assessment_item_id"] for item in high_profile["assessment_items"]}

    assert "AC-11" in moderate_ids
    assert "AC-11" not in low_ids
    assert "AC-10" in high_ids
    assert "AC-10" not in moderate_ids
    assert low_ids < moderate_ids < high_ids


def test_extract_rejects_duplicate_baseline_control_id(
    nist_catalog_bundle: tuple[str, dict[str, object]],
    nist_moderate_baseline_bundle: tuple[str, dict[str, object]],
) -> None:
    _, catalog_document = nist_catalog_bundle
    _, baseline_document = nist_moderate_baseline_bundle
    mutated = copy.deepcopy(baseline_document)
    with_ids = mutated["profile"]["imports"][0]["include-controls"][0]["with-ids"]
    with_ids.append(with_ids[0])

    with pytest.raises(
        Rev5ProfileError,
        match="duplicate baseline control id 'AC-1'",
    ):
        extract_fedramp_rev5_transition_assessment_items(
            impact_level="moderate",
            catalog_document=catalog_document,
            baseline_document=mutated,
        )


def test_extract_rejects_missing_baseline_with_ids(
    nist_catalog_bundle: tuple[str, dict[str, object]],
    nist_moderate_baseline_bundle: tuple[str, dict[str, object]],
) -> None:
    _, catalog_document = nist_catalog_bundle
    _, baseline_document = nist_moderate_baseline_bundle
    mutated = copy.deepcopy(baseline_document)
    del mutated["profile"]["imports"][0]["include-controls"][0]["with-ids"]

    with pytest.raises(Rev5ProfileError, match="must declare with-ids"):
        extract_fedramp_rev5_transition_assessment_items(
            impact_level="moderate",
            catalog_document=catalog_document,
            baseline_document=mutated,
        )


def test_extract_rejects_malformed_statement_prose_for_selected_control(
    nist_catalog_bundle: tuple[str, dict[str, object]],
    nist_moderate_baseline_bundle: tuple[str, dict[str, object]],
) -> None:
    _, catalog_document = nist_catalog_bundle
    _, baseline_document = nist_moderate_baseline_bundle
    mutated_catalog = copy.deepcopy(catalog_document)
    control = mutated_catalog["catalog"]["groups"][0]["controls"][1]["controls"][0]
    del control["parts"]

    with pytest.raises(
        Rev5ProfileError,
        match="baseline control 'AC-2.1' has malformed statement prose",
    ):
        extract_fedramp_rev5_transition_assessment_items(
            impact_level="moderate",
            catalog_document=mutated_catalog,
            baseline_document=baseline_document,
        )


def test_extract_rejects_baseline_count_drift(
    nist_catalog_bundle: tuple[str, dict[str, object]],
    nist_moderate_baseline_bundle: tuple[str, dict[str, object]],
) -> None:
    _, catalog_document = nist_catalog_bundle
    _, baseline_document = nist_moderate_baseline_bundle
    mutated = copy.deepcopy(baseline_document)
    with_ids = mutated["profile"]["imports"][0]["include-controls"][0]["with-ids"]
    with_ids.pop()

    with pytest.raises(
        Rev5ProfileError,
        match=(
            f"expected {EXPECTED_BASELINE_CONTROL_COUNTS['moderate']} unique baseline controls"
        ),
    ):
        extract_fedramp_rev5_transition_assessment_items(
            impact_level="moderate",
            catalog_document=catalog_document,
            baseline_document=mutated,
        )


def test_compile_rejects_unsupported_impact_level() -> None:
    with pytest.raises(Rev5ProfileError, match="unsupported impact_level 'critical'"):
        compile_fedramp_rev5_transition_profile(
            manifest_path=MANIFEST_PATH,
            project_root=ROOT,
            generated_at=GENERATED_AT,
            impact_level="critical",
        )
