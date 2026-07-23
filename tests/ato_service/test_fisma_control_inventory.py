"""Tests for FISMA control inventory loading and validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ato_service.fisma_control_inventory import (
    FismaControlInventory,
    FismaControlInventoryError,
    load_fisma_control_inventory,
)
from tests.support.platform import requires_symlink

ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = ROOT / "docs" / "contracts" / "fixtures"
VALID_FIXTURE = FIXTURES_DIR / "fisma-control-inventory.valid.example.json"
SCHEMA_PATH = ROOT / "docs" / "contracts" / "fisma-control-inventory.schema.json"
MANIFEST_ID = "ato-authorities-2026-07-10-draft"


def test_load_valid_example_returns_sorted_control_ids() -> None:
    inventory = load_fisma_control_inventory(
        VALID_FIXTURE,
        project_root=ROOT,
        schema_path=SCHEMA_PATH,
    )

    assert inventory == FismaControlInventory(
        schema_version="1.0.0",
        inventory_id="agency-moderate-inventory-example",
        authority_manifest_id="ato-authorities-2026-07-10-draft",
        impact_level="moderate",
        status="draft",
        approved_at=None,
        approved_by=None,
        source_reference=(
            "Example agency security baseline workbook reference for contract tests only; "
            "not a production customer inventory."
        ),
        control_ids=("AC-1", "AC-2", "IA-5"),
    )


def test_load_reorders_unsorted_control_ids_deterministically(
    tmp_path: Path,
) -> None:
    document = json.loads(VALID_FIXTURE.read_text(encoding="utf-8"))
    document["control_ids"] = ["IA-5", "AC-1", "AC-2"]
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(document), encoding="utf-8")

    inventory = load_fisma_control_inventory(
        inventory_path,
        project_root=ROOT,
        schema_path=SCHEMA_PATH,
    )

    assert inventory.control_ids == ("AC-1", "AC-2", "IA-5")


def test_load_rejects_unreadable_file(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.json"

    with pytest.raises(FismaControlInventoryError, match="regular file"):
        load_fisma_control_inventory(
            missing_path,
            project_root=ROOT,
            schema_path=SCHEMA_PATH,
        )


def test_load_rejects_malformed_json(tmp_path: Path) -> None:
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(FismaControlInventoryError, match="unreadable or malformed"):
        load_fisma_control_inventory(
            inventory_path,
            project_root=ROOT,
            schema_path=SCHEMA_PATH,
        )


def test_load_rejects_non_object_json(tmp_path: Path) -> None:
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text("[]", encoding="utf-8")

    with pytest.raises(FismaControlInventoryError, match="must be a JSON object"):
        load_fisma_control_inventory(
            inventory_path,
            project_root=ROOT,
            schema_path=SCHEMA_PATH,
        )


def test_load_rejects_duplicate_control_ids(tmp_path: Path) -> None:
    document = json.loads(VALID_FIXTURE.read_text(encoding="utf-8"))
    document["control_ids"] = ["AC-1", "AC-1", "AC-2"]
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(FismaControlInventoryError, match="duplicate|non-unique"):
        load_fisma_control_inventory(
            inventory_path,
            project_root=ROOT,
            schema_path=SCHEMA_PATH,
        )


def test_load_rejects_noncanonical_lowercase_control_id(tmp_path: Path) -> None:
    document = json.loads(VALID_FIXTURE.read_text(encoding="utf-8"))
    document["control_ids"] = ["ac-1", "AC-2", "IA-5"]
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(
        FismaControlInventoryError,
        match="canonical uppercase|schema validation",
    ):
        load_fisma_control_inventory(
            inventory_path,
            project_root=ROOT,
            schema_path=SCHEMA_PATH,
        )


def test_load_rejects_approved_inventory_missing_approval_fields(
    tmp_path: Path,
) -> None:
    document = json.loads(VALID_FIXTURE.read_text(encoding="utf-8"))
    document["status"] = "approved"
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(FismaControlInventoryError, match="approved_at|schema validation"):
        load_fisma_control_inventory(
            inventory_path,
            project_root=ROOT,
            schema_path=SCHEMA_PATH,
        )


def test_load_accepts_approved_inventory_with_approval_fields(
    tmp_path: Path,
) -> None:
    document = json.loads(VALID_FIXTURE.read_text(encoding="utf-8"))
    document["status"] = "approved"
    document["approved_at"] = "2026-07-14T16:00:00Z"
    document["approved_by"] = "security.officer.example@agency.gov"
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(document), encoding="utf-8")

    inventory = load_fisma_control_inventory(
        inventory_path,
        project_root=ROOT,
        schema_path=SCHEMA_PATH,
    )

    assert inventory.status == "approved"
    assert inventory.approved_at == "2026-07-14T16:00:00Z"
    assert inventory.approved_by == "security.officer.example@agency.gov"


def test_load_rejects_draft_inventory_with_approval_fields(tmp_path: Path) -> None:
    document = json.loads(VALID_FIXTURE.read_text(encoding="utf-8"))
    document["approved_at"] = "2026-07-14T16:00:00Z"
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(
        FismaControlInventoryError,
        match="approved_at to null|schema validation",
    ):
        load_fisma_control_inventory(
            inventory_path,
            project_root=ROOT,
            schema_path=SCHEMA_PATH,
        )


def test_load_rejects_privacy_family_control_ids(tmp_path: Path) -> None:
    document = json.loads(VALID_FIXTURE.read_text(encoding="utf-8"))
    document["control_ids"] = ["AC-1", "PT-1"]
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(FismaControlInventoryError, match="privacy-family control_id 'PT-1'"):
        load_fisma_control_inventory(
            inventory_path,
            project_root=ROOT,
            schema_path=SCHEMA_PATH,
        )


def test_load_rejects_null_byte_in_path(tmp_path: Path) -> None:
    document = json.loads(VALID_FIXTURE.read_text(encoding="utf-8"))
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(document), encoding="utf-8")
    malformed_path = Path(str(inventory_path) + "\0.extra")

    with pytest.raises(FismaControlInventoryError, match="path is malformed"):
        load_fisma_control_inventory(
            malformed_path,
            project_root=ROOT,
            schema_path=SCHEMA_PATH,
        )


def test_load_rejects_directory_path(tmp_path: Path) -> None:
    directory = tmp_path / "inventory.json"
    directory.mkdir()

    with pytest.raises(FismaControlInventoryError, match="regular file"):
        load_fisma_control_inventory(
            directory,
            project_root=ROOT,
            schema_path=SCHEMA_PATH,
        )


@requires_symlink
def test_load_rejects_symlink_path(tmp_path: Path) -> None:
    document = json.loads(VALID_FIXTURE.read_text(encoding="utf-8"))
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(document), encoding="utf-8")
    link = tmp_path / "linked-inventory.json"
    link.symlink_to(inventory_path)

    with pytest.raises(FismaControlInventoryError, match="must not be a symlink"):
        load_fisma_control_inventory(
            link,
            project_root=ROOT,
            schema_path=SCHEMA_PATH,
        )


def test_load_rejects_malformed_utf8(tmp_path: Path) -> None:
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_bytes(b"\xff\xfe")

    with pytest.raises(FismaControlInventoryError, match="unreadable or malformed"):
        load_fisma_control_inventory(
            inventory_path,
            project_root=ROOT,
            schema_path=SCHEMA_PATH,
        )


def test_load_rejects_authority_manifest_id_mismatch(tmp_path: Path) -> None:
    document = json.loads(VALID_FIXTURE.read_text(encoding="utf-8"))
    document["authority_manifest_id"] = "wrong-manifest-id"
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(
        FismaControlInventoryError,
        match="authority_manifest_id.*does not match authority manifest",
    ):
        load_fisma_control_inventory(
            inventory_path,
            project_root=ROOT,
            schema_path=SCHEMA_PATH,
        )


def test_load_rejects_inventory_when_authority_manifest_unavailable(
    tmp_path: Path,
) -> None:
    document = json.loads(VALID_FIXTURE.read_text(encoding="utf-8"))
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(document), encoding="utf-8")
    isolated_root = tmp_path / "isolated-project"
    isolated_root.mkdir()
    (isolated_root / "docs" / "contracts").mkdir(parents=True)

    with pytest.raises(
        FismaControlInventoryError,
        match="verified authority manifest is unavailable or invalid",
    ):
        load_fisma_control_inventory(
            inventory_path,
            project_root=isolated_root,
            schema_path=SCHEMA_PATH,
        )


def test_valid_example_inventory_matches_repository_manifest_id() -> None:
    inventory = load_fisma_control_inventory(
        VALID_FIXTURE,
        project_root=ROOT,
        schema_path=SCHEMA_PATH,
    )

    assert inventory.authority_manifest_id == MANIFEST_ID


def test_contract_fixtures_cover_valid_and_invalid_examples() -> None:
    valid_names = {
        path.name
        for path in FIXTURES_DIR.glob("fisma-control-inventory.valid.*.json")
    }
    invalid_names = {
        path.name
        for path in FIXTURES_DIR.glob("fisma-control-inventory.invalid.*.json")
    }

    assert "fisma-control-inventory.valid.example.json" in valid_names
    assert "fisma-control-inventory.invalid.approved-missing-approval-fields.json" in invalid_names
    assert "fisma-control-inventory.invalid.duplicate-control-ids.json" in invalid_names
