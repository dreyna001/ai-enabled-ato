"""Tests for ORM-to-domain JSON mapping helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from ato_service.domain_mapping import (
    format_iso_date,
    format_utc_datetime,
    format_uuid,
    map_package_revision_to_domain,
    map_source_artifact_to_domain,
    map_system_to_domain,
)

ROOT = Path(__file__).resolve().parents[2]
DOMAIN_SCHEMA_PATH = ROOT / "docs" / "contracts" / "domain.schema.json"
_FORMAT_CHECKER = FormatChecker()


@dataclass
class _SystemRow:
    system_id: UUID
    display_name: str
    external_system_id: str | None
    owner_group: str
    viewer_groups: list[str]
    created_at: datetime
    archived_at: datetime | None


@dataclass
class _PackageRevisionRow:
    package_revision_id: UUID
    system_id: UUID
    parent_revision_id: UUID | None
    profile_id: str
    certification_class: str | None
    impact_level: str | None
    data_origin: str
    sensitivity: str
    effective_data_labels: list[str]
    authority_manifest_id: str
    content_manifest_sha256: str | None
    package_content_sha256: str | None
    system_context_snapshot_id: UUID | None
    revision_version: int
    status: str
    created_by: str
    created_at: datetime


@dataclass
class _SourceArtifactRow:
    artifact_id: UUID
    package_revision_id: UUID
    display_filename: str
    storage_key: str
    sha256: str
    size_bytes: int
    declared_media_type: str
    detected_media_type: str
    artifact_kind: str
    malware_scan_status: str
    extraction_status: str
    source_date: date | None
    uploaded_at: datetime


def _validator_for(def_name: str) -> Draft202012Validator:
    schema = json.loads(DOMAIN_SCHEMA_PATH.read_text(encoding="utf-8"))
    object_schema = {
        **schema["$defs"][def_name],
        "$defs": schema["$defs"],
    }
    return Draft202012Validator(object_schema, format_checker=_FORMAT_CHECKER)


def test_format_helpers_use_exact_contract_formats() -> None:
    value = UUID("11111111-1111-4111-8111-111111111111")
    assert format_uuid(value) == "11111111-1111-4111-8111-111111111111"
    assert format_iso_date(date(2026, 7, 10)) == "2026-07-10"
    assert format_iso_date(None) is None
    assert (
        format_utc_datetime(datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc))
        == "2026-07-10T20:00:00Z"
    )
    assert (
        format_utc_datetime(
            datetime(2026, 7, 10, 20, 0, 0, 123456, tzinfo=timezone.utc)
        )
        == "2026-07-10T20:00:00.123456Z"
    )


def test_format_utc_datetime_rejects_naive_values() -> None:
    with pytest.raises(ValueError):
        format_utc_datetime(datetime(2026, 7, 10, 20, 0))


def test_map_system_to_domain_matches_schema() -> None:
    row = _SystemRow(
        system_id=UUID("22222222-2222-4222-8222-222222222222"),
        display_name="Example System",
        external_system_id="ext-1",
        owner_group="owners",
        viewer_groups=["viewers"],
        created_at=datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc),
        archived_at=None,
    )

    payload = map_system_to_domain(row)

    _validator_for("System").validate(payload)
    assert payload["object_type"] == "system"
    assert "password" not in json.dumps(payload)


def test_map_package_revision_to_domain_matches_fixture_and_schema() -> None:
    fixture = json.loads(
        (ROOT / "docs/contracts/fixtures/domain.valid.fisma-package-revision.json").read_text(
            encoding="utf-8"
        )
    )
    row = _PackageRevisionRow(
        package_revision_id=UUID(fixture["package_revision_id"]),
        system_id=UUID(fixture["system_id"]),
        parent_revision_id=None,
        profile_id=fixture["profile_id"],
        certification_class=fixture["certification_class"],
        impact_level=fixture["impact_level"],
        data_origin=fixture["data_origin"],
        sensitivity=fixture["sensitivity"],
        effective_data_labels=fixture["effective_data_labels"],
        authority_manifest_id=fixture["authority_manifest_id"],
        content_manifest_sha256=fixture["content_manifest_sha256"],
        package_content_sha256=fixture["package_content_sha256"],
        system_context_snapshot_id=None,
        revision_version=fixture["revision_version"],
        status=fixture["status"],
        created_by=fixture["created_by"],
        created_at=datetime.fromisoformat(fixture["created_at"].replace("Z", "+00:00")),
    )

    payload = map_package_revision_to_domain(row)

    assert payload == fixture
    _validator_for("PackageRevision").validate(payload)


def test_map_source_artifact_to_domain_matches_schema() -> None:
    row = _SourceArtifactRow(
        artifact_id=UUID("33333333-3333-4333-8333-333333333333"),
        package_revision_id=UUID("11111111-1111-4111-8111-111111111111"),
        display_filename="evidence.pdf",
        storage_key="aa/" + ("b" * 64),
        sha256="c" * 64,
        size_bytes=1024,
        declared_media_type="application/pdf",
        detected_media_type="application/pdf",
        artifact_kind="evidence_document",
        malware_scan_status="pending",
        extraction_status="pending",
        source_date=date(2026, 7, 9),
        uploaded_at=datetime(2026, 7, 10, 21, 0, tzinfo=timezone.utc),
    )

    payload = map_source_artifact_to_domain(row)

    _validator_for("SourceArtifact").validate(payload)
    assert payload["source_date"] == "2026-07-09"
    assert payload["uploaded_at"].endswith("Z")
    assert "secret" not in json.dumps(payload).lower()
