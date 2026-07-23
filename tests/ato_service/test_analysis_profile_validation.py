"""Tests for analysis profile semantic authority validation."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ato_service.analysis_profile_validation import (
    AnalysisProfileSemanticError,
    validate_analysis_profile_semantics,
)

MANIFEST_ID = "fixture.semantic"
AUTHORITY_ID = "fixture-authority"
ARCHIVE_AUTHORITY_ID = "fixture.archive"
ANALYSIS_PROFILE_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "contracts"
    / "analysis-profile.schema.json"
)


def _write_fixture_project(
    tmp_path: Path,
    *,
    manifest_id: str = MANIFEST_ID,
    authority_id: str = AUTHORITY_ID,
    artifact_bytes: bytes | None = None,
) -> tuple[Path, Path]:
    authority_document = {
        "requirements": {
            "FR-1": {"title": "Representative provider requirement"},
        },
        "artifacts": {
            "cpo": {"display_name": "Cloud Provider Overview"},
        },
    }
    resolved_bytes = (
        artifact_bytes
        if artifact_bytes is not None
        else json.dumps(authority_document, sort_keys=True).encode("utf-8")
    )
    artifact_dir = tmp_path / "reference" / "authorities" / "fixture"
    artifact_dir.mkdir(parents=True)
    artifact_path = artifact_dir / "authority.json"
    artifact_path.write_bytes(resolved_bytes)
    manifest = {
        "schema_version": "1.0.0",
        "manifest_id": manifest_id,
        "status": "draft",
        "created_at": "2026-07-10T22:33:12Z",
        "approved_at": None,
        "approved_by": None,
        "sources": [
            {
                "authority_id": authority_id,
                "title": "Fixture Authority",
                "source_url": "https://example.test/authority.json",
                "source_version_or_date": "2026-07-10",
                "retrieved_at_utc": "2026-07-10T22:33:12Z",
                "effective_date": "2026-07-10",
                "sha256": hashlib.sha256(resolved_bytes).hexdigest(),
                "size_bytes": len(resolved_bytes),
                "local_path": "reference/authorities/fixture/authority.json",
                "review_status": "pending",
            }
        ],
    }
    manifest_path = tmp_path / "authority-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, artifact_path


def _build_zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for member_name, member_bytes in members.items():
            archive.writestr(member_name, member_bytes)
    return buffer.getvalue()


def _write_zip_fixture_project(
    tmp_path: Path,
    *,
    manifest_id: str = MANIFEST_ID,
    authority_id: str = ARCHIVE_AUTHORITY_ID,
    member_name: str = "fixture/pkg/authority-min.json",
    member_document: dict[str, object] | None = None,
) -> tuple[Path, bytes]:
    resolved_document = member_document or {
        "requirements": {
            "FR-1": {"title": "Representative archive requirement"},
        },
        "artifacts": {
            "cpo": {"display_name": "Cloud Provider Overview"},
        },
    }
    member_bytes = json.dumps(resolved_document, sort_keys=True).encode("utf-8")
    zip_bytes = _build_zip_bytes({member_name: member_bytes})
    artifact_dir = tmp_path / "reference" / "authorities" / "fixture"
    artifact_dir.mkdir(parents=True)
    zip_path = artifact_dir / "authority.zip"
    zip_path.write_bytes(zip_bytes)
    manifest = {
        "schema_version": "1.0.0",
        "manifest_id": manifest_id,
        "status": "draft",
        "created_at": "2026-07-10T22:33:12Z",
        "approved_at": None,
        "approved_by": None,
        "sources": [
            {
                "authority_id": authority_id,
                "title": "Fixture Archive Authority",
                "source_url": "https://example.test/authority.zip",
                "source_version_or_date": "2026-07-10",
                "retrieved_at_utc": "2026-07-10T22:33:12Z",
                "effective_date": "2026-07-10",
                "sha256": hashlib.sha256(zip_bytes).hexdigest(),
                "size_bytes": len(zip_bytes),
                "local_path": "reference/authorities/fixture/authority.zip",
                "review_status": "pending",
            }
        ],
    }
    manifest_path = tmp_path / "authority-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, zip_bytes


def _archive_minimal_profile(
    *,
    manifest_id: str = MANIFEST_ID,
    authority_id: str = ARCHIVE_AUTHORITY_ID,
    archive_member: str = "authority-min.json",
    source_pointer: str = "/requirements/FR-1",
    assessment_item_id: str = "FR-1",
) -> dict[str, object]:
    return {
        "authority_manifest_id": manifest_id,
        "assessment_items": [
            {
                "assessment_item_id": assessment_item_id,
                "authority_refs": [
                    {
                        "authority_id": authority_id,
                        "archive_member": archive_member,
                        "source_pointer": source_pointer,
                    }
                ],
            }
        ],
        "artifact_requirements": [
            {
                "artifact_id": "cpo",
                "official_schema_authority_id": None,
                "authority_refs": [],
            }
        ],
        "cadence_rules": [],
    }


def _authority_ref_schema_validator() -> Draft202012Validator:
    schema = json.loads(ANALYSIS_PROFILE_SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema["$defs"]["authorityRef"])


def _validate_authority_ref_schema(ref: dict[str, object]) -> None:
    _authority_ref_schema_validator().validate(ref)


def _minimal_profile(
    *,
    manifest_id: str = MANIFEST_ID,
    authority_id: str = AUTHORITY_ID,
    assessment_item_id: str = "FR-1",
    artifact_id: str = "cpo",
    official_schema_authority_id: str | None = AUTHORITY_ID,
) -> dict[str, object]:
    return {
        "authority_manifest_id": manifest_id,
        "assessment_items": [
            {
                "assessment_item_id": assessment_item_id,
                "authority_refs": [
                    {
                        "authority_id": authority_id,
                        "source_pointer": "/requirements/FR-1",
                    }
                ],
            }
        ],
        "artifact_requirements": [
            {
                "artifact_id": artifact_id,
                "official_schema_authority_id": official_schema_authority_id,
                "authority_refs": [
                    {
                        "authority_id": authority_id,
                        "source_pointer": "/artifacts/cpo",
                    }
                ],
            }
        ],
        "cadence_rules": [],
    }


def test_validate_analysis_profile_semantics_success(tmp_path: Path) -> None:
    manifest_path, _artifact_path = _write_fixture_project(tmp_path)
    profile = _minimal_profile()

    assert (
        validate_analysis_profile_semantics(
            profile,
            manifest_path=manifest_path,
            project_root=tmp_path,
        )
        is None
    )


def test_validate_analysis_profile_semantics_manifest_id_mismatch(
    tmp_path: Path,
) -> None:
    manifest_path, _artifact_path = _write_fixture_project(tmp_path)
    profile = _minimal_profile(manifest_id="wrong.manifest")

    with pytest.raises(
        AnalysisProfileSemanticError,
        match="authority_manifest_id 'wrong.manifest' does not match",
    ):
        validate_analysis_profile_semantics(
            profile,
            manifest_path=manifest_path,
            project_root=tmp_path,
        )


def test_validate_analysis_profile_semantics_duplicate_assessment_item_ids(
    tmp_path: Path,
) -> None:
    manifest_path, _artifact_path = _write_fixture_project(tmp_path)
    profile = _minimal_profile()
    profile["assessment_items"] = [
        {
            "assessment_item_id": "FR-1",
            "authority_refs": [
                {
                    "authority_id": AUTHORITY_ID,
                    "source_pointer": "/requirements/FR-1",
                }
            ],
        },
        {
            "assessment_item_id": "FR-1",
            "authority_refs": [
                {
                    "authority_id": AUTHORITY_ID,
                    "source_pointer": "/requirements/FR-1",
                }
            ],
        },
    ]

    with pytest.raises(
        AnalysisProfileSemanticError,
        match="duplicate assessment_item_id 'FR-1'",
    ):
        validate_analysis_profile_semantics(
            profile,
            manifest_path=manifest_path,
            project_root=tmp_path,
        )


def test_validate_analysis_profile_semantics_unknown_authority_id(
    tmp_path: Path,
) -> None:
    manifest_path, _artifact_path = _write_fixture_project(tmp_path)
    profile = _minimal_profile(authority_id="missing.authority")

    with pytest.raises(
        AnalysisProfileSemanticError,
        match="references unknown authority_id 'missing.authority'",
    ):
        validate_analysis_profile_semantics(
            profile,
            manifest_path=manifest_path,
            project_root=tmp_path,
        )


def test_validate_analysis_profile_semantics_unresolved_pointer(
    tmp_path: Path,
) -> None:
    manifest_path, _artifact_path = _write_fixture_project(tmp_path)
    profile = _minimal_profile()
    profile["assessment_items"][0]["authority_refs"][0]["source_pointer"] = (
        "/requirements/missing"
    )

    with pytest.raises(
        AnalysisProfileSemanticError,
        match=r"authority_ref 'fixture-authority' '/requirements/missing'",
    ):
        validate_analysis_profile_semantics(
            profile,
            manifest_path=manifest_path,
            project_root=tmp_path,
        )


def test_validate_analysis_profile_semantics_unknown_official_schema_authority_id(
    tmp_path: Path,
) -> None:
    manifest_path, _artifact_path = _write_fixture_project(tmp_path)
    profile = _minimal_profile(official_schema_authority_id="missing.schema")

    with pytest.raises(
        AnalysisProfileSemanticError,
        match="unknown official_schema_authority_id 'missing.schema'",
    ):
        validate_analysis_profile_semantics(
            profile,
            manifest_path=manifest_path,
            project_root=tmp_path,
        )


@pytest.mark.parametrize(
    ("profile_mutator", "match"),
    [
        (
            lambda profile: profile.__setitem__("assessment_items", "not-a-list"),
            "assessment_items must be a list",
        ),
        (
            lambda profile: profile["assessment_items"].__setitem__(
                0,
                {
                    "assessment_item_id": "FR-1",
                    "authority_refs": "not-a-list",
                },
            ),
            "authority_refs must be a list",
        ),
        (
            lambda profile: profile["assessment_items"][0].__setitem__(
                "authority_refs",
                [{"source_pointer": "/requirements/FR-1"}],
            ),
            "must declare authority_id",
        ),
        (
            lambda profile: profile["assessment_items"][0].__setitem__(
                "authority_refs",
                [{"authority_id": AUTHORITY_ID}],
            ),
            "must declare source_pointer",
        ),
    ],
)
def test_validate_analysis_profile_semantics_malformed_authority_refs(
    tmp_path: Path,
    profile_mutator,
    match: str,
) -> None:
    manifest_path, _artifact_path = _write_fixture_project(tmp_path)
    profile = _minimal_profile()
    profile_mutator(profile)

    with pytest.raises(AnalysisProfileSemanticError, match=match):
        validate_analysis_profile_semantics(
            profile,
            manifest_path=manifest_path,
            project_root=tmp_path,
        )


def test_validate_analysis_profile_semantics_tampered_authority_bytes_fail_first(
    tmp_path: Path,
) -> None:
    manifest_path, artifact_path = _write_fixture_project(tmp_path)
    artifact_path.write_bytes(b"x" * artifact_path.stat().st_size)
    profile = _minimal_profile()

    with pytest.raises(
        AnalysisProfileSemanticError,
        match="sha256 does not match local artifact",
    ):
        validate_analysis_profile_semantics(
            profile,
            manifest_path=manifest_path,
            project_root=tmp_path,
        )


def test_validate_analysis_profile_semantics_archive_member_success(
    tmp_path: Path,
) -> None:
    manifest_path, _zip_bytes = _write_zip_fixture_project(tmp_path)
    profile = _archive_minimal_profile()

    assert (
        validate_analysis_profile_semantics(
            profile,
            manifest_path=manifest_path,
            project_root=tmp_path,
        )
        is None
    )


def test_validate_analysis_profile_semantics_archive_member_unresolved_pointer(
    tmp_path: Path,
) -> None:
    manifest_path, _zip_bytes = _write_zip_fixture_project(tmp_path)
    profile = _archive_minimal_profile(source_pointer="/requirements/missing")

    with pytest.raises(
        AnalysisProfileSemanticError,
        match=(
            r"authority_ref 'fixture\.archive' archive_member 'authority-min\.json' "
            r"'/requirements/missing'"
        ),
    ):
        validate_analysis_profile_semantics(
            profile,
            manifest_path=manifest_path,
            project_root=tmp_path,
        )


def test_validate_analysis_profile_semantics_archive_member_missing_member(
    tmp_path: Path,
) -> None:
    manifest_path, _zip_bytes = _write_zip_fixture_project(tmp_path)
    profile = _archive_minimal_profile(archive_member="missing.json")

    with pytest.raises(
        AnalysisProfileSemanticError,
        match=(
            r"authority_ref 'fixture\.archive' archive_member 'missing\.json': "
            r"no archive member ending with"
        ),
    ):
        validate_analysis_profile_semantics(
            profile,
            manifest_path=manifest_path,
            project_root=tmp_path,
        )


def test_validate_analysis_profile_semantics_archive_member_ambiguous_member(
    tmp_path: Path,
) -> None:
    member_document = {
        "requirements": {"FR-1": {"title": "One"}},
    }
    member_bytes = json.dumps(member_document, sort_keys=True).encode("utf-8")
    zip_bytes = _build_zip_bytes(
        {
            "fixture/a/authority-min.json": member_bytes,
            "fixture/b/authority-min.json": member_bytes,
        }
    )
    artifact_dir = tmp_path / "reference" / "authorities" / "fixture"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "authority.zip").write_bytes(zip_bytes)
    manifest = {
        "schema_version": "1.0.0",
        "manifest_id": MANIFEST_ID,
        "status": "draft",
        "created_at": "2026-07-10T22:33:12Z",
        "approved_at": None,
        "approved_by": None,
        "sources": [
            {
                "authority_id": ARCHIVE_AUTHORITY_ID,
                "title": "Fixture Archive Authority",
                "source_url": "https://example.test/authority.zip",
                "source_version_or_date": "2026-07-10",
                "retrieved_at_utc": "2026-07-10T22:33:12Z",
                "effective_date": "2026-07-10",
                "sha256": hashlib.sha256(zip_bytes).hexdigest(),
                "size_bytes": len(zip_bytes),
                "local_path": "reference/authorities/fixture/authority.zip",
                "review_status": "pending",
            }
        ],
    }
    manifest_path = tmp_path / "authority-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    profile = _archive_minimal_profile()

    with pytest.raises(
        AnalysisProfileSemanticError,
        match=(
            r"authority_ref 'fixture\.archive' archive_member 'authority-min\.json': "
            r"ambiguous archive member suffix"
        ),
    ):
        validate_analysis_profile_semantics(
            profile,
            manifest_path=manifest_path,
            project_root=tmp_path,
        )


@pytest.mark.parametrize(
    "archive_member",
    [
        "../authority-min.json",
        "fixture/../authority-min.json",
        "/authority-min.json",
        "fixture\\authority-min.json",
        "C:authority-min.json",
        "",
    ],
)
def test_validate_analysis_profile_semantics_archive_member_unsafe_suffix(
    tmp_path: Path,
    archive_member: str,
) -> None:
    manifest_path, _zip_bytes = _write_zip_fixture_project(tmp_path)
    profile = _archive_minimal_profile(archive_member=archive_member)

    with pytest.raises(AnalysisProfileSemanticError):
        validate_analysis_profile_semantics(
            profile,
            manifest_path=manifest_path,
            project_root=tmp_path,
        )


def test_validate_analysis_profile_semantics_zip_authority_without_archive_member_fails(
    tmp_path: Path,
) -> None:
    manifest_path, _zip_bytes = _write_zip_fixture_project(tmp_path)
    profile = _minimal_profile(authority_id=ARCHIVE_AUTHORITY_ID)

    with pytest.raises(
        AnalysisProfileSemanticError,
        match=r"local_path must reference a \.json file",
    ):
        validate_analysis_profile_semantics(
            profile,
            manifest_path=manifest_path,
            project_root=tmp_path,
        )


def test_validate_analysis_profile_semantics_direct_json_regression_without_archive_member(
    tmp_path: Path,
) -> None:
    manifest_path, _artifact_path = _write_fixture_project(tmp_path)
    profile = _minimal_profile()
    assert "archive_member" not in profile["assessment_items"][0]["authority_refs"][0]

    assert (
        validate_analysis_profile_semantics(
            profile,
            manifest_path=manifest_path,
            project_root=tmp_path,
        )
        is None
    )


@pytest.mark.parametrize(
    ("ref", "should_validate"),
    [
        (
            {
                "authority_id": AUTHORITY_ID,
                "source_pointer": "/requirements/FR-1",
            },
            True,
        ),
        (
            {
                "authority_id": AUTHORITY_ID,
                "source_pointer": "/requirements/FR-1",
                "archive_member": "pkg/authority-min.json",
            },
            True,
        ),
        (
            {
                "authority_id": AUTHORITY_ID,
                "source_pointer": "/requirements/FR-1",
                "archive_member": "../authority-min.json",
            },
            False,
        ),
        (
            {
                "authority_id": AUTHORITY_ID,
                "source_pointer": "/requirements/FR-1",
                "archive_member": "",
            },
            False,
        ),
        (
            {
                "authority_id": AUTHORITY_ID,
                "source_pointer": "/requirements/FR-1",
                "archive_member": "fixture\\authority-min.json",
            },
            False,
        ),
        (
            {
                "authority_id": AUTHORITY_ID,
                "source_pointer": "/requirements/FR-1",
                "extra": "field",
            },
            False,
        ),
    ],
)
def test_authority_ref_schema_archive_member_acceptance(
    ref: dict[str, object],
    should_validate: bool,
) -> None:
    if should_validate:
        _validate_authority_ref_schema(ref)
    else:
        with pytest.raises(Exception):
            _validate_authority_ref_schema(ref)


def test_validate_analysis_profile_semantics_empty_archive_member_rejected(
    tmp_path: Path,
) -> None:
    manifest_path, _zip_bytes = _write_zip_fixture_project(tmp_path)
    profile = _archive_minimal_profile(archive_member="")

    with pytest.raises(
        AnalysisProfileSemanticError,
        match="archive_member must be a non-empty string",
    ):
        validate_analysis_profile_semantics(
            profile,
            manifest_path=manifest_path,
            project_root=tmp_path,
        )
