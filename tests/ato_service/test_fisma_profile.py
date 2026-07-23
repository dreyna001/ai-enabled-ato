"""Tests for the real FISMA agency security profile compiler."""

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
    resolve_json_pointer,
)
from ato_service.authority_manifest import verify_authority_manifest
from ato_service.fisma_control_inventory import (
    FismaControlInventory,
    FismaControlInventoryError,
    load_fisma_control_inventory,
)
from ato_service.fisma_generator import FISMA_EXPORT_PATHS
from ato_service.fisma_profile import (
    FismaProfileError,
    compile_fisma_agency_security_profile,
    extract_fisma_agency_security_assessment_items,
    fisma_agency_artifact_requirements,
)
from ato_service.rev5_profile import CATALOG_MEMBER_SUFFIX

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "docs" / "contracts" / "authority-manifest.json"
MANIFEST_ID = "ato-authorities-2026-07-10-draft"
NIST_AUTHORITY_ID = "nist-sp800-53-release-5.2.0"
VALID_INVENTORY_PATH = (
    ROOT / "docs" / "contracts" / "fixtures" / "fisma-control-inventory.valid.example.json"
)
GENERATED_AT = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)
STABLE_PROFILE_DIGEST = (
    "c2c50046a1827adb0ba870e9d7f0ece1b883369bf8b532736eec42f202e13bd4"
)

EXPECTED_CONTROL_IDS = ("AC-1", "AC-2", "IA-5")

AC_1_TITLE = "Policy and Procedures"
AC_2_TITLE = "Account Management"
IA_5_TITLE = "Authenticator Management"

AC_1_PROSE_PREFIX = (
    "a. Develop, document, and disseminate to {{ insert: param, ac-1_prm_1 }}:"
)
AC_2_PROSE_PREFIX = (
    "a. Define and document the types of accounts allowed and specifically "
    "prohibited for use within the system;"
)
IA_5_PROSE_PREFIX = "Manage system authenticators by:"

ARTIFACT_EXPECTATIONS: dict[str, dict[str, object]] = {
    "ssp_security_draft": {
        "display_name": "SSP Security Draft",
        "owner": "system_owner",
        "required": True,
        "human_readable_required": True,
        "machine_readable_required": True,
        "export_paths": [
            "human/ssp-security-draft.md",
            "machine/ssp-security-draft.json",
        ],
    },
    "sar_input_pack": {
        "display_name": "SAR Input Pack",
        "owner": "assessor",
        "required": True,
        "human_readable_required": True,
        "machine_readable_required": True,
        "export_paths": [
            "human/sar-input-pack.md",
            "machine/sar-input-pack.json",
        ],
    },
    "poam_draft": {
        "display_name": "POA&M Draft",
        "owner": "system_owner",
        "required": True,
        "human_readable_required": True,
        "machine_readable_required": True,
        "export_paths": [
            "human/poam-draft.md",
            "machine/poam-draft.json",
        ],
    },
    "security_readiness_matrix": {
        "display_name": "Security Readiness and Assessment Matrix",
        "owner": "product_analysis",
        "required": True,
        "human_readable_required": True,
        "machine_readable_required": True,
        "export_paths": [
            "human/security-readiness.md",
            "machine/security-readiness.json",
            "human/assessment-matrix.md",
            "validation/fisma-export-readiness.json",
        ],
    },
}


@pytest.fixture
def authority_manifest() -> dict[str, object]:
    return verify_authority_manifest(MANIFEST_PATH, project_root=ROOT)


@pytest.fixture
def example_inventory() -> FismaControlInventory:
    return load_fisma_control_inventory(VALID_INVENTORY_PATH, project_root=ROOT)


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
) -> None:
    for section_name in ("assessment_items", "artifact_requirements", "cadence_rules"):
        for entry in profile.get(section_name, []):
            for ref in entry.get("authority_refs", []):
                _resolve_nist_archive_ref(
                    authority_manifest=authority_manifest,
                    ref=ref,
                )


def _inventory_from_example(**overrides: object) -> FismaControlInventory:
    base = load_fisma_control_inventory(VALID_INVENTORY_PATH, project_root=ROOT)
    fields = {
        "schema_version": base.schema_version,
        "inventory_id": base.inventory_id,
        "authority_manifest_id": base.authority_manifest_id,
        "impact_level": base.impact_level,
        "status": base.status,
        "approved_at": base.approved_at,
        "approved_by": base.approved_by,
        "source_reference": base.source_reference,
        "control_ids": base.control_ids,
    }
    fields.update(overrides)
    return FismaControlInventory(**fields)


def test_load_valid_example_inventory_has_exact_three_control_ids(
    example_inventory: FismaControlInventory,
) -> None:
    assert example_inventory.control_ids == EXPECTED_CONTROL_IDS
    assert example_inventory.impact_level == "moderate"
    assert example_inventory.authority_manifest_id == MANIFEST_ID


def test_extract_yields_exactly_three_inventory_controls(
    example_inventory: FismaControlInventory,
    nist_catalog_bundle: tuple[str, dict[str, object]],
) -> None:
    catalog_member, catalog_document = nist_catalog_bundle
    items = extract_fisma_agency_security_assessment_items(
        inventory=example_inventory,
        catalog_document=catalog_document,
        catalog_archive_member=catalog_member,
    )

    item_ids = [item["assessment_item_id"] for item in items]
    assert item_ids == list(EXPECTED_CONTROL_IDS)
    assert len(set(item_ids)) == 3


def test_extract_representative_controls_use_official_catalog_title_and_prose(
    example_inventory: FismaControlInventory,
    nist_catalog_bundle: tuple[str, dict[str, object]],
) -> None:
    catalog_member, catalog_document = nist_catalog_bundle
    items = extract_fisma_agency_security_assessment_items(
        inventory=example_inventory,
        catalog_document=catalog_document,
        catalog_archive_member=catalog_member,
    )

    ac1 = _item_by_id(items, "AC-1")
    assert ac1["title"] == AC_1_TITLE
    assert ac1["requirement_text"].startswith(AC_1_PROSE_PREFIX)
    assert ac1["requirement_text"].strip()

    ac2 = _item_by_id(items, "AC-2")
    assert ac2["title"] == AC_2_TITLE
    assert ac2["requirement_text"].startswith(AC_2_PROSE_PREFIX)
    assert ac2["requirement_text"].strip()

    ia5 = _item_by_id(items, "IA-5")
    assert ia5["title"] == IA_5_TITLE
    assert ia5["requirement_text"].startswith(IA_5_PROSE_PREFIX)
    assert ia5["requirement_text"].strip()


def test_extract_assessment_items_match_fisma_contract(
    example_inventory: FismaControlInventory,
    nist_catalog_bundle: tuple[str, dict[str, object]],
    authority_manifest: dict[str, object],
) -> None:
    catalog_member, catalog_document = nist_catalog_bundle
    items = extract_fisma_agency_security_assessment_items(
        inventory=example_inventory,
        catalog_document=catalog_document,
        catalog_archive_member=catalog_member,
    )

    for item in items:
        assert item["assessment_item_type"] == "nist_control"
        assert item["force"] == "customer_required"
        assert item["owner"] == "system_owner"
        assert item["required_evidence_kinds"] == []
        assert item["model_analysis_allowed"] is False
        assert item["applicability"] == {
            "paths": ["agency_fisma"],
            "classes": [],
            "impact_levels": ["moderate"],
            "affects": ["system_owner"],
        }
        assert len(item["authority_refs"]) == 1
        ref = item["authority_refs"][0]
        assert ref["authority_id"] == NIST_AUTHORITY_ID
        assert ref["archive_member"] == catalog_member
        assert isinstance(ref["source_pointer"], str)
        assert ref["source_pointer"].startswith("/catalog/")
        _resolve_nist_archive_ref(authority_manifest=authority_manifest, ref=ref)


def test_extract_does_not_add_controls_beyond_inventory(
    example_inventory: FismaControlInventory,
    nist_catalog_bundle: tuple[str, dict[str, object]],
) -> None:
    catalog_member, catalog_document = nist_catalog_bundle
    items = extract_fisma_agency_security_assessment_items(
        inventory=example_inventory,
        catalog_document=catalog_document,
        catalog_archive_member=catalog_member,
    )

    assert {item["assessment_item_id"] for item in items} == set(
        example_inventory.control_ids
    )


def test_fisma_agency_artifact_requirements_declares_four_artifact_groups() -> None:
    artifacts = fisma_agency_artifact_requirements()

    assert [artifact["artifact_id"] for artifact in artifacts] == [
        "ssp_security_draft",
        "sar_input_pack",
        "poam_draft",
        "security_readiness_matrix",
    ]


@pytest.mark.parametrize("artifact_id", tuple(ARTIFACT_EXPECTATIONS))
def test_fisma_agency_artifact_requirements_match_contract(
    artifact_id: str,
) -> None:
    expected = ARTIFACT_EXPECTATIONS[artifact_id]
    artifact = next(
        item
        for item in fisma_agency_artifact_requirements()
        if item["artifact_id"] == artifact_id
    )

    assert artifact["display_name"] == expected["display_name"]
    assert artifact["required"] == expected["required"]
    assert artifact["owner"] == expected["owner"]
    assert artifact["official_schema_authority_id"] is None
    assert artifact["human_readable_required"] == expected["human_readable_required"]
    assert artifact["machine_readable_required"] == expected["machine_readable_required"]
    assert artifact["export_paths"] == expected["export_paths"]
    assert artifact["authority_refs"] == []


def test_fisma_agency_artifact_requirements_cover_fisma_export_paths_exactly_once() -> None:
    artifacts = fisma_agency_artifact_requirements()
    covered_paths: list[str] = []
    for artifact in artifacts:
        covered_paths.extend(artifact["export_paths"])

    assert covered_paths == list(FISMA_EXPORT_PATHS)
    assert len(covered_paths) == len(set(covered_paths))


def test_compile_fisma_agency_security_profile_is_schema_and_semantically_valid(
    example_inventory: FismaControlInventory,
    authority_manifest: dict[str, object],
) -> None:
    profile = compile_fisma_agency_security_profile(
        inventory=example_inventory,
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
        generated_at=GENERATED_AT,
        profile_version="1.0.0",
    )

    assert profile["schema_version"] == "2.0.0"
    assert profile["profile_id"] == "fisma_agency_security"
    assert profile["profile_version"] == "1.0.0"
    assert profile["authority_manifest_id"] == MANIFEST_ID
    assert profile["generated_at"] == "2026-07-14T16:00:00Z"
    assert profile["qualification_status"] == "draft"
    assert profile["certification_class"] is None
    assert profile["impact_level"] == "moderate"
    assert profile["cadence_rules"] == []
    assert [item["assessment_item_id"] for item in profile["assessment_items"]] == list(
        EXPECTED_CONTROL_IDS
    )
    assert profile["status_policy"]["exact_row_coverage_required"] is True
    assert profile["status_policy"]["system_may_be_more_favorable_than_model"] is False
    assert profile["status_policy"]["repair_attempts"] == 1

    validate_analysis_profile_semantics(
        profile,
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
    )
    _resolve_profile_authority_refs(
        profile,
        authority_manifest=authority_manifest,
    )


def test_compile_fisma_agency_security_profile_digest_is_stable(
    example_inventory: FismaControlInventory,
) -> None:
    profile = compile_fisma_agency_security_profile(
        inventory=example_inventory,
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
        generated_at=GENERATED_AT,
        profile_version="1.0.0",
    )
    assert analysis_profile_sha256(profile) == STABLE_PROFILE_DIGEST

    second = compile_fisma_agency_security_profile(
        inventory=example_inventory,
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
        generated_at=GENERATED_AT,
        profile_version="1.0.0",
    )
    assert analysis_profile_sha256(second) == STABLE_PROFILE_DIGEST


def test_compile_fisma_agency_security_profile_digest_changes_with_inventory(
    example_inventory: FismaControlInventory,
) -> None:
    baseline = compile_fisma_agency_security_profile(
        inventory=example_inventory,
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
        generated_at=GENERATED_AT,
    )
    reduced_inventory = _inventory_from_example(control_ids=("AC-1", "AC-2"))
    reduced = compile_fisma_agency_security_profile(
        inventory=reduced_inventory,
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
        generated_at=GENERATED_AT,
    )

    assert analysis_profile_sha256(reduced) != analysis_profile_sha256(baseline)


def test_compile_rejects_inventory_manifest_id_mismatch(
    example_inventory: FismaControlInventory,
) -> None:
    mismatched = _inventory_from_example(
        authority_manifest_id="wrong-manifest-id",
    )

    with pytest.raises(
        FismaProfileError,
        match="authority_manifest_id",
    ):
        compile_fisma_agency_security_profile(
            inventory=mismatched,
            manifest_path=MANIFEST_PATH,
            project_root=ROOT,
            generated_at=GENERATED_AT,
        )


def test_load_rejects_inventory_manifest_id_mismatch_before_compile(
    tmp_path: Path,
) -> None:
    document = json.loads(VALID_INVENTORY_PATH.read_text(encoding="utf-8"))
    document["authority_manifest_id"] = "wrong-manifest-id"
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(
        FismaControlInventoryError,
        match="authority_manifest_id.*does not match authority manifest",
    ):
        load_fisma_control_inventory(inventory_path, project_root=ROOT)


def test_extract_rejects_unknown_control_id_from_constructed_inventory(
    nist_catalog_bundle: tuple[str, dict[str, object]],
) -> None:
    catalog_member, catalog_document = nist_catalog_bundle
    inventory = _inventory_from_example(
        control_ids=("AC-1", "AC-2", "IA-5", "ZZ-99"),
    )

    with pytest.raises(
        FismaProfileError,
        match=r"control 'ZZ-99'|missing from catalog",
    ):
        extract_fisma_agency_security_assessment_items(
            inventory=inventory,
            catalog_document=catalog_document,
            catalog_archive_member=catalog_member,
        )


def test_extract_rejects_privacy_family_control_id_defensively(
    nist_catalog_bundle: tuple[str, dict[str, object]],
) -> None:
    catalog_member, catalog_document = nist_catalog_bundle
    inventory = _inventory_from_example(control_ids=("AC-1", "PT-1"))

    with pytest.raises(
        FismaProfileError,
        match=r"privacy-family control_id 'PT-1'|privacy-family control",
    ):
        extract_fisma_agency_security_assessment_items(
            inventory=inventory,
            catalog_document=catalog_document,
            catalog_archive_member=catalog_member,
        )


def test_extract_rejects_missing_catalog_control(
    nist_catalog_bundle: tuple[str, dict[str, object]],
) -> None:
    catalog_member, catalog_document = nist_catalog_bundle
    inventory = _inventory_from_example(control_ids=("AC-2",))
    mutated_catalog = copy.deepcopy(catalog_document)
    groups = mutated_catalog["catalog"]["groups"]
    groups[0]["controls"] = [
        control
        for control in groups[0]["controls"]
        if control.get("id") != "ac-2"
    ]

    with pytest.raises(
        FismaProfileError,
        match=r"control 'AC-2'|missing from catalog",
    ):
        extract_fisma_agency_security_assessment_items(
            inventory=inventory,
            catalog_document=mutated_catalog,
            catalog_archive_member=catalog_member,
        )


def test_extract_rejects_malformed_statement_prose(
    example_inventory: FismaControlInventory,
    nist_catalog_bundle: tuple[str, dict[str, object]],
) -> None:
    catalog_member, catalog_document = nist_catalog_bundle
    mutated_catalog = copy.deepcopy(catalog_document)
    control = mutated_catalog["catalog"]["groups"][0]["controls"][1]
    assert control["id"] == "ac-2"
    del control["parts"]

    with pytest.raises(
        FismaProfileError,
        match=r"control 'AC-2'.*malformed statement prose",
    ):
        extract_fisma_agency_security_assessment_items(
            inventory=example_inventory,
            catalog_document=mutated_catalog,
            catalog_archive_member=catalog_member,
        )


def test_compile_does_not_silently_normalize_inventory_manifest_binding(
    example_inventory: FismaControlInventory,
) -> None:
    raw_document = json.loads(VALID_INVENTORY_PATH.read_text(encoding="utf-8"))
    assert raw_document["authority_manifest_id"] != "synthetic-normalized-id"

    with pytest.raises(FismaProfileError, match="authority_manifest_id"):
        compile_fisma_agency_security_profile(
            inventory=_inventory_from_example(
                authority_manifest_id="synthetic-normalized-id",
            ),
            manifest_path=MANIFEST_PATH,
            project_root=ROOT,
            generated_at=GENERATED_AT,
        )

    profile = compile_fisma_agency_security_profile(
        inventory=example_inventory,
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
        generated_at=GENERATED_AT,
    )
    assert profile["authority_manifest_id"] == example_inventory.authority_manifest_id
    assert profile["impact_level"] == example_inventory.impact_level
