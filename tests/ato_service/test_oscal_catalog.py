"""Tests for shared NIST OSCAL catalog control indexing."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from ato_service.authority_catalog import load_json_authority_archive_member
from ato_service.authority_manifest import verify_authority_manifest
from ato_service.oscal_catalog import (
    OscalCatalogError,
    index_oscal_catalog_controls,
    normalize_oscal_control_id,
)
from ato_service.rev5_profile import CATALOG_MEMBER_SUFFIX

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "docs" / "contracts" / "authority-manifest.json"
NIST_AUTHORITY_ID = "nist-sp800-53-release-5.2.0"

AC_2_1_REQUIREMENT_TEXT = (
    "Support the management of system accounts using "
    "{{ insert: param, ac-02.01_odp }}."
)


@pytest.fixture
def authority_manifest() -> dict[str, object]:
    return verify_authority_manifest(MANIFEST_PATH, project_root=ROOT)


@pytest.fixture
def nist_catalog_document(authority_manifest: dict[str, object]) -> dict[str, object]:
    _member_name, document = load_json_authority_archive_member(
        manifest=authority_manifest,
        authority_id=NIST_AUTHORITY_ID,
        project_root=ROOT,
        member_suffix=CATALOG_MEMBER_SUFFIX,
    )
    return document


def test_normalize_oscal_control_id_uppercases_only() -> None:
    assert normalize_oscal_control_id("ac-2.1") == "AC-2.1"
    assert normalize_oscal_control_id(" CM-2 ") == "CM-2"


def test_normalize_oscal_control_id_rejects_empty() -> None:
    with pytest.raises(OscalCatalogError, match="control id must be a non-empty string"):
        normalize_oscal_control_id("")
    with pytest.raises(OscalCatalogError, match="control id must be a non-empty string"):
        normalize_oscal_control_id("   ")


def test_index_oscal_catalog_controls_indexes_pinned_catalog(
    nist_catalog_document: dict[str, object],
) -> None:
    index = index_oscal_catalog_controls(nist_catalog_document)

    assert len(index) > 1000
    assert "AC-2" in index
    assert "AC-2.1" in index
    assert "CM-2" in index

    ac2 = index["AC-2"]
    assert ac2.normalized_id == "AC-2"
    assert ac2.title == "Account Management"
    assert ac2.catalog_pointer == "/catalog/groups/0/controls/1"
    assert ac2.requirement_text.startswith(
        "a. Define and document the types of accounts allowed"
    )
    assert "b. Assign account managers;" in ac2.requirement_text

    ac21 = index["AC-2.1"]
    assert ac21.title == "Automated System Account Management"
    assert ac21.requirement_text == AC_2_1_REQUIREMENT_TEXT
    assert ac21.catalog_pointer == "/catalog/groups/0/controls/1/controls/0"


def test_index_oscal_catalog_controls_rejects_missing_catalog() -> None:
    with pytest.raises(OscalCatalogError, match="catalog document must include catalog"):
        index_oscal_catalog_controls({})


def test_index_oscal_catalog_controls_rejects_missing_groups() -> None:
    with pytest.raises(OscalCatalogError, match="catalog must declare groups"):
        index_oscal_catalog_controls({"catalog": {}})


def test_index_oscal_catalog_controls_rejects_empty_index() -> None:
    with pytest.raises(OscalCatalogError, match="catalog index is empty"):
        index_oscal_catalog_controls({"catalog": {"groups": []}})


def test_index_oscal_catalog_controls_rejects_non_object_group() -> None:
    with pytest.raises(
        OscalCatalogError,
        match="catalog group at /catalog/groups/0 must be an object",
    ):
        index_oscal_catalog_controls({"catalog": {"groups": ["not-a-group"]}})


def test_index_oscal_catalog_controls_rejects_duplicate_control_id(
    nist_catalog_document: dict[str, object],
) -> None:
    mutated = copy.deepcopy(nist_catalog_document)
    duplicate = copy.deepcopy(
        mutated["catalog"]["groups"][0]["controls"][0]
    )
    mutated["catalog"]["groups"][0]["controls"].append(duplicate)

    with pytest.raises(
        OscalCatalogError,
        match="duplicate catalog control id 'AC-1'",
    ):
        index_oscal_catalog_controls(mutated)


def test_index_oscal_catalog_controls_leaves_empty_requirement_for_malformed_statement(
    nist_catalog_document: dict[str, object],
) -> None:
    mutated = copy.deepcopy(nist_catalog_document)
    control = mutated["catalog"]["groups"][0]["controls"][1]["controls"][0]
    del control["parts"]

    index = index_oscal_catalog_controls(mutated)

    assert index["AC-2.1"].requirement_text == ""


def test_index_oscal_catalog_controls_leaves_empty_requirement_for_non_object_parts(
    nist_catalog_document: dict[str, object],
) -> None:
    mutated = copy.deepcopy(nist_catalog_document)
    control = mutated["catalog"]["groups"][0]["controls"][1]["controls"][0]
    control["parts"] = ["not-a-part"]

    index = index_oscal_catalog_controls(mutated)

    assert index["AC-2.1"].requirement_text == ""
