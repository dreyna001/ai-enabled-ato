"""Tests for the real FedRAMP 20x Program Class C profile compiler."""

from __future__ import annotations

import copy
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ato_service.analysis_profile import analysis_profile_sha256
from ato_service.analysis_profile_validation import validate_analysis_profile_semantics
from ato_service.authority_catalog import (
    load_json_authority_source,
    resolve_json_pointer,
)
from ato_service.fedramp_profile import (
    FedrampProfileError,
    compile_fedramp_20x_class_c_profile,
    extract_fedramp_20x_class_c_assessment_items,
    fedramp_20x_artifact_requirements,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "docs" / "contracts" / "authority-manifest.json"
MANIFEST_ID = "ato-authorities-2026-07-10-draft"
FEDRAMP_AUTHORITY_ID = "fedramp-consolidated-rules-2026"
CPO_SCHEMA_AUTHORITY_ID = "fedramp-schema-cpo-2026-06-24"
SDR_SCHEMA_AUTHORITY_ID = "fedramp-schema-sdr-2026-06-24"
OCR_SCHEMA_AUTHORITY_ID = "fedramp-schema-ocr-2026-06-24"
GENERATED_AT = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)
STABLE_PROFILE_DIGEST = (
    "3f1b04c5836539151c8cda5346896802947cfefec707fb94b1c0b9801793339b"
)

EXPECTED_FRR_RULE_COUNT = 155
EXPECTED_KSI_COUNT = 46
EXPECTED_TOTAL_ITEM_COUNT = 201

EXPECTED_FRR_FORCE_DISTRIBUTION = {
    "MUST": 91,
    "MUST_NOT": 5,
    "SHOULD": 40,
    "SHOULD_NOT": 4,
    "MAY": 15,
}

TWENTY_X_ONLY_RULE_IDS = (
    "CPO-CSX-CPM",
    "FRC-CSX-VVK",
    "FRC-CSX-MOT",
    "FRC-CSX-VVR",
    "FRC-CSX-MAS",
    "IVV-CSX-AIA",
    "SDR-CSX-KSI",
    "SDR-CSX-KMT",
    "VDR-TFR-MVX",
)

EXCLUDED_RULE_IDS = (
    "FRC-CLA-MFR",
    "MKT-IIP-AGU",
    "FRC-APS-ATO",
    "FRC-CCL-UCC",
)

AFC_CSO_INB_POINTER = "/FRR/AFC/data/all/CSO/AFC-CSO-INB"
FRC_CSX_VVK_POINTER = "/FRR/FRC/data/20x/CSX/FRC-CSX-VVK"
KSI_CNA_EIS_POINTER = "/KSI/CNA/indicators/KSI-CNA-EIS"
KSI_VVK_PARENT_POINTER = "/FRR/FRC/data/20x/CSX/FRC-CSX-VVK"

AFC_CSO_INB_STATEMENT = (
    "Providers MUST establish and maintain an email address to receive messages "
    "from FedRAMP; this inbox is a FedRAMP Security Inbox (FSI)."
)
FRC_CSX_VVK_CLASS_C_STATEMENT = (
    "Providers seeking 20x Class C Certification MUST implement automated methods "
    "to persistently verify and validate the accuracy and completeness of Key "
    "Security Indicators with at least 2 automated methods for each Key Security "
    "Indicator."
)
KSI_CNA_EIS_CLASS_C_STATEMENT = (
    "Automated services are used to persistently assess the security of all "
    "machine-based information resources and automatically enforce their intended "
    "operational state."
)

ARTIFACT_EXPECTATIONS: dict[str, dict[str, object]] = {
    "cpo": {
        "display_name": "Certification Package Overview",
        "owner": "provider",
        "official_schema_authority_id": CPO_SCHEMA_AUTHORITY_ID,
        "human_readable_required": True,
        "machine_readable_required": True,
        "export_paths": ["human/cpo.md", "machine/cpo.json"],
        "source_pointers": ["/FRR/CPO/data/all/CSO/CPO-CSO-OVR"],
    },
    "sdr": {
        "display_name": "Security Decision Record",
        "owner": "provider",
        "official_schema_authority_id": SDR_SCHEMA_AUTHORITY_ID,
        "human_readable_required": True,
        "machine_readable_required": True,
        "export_paths": ["human/sdr.md", "machine/sdr.json"],
        "source_pointers": ["/FRR/SDR/data/all/CSO/SDR-CSO-FRR"],
    },
    "ocr": {
        "display_name": "Ongoing Certification Report",
        "owner": "provider",
        "official_schema_authority_id": OCR_SCHEMA_AUTHORITY_ID,
        "human_readable_required": True,
        "machine_readable_required": True,
        "export_paths": ["human/ocr.md", "machine/ocr.json"],
        "source_pointers": ["/FRR/CCM/data/all/OCR/CCM-OCR-AVL"],
    },
    "scg": {
        "display_name": "Secure Configuration Guide",
        "owner": "provider",
        "official_schema_authority_id": None,
        "human_readable_required": True,
        "machine_readable_required": False,
        "export_paths": ["human/scg-readiness.md"],
        "source_pointers": [
            "/FRR/SCG/data/all/CSO/SCG-CSO-RSC",
            "/FRR/SCG/data/all/CSO/SCG-CSO-AUP",
        ],
    },
    "independent_assessment": {
        "display_name": "FedRAMP Independent Assessment Results",
        "owner": "assessor",
        "official_schema_authority_id": None,
        "human_readable_required": True,
        "machine_readable_required": False,
        "export_paths": ["provenance/assessor-imports.json"],
        "source_pointers": [
            "/FRR/IVV/data/all/CSO/IVV-CSO-ICP",
            "/FRR/IVV/data/all/IAS/IVV-IAS-OSA",
        ],
    },
    "ksi_metric_material": {
        "display_name": "Key Security Indicator Methods and Metric History",
        "owner": "shared",
        "official_schema_authority_id": None,
        "human_readable_required": True,
        "machine_readable_required": False,
        "export_paths": ["human/ksi-summary.md", "machine/ksi-summary.json"],
        "source_pointers": [
            "/FRR/FRC/data/20x/CSX/FRC-CSX-VVK",
            "/FRR/FRC/data/20x/CSX/FRC-CSX-MOT",
        ],
    },
}


@pytest.fixture
def authority_manifest() -> dict[str, object]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def fedramp_document(authority_manifest: dict[str, object]) -> dict[str, object]:
    return load_json_authority_source(
        manifest=authority_manifest,
        authority_id=FEDRAMP_AUTHORITY_ID,
        project_root=ROOT,
    )


def _frr_items(items: list[dict[str, object]]) -> list[dict[str, object]]:
    return [item for item in items if item["assessment_item_type"] == "fedramp_rule"]


def _ksi_items(items: list[dict[str, object]]) -> list[dict[str, object]]:
    return [item for item in items if item["assessment_item_type"] == "fedramp_ksi"]


def _item_by_id(items: list[dict[str, object]], item_id: str) -> dict[str, object]:
    return next(item for item in items if item["assessment_item_id"] == item_id)


def _resolve_all_authority_refs(
    profile: dict[str, object],
    *,
    document: dict[str, object],
) -> None:
    for section_name in ("assessment_items", "artifact_requirements", "cadence_rules"):
        for entry in profile.get(section_name, []):
            for ref in entry.get("authority_refs", []):
                resolve_json_pointer(document, ref["source_pointer"])


def test_extract_yields_exactly_201_unique_sorted_items(
    fedramp_document: dict[str, object],
) -> None:
    items = extract_fedramp_20x_class_c_assessment_items(fedramp_document)

    assert len(items) == EXPECTED_TOTAL_ITEM_COUNT
    assert len(_frr_items(items)) == EXPECTED_FRR_RULE_COUNT
    assert len(_ksi_items(items)) == EXPECTED_KSI_COUNT

    item_ids = [item["assessment_item_id"] for item in items]
    assert item_ids == sorted(item_ids)
    assert len(set(item_ids)) == EXPECTED_TOTAL_ITEM_COUNT


def test_extract_frr_force_distribution(fedramp_document: dict[str, object]) -> None:
    items = extract_fedramp_20x_class_c_assessment_items(fedramp_document)
    forces = Counter(item["force"] for item in _frr_items(items))

    assert dict(sorted(forces.items())) == EXPECTED_FRR_FORCE_DISTRIBUTION


def test_extract_includes_required_rules_and_ksi(
    fedramp_document: dict[str, object],
) -> None:
    items = extract_fedramp_20x_class_c_assessment_items(fedramp_document)
    item_ids = {item["assessment_item_id"] for item in items}

    assert "AFC-CSO-INB" in item_ids
    assert "FRC-CSX-VVK" in item_ids
    assert "KSI-CNA-EIS" in item_ids
    assert all(rule_id in item_ids for rule_id in TWENTY_X_ONLY_RULE_IDS)


def test_extract_excludes_class_a_rev5_and_empty_class_rules(
    fedramp_document: dict[str, object],
) -> None:
    items = extract_fedramp_20x_class_c_assessment_items(fedramp_document)
    item_ids = {item["assessment_item_id"] for item in items}

    for rule_id in EXCLUDED_RULE_IDS:
        assert rule_id not in item_ids


def test_extract_uses_class_c_statement_for_varies_by_class_rule(
    fedramp_document: dict[str, object],
) -> None:
    items = extract_fedramp_20x_class_c_assessment_items(fedramp_document)
    vvk = _item_by_id(items, "FRC-CSX-VVK")

    assert vvk["requirement_text"] == FRC_CSX_VVK_CLASS_C_STATEMENT
    assert vvk["force"] == "MUST"
    assert vvk["authority_refs"][0]["source_pointer"] == FRC_CSX_VVK_POINTER


def test_extract_uses_class_c_statement_for_varies_by_class_ksi(
    fedramp_document: dict[str, object],
) -> None:
    items = extract_fedramp_20x_class_c_assessment_items(fedramp_document)
    eis = _item_by_id(items, "KSI-CNA-EIS")

    assert eis["requirement_text"] == KSI_CNA_EIS_CLASS_C_STATEMENT
    assert eis["title"] == "Enforcing Intended State"
    assert eis["authority_refs"][0]["source_pointer"] == KSI_CNA_EIS_POINTER


def test_extract_authority_refs_resolve_and_match_official_statements(
    fedramp_document: dict[str, object],
) -> None:
    items = extract_fedramp_20x_class_c_assessment_items(fedramp_document)

    for item in items:
        for ref in item["authority_refs"]:
            assert ref["authority_id"] == FEDRAMP_AUTHORITY_ID
            resolve_json_pointer(fedramp_document, ref["source_pointer"])

    inb = _item_by_id(items, "AFC-CSO-INB")
    assert inb["title"] == "Maintain a FedRAMP Security Inbox"
    assert inb["requirement_text"] == AFC_CSO_INB_STATEMENT
    assert inb["authority_refs"][0]["source_pointer"] == AFC_CSO_INB_POINTER

    source_rule = resolve_json_pointer(fedramp_document, AFC_CSO_INB_POINTER)
    assert inb["requirement_text"] == source_rule["statement"]


def test_extract_ksi_items_include_parent_rule_pointers(
    fedramp_document: dict[str, object],
) -> None:
    items = extract_fedramp_20x_class_c_assessment_items(fedramp_document)
    eis = _item_by_id(items, "KSI-CNA-EIS")
    parent_pointers = {ref["source_pointer"] for ref in eis["authority_refs"]}

    assert KSI_CNA_EIS_POINTER in parent_pointers
    assert KSI_VVK_PARENT_POINTER in parent_pointers
    assert "/FRR/FRC/data/20x/CSX/FRC-CSX-MOT" in parent_pointers
    assert "/FRR/IVV/data/20x/CSX/IVV-CSX-AIA" in parent_pointers


def test_fedramp_20x_artifact_requirements_declares_six_artifacts() -> None:
    artifacts = fedramp_20x_artifact_requirements()

    assert [artifact["artifact_id"] for artifact in artifacts] == [
        "cpo",
        "sdr",
        "ocr",
        "scg",
        "independent_assessment",
        "ksi_metric_material",
    ]


@pytest.mark.parametrize("artifact_id", tuple(ARTIFACT_EXPECTATIONS))
def test_fedramp_20x_artifact_requirements_match_contract(
    artifact_id: str,
    fedramp_document: dict[str, object],
) -> None:
    expected = ARTIFACT_EXPECTATIONS[artifact_id]
    artifact = next(
        item
        for item in fedramp_20x_artifact_requirements()
        if item["artifact_id"] == artifact_id
    )

    assert artifact["display_name"] == expected["display_name"]
    assert artifact["required"] is True
    assert artifact["owner"] == expected["owner"]
    assert artifact["official_schema_authority_id"] == expected["official_schema_authority_id"]
    assert artifact["human_readable_required"] == expected["human_readable_required"]
    assert artifact["machine_readable_required"] == expected["machine_readable_required"]
    assert artifact["export_paths"] == expected["export_paths"]

    pointers = [ref["source_pointer"] for ref in artifact["authority_refs"]]
    assert pointers == expected["source_pointers"]
    for ref in artifact["authority_refs"]:
        assert ref["authority_id"] == FEDRAMP_AUTHORITY_ID
        resolve_json_pointer(fedramp_document, ref["source_pointer"])


def test_compile_fedramp_20x_class_c_profile_is_schema_and_semantically_valid(
    fedramp_document: dict[str, object],
) -> None:
    profile = compile_fedramp_20x_class_c_profile(
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
        generated_at=GENERATED_AT,
        profile_version="1.0.0",
    )

    assert profile["schema_version"] == "2.0.0"
    assert profile["profile_id"] == "fedramp_20x_program"
    assert profile["profile_version"] == "1.0.0"
    assert profile["authority_manifest_id"] == MANIFEST_ID
    assert profile["generated_at"] == "2026-07-14T16:00:00Z"
    assert profile["qualification_status"] == "draft"
    assert profile["certification_class"] == "C"
    assert profile["impact_level"] is None
    assert profile["cadence_rules"] == []
    assert profile["status_policy"]["exact_row_coverage_required"] is True
    assert profile["status_policy"]["system_may_be_more_favorable_than_model"] is False
    assert profile["status_policy"]["repair_attempts"] == 1

    validate_analysis_profile_semantics(
        profile,
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
    )
    _resolve_all_authority_refs(profile, document=fedramp_document)


def test_compile_fedramp_20x_class_c_profile_digest_is_stable() -> None:
    profile = compile_fedramp_20x_class_c_profile(
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
        generated_at=GENERATED_AT,
        profile_version="1.0.0",
    )

    assert analysis_profile_sha256(profile) == STABLE_PROFILE_DIGEST

    second = compile_fedramp_20x_class_c_profile(
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
        generated_at=GENERATED_AT,
        profile_version="1.0.0",
    )
    assert analysis_profile_sha256(second) == STABLE_PROFILE_DIGEST


def test_extract_rejects_malformed_subset_applicability(
    fedramp_document: dict[str, object],
) -> None:
    mutated = copy.deepcopy(fedramp_document)
    mutated["FRR"]["AFC"]["info"]["subsets"]["CSO"]["applicability"] = "not-an-object"

    with pytest.raises(FedrampProfileError, match="subset applicability must be an object"):
        extract_fedramp_20x_class_c_assessment_items(mutated)


def test_extract_rejects_malformed_applicable_rule_missing_statement(
    fedramp_document: dict[str, object],
) -> None:
    mutated = copy.deepcopy(fedramp_document)
    del mutated["FRR"]["AFC"]["data"]["all"]["CSO"]["AFC-CSO-INB"]["statement"]

    with pytest.raises(
        FedrampProfileError,
        match=r"FRR rule at /FRR/AFC/data/all/CSO/AFC-CSO-INB must declare a nonempty statement",
    ):
        extract_fedramp_20x_class_c_assessment_items(mutated)


def test_extract_rejects_duplicate_frr_rule_id(
    fedramp_document: dict[str, object],
) -> None:
    mutated = copy.deepcopy(fedramp_document)
    mutated["FRR"]["CCM"]["data"]["all"]["OCR"]["AFC-CSO-INB"] = copy.deepcopy(
        mutated["FRR"]["AFC"]["data"]["all"]["CSO"]["AFC-CSO-INB"]
    )

    with pytest.raises(
        FedrampProfileError,
        match="duplicate FRR assessment_item_id 'AFC-CSO-INB'",
    ):
        extract_fedramp_20x_class_c_assessment_items(mutated)


def test_extract_rejects_format_drift_when_rule_count_changes(
    fedramp_document: dict[str, object],
) -> None:
    mutated = copy.deepcopy(fedramp_document)
    del mutated["FRR"]["AFC"]["data"]["all"]["CSO"]["AFC-CSO-INB"]

    with pytest.raises(
        FedrampProfileError,
        match=f"expected {EXPECTED_FRR_RULE_COUNT} fedramp_rule assessment items",
    ):
        extract_fedramp_20x_class_c_assessment_items(mutated)


def test_extract_rejects_unrecognized_force(
    fedramp_document: dict[str, object],
) -> None:
    mutated = copy.deepcopy(fedramp_document)
    mutated["FRR"]["AFC"]["data"]["all"]["CSO"]["AFC-CSO-INB"]["force"] = "REQUIRED"

    with pytest.raises(
        FedrampProfileError,
        match=r"unrecognized force 'REQUIRED'",
    ):
        extract_fedramp_20x_class_c_assessment_items(mutated)
