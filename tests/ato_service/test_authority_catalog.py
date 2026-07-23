"""Tests for authority catalog source loading and JSON pointer resolution."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

import ato_service.authority_catalog as authority_catalog
from ato_service.authority_catalog import (
    AuthorityCatalogError,
    authority_sources_by_id,
    load_json_authority_archive_member,
    load_json_authority_source,
    resolve_json_pointer,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "docs" / "contracts" / "authority-manifest.json"
FEDRAMP_AUTHORITY_ID = "fedramp-consolidated-rules-2026"
FEDRAMP_RULE_POINTER = "/FRR/AFC/data/all/CSO/AFC-CSO-INB"
NIST_AUTHORITY_ID = "nist-sp800-53-release-5.2.0"
NIST_CATALOG_MEMBER_SUFFIX = "NIST_SP-800-53_rev5_catalog-min.json"
NIST_MODERATE_PROFILE_MEMBER_SUFFIX = (
    "NIST_SP-800-53_rev5_MODERATE-baseline_profile-min.json"
)


def _source_entry(
    *,
    authority_id: str,
    local_path: str,
    content: bytes,
) -> dict[str, object]:
    return {
        "authority_id": authority_id,
        "local_path": local_path,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _build_zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for member_name, member_bytes in members.items():
            archive.writestr(member_name, member_bytes)
    return buffer.getvalue()


def _find_control(groups: list[dict], control_id: str) -> dict | None:
    for group in groups:
        for control in group.get("controls", []):
            if control.get("id", "").lower() == control_id:
                return control
        nested = group.get("groups", [])
        if nested:
            found = _find_control(nested, control_id)
            if found is not None:
                return found
    return None


@pytest.fixture
def authority_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_authority_sources_by_id_indexes_real_manifest(
    authority_manifest: dict,
) -> None:
    sources_by_id = authority_sources_by_id(authority_manifest)

    assert FEDRAMP_AUTHORITY_ID in sources_by_id
    assert (
        sources_by_id[FEDRAMP_AUTHORITY_ID]["local_path"]
        == "reference/authorities/fedramp/fedramp-consolidated-rules-2026.json"
    )
    assert len(sources_by_id) == len(authority_manifest["sources"])


def test_authority_sources_by_id_rejects_duplicate_ids() -> None:
    manifest = {
        "sources": [
            {"authority_id": "alpha.example"},
            {"authority_id": "alpha.example"},
        ]
    }

    with pytest.raises(AuthorityCatalogError, match="duplicate authority_id"):
        authority_sources_by_id(manifest)


def test_authority_sources_by_id_rejects_malformed_source_list() -> None:
    with pytest.raises(AuthorityCatalogError, match="sources list"):
        authority_sources_by_id({"sources": "not-a-list"})

    with pytest.raises(AuthorityCatalogError, match="must be an object"):
        authority_sources_by_id({"sources": ["not-an-object"]})

    with pytest.raises(AuthorityCatalogError, match="must declare authority_id"):
        authority_sources_by_id({"sources": [{}]})

    with pytest.raises(AuthorityCatalogError, match="malformed authority_id"):
        authority_sources_by_id({"sources": [{"authority_id": "BAD ID"}]})


def test_load_json_authority_source_loads_real_fedramp_source(
    authority_manifest: dict,
) -> None:
    document = load_json_authority_source(
        manifest=authority_manifest,
        authority_id=FEDRAMP_AUTHORITY_ID,
        project_root=ROOT,
    )

    assert document["info"]["title"] == "FedRAMP Consolidated Rules for 2026"


def test_resolve_json_pointer_loads_real_fedramp_rule(
    authority_manifest: dict,
) -> None:
    document = load_json_authority_source(
        manifest=authority_manifest,
        authority_id=FEDRAMP_AUTHORITY_ID,
        project_root=ROOT,
    )
    rule = resolve_json_pointer(document, FEDRAMP_RULE_POINTER)

    assert rule["name"] == "Maintain a FedRAMP Security Inbox"
    assert rule["force"] == "MUST"
    assert "FedRAMP Security Inbox" in rule["statement"]


def test_resolve_json_pointer_empty_pointer_returns_root() -> None:
    document = {"alpha": 1, "items": [10, 20]}

    assert resolve_json_pointer(document, "") is document


def test_resolve_json_pointer_supports_object_and_array_tokens() -> None:
    document = {
        "items": [
            {"name": "first"},
            {"labels": ["a", "b"]},
        ],
        "meta~key": {"slash/key": "value"},
    }

    assert resolve_json_pointer(document, "/items/0/name") == "first"
    assert resolve_json_pointer(document, "/items/1/labels/1") == "b"
    assert resolve_json_pointer(document, "/meta~0key/slash~1key") == "value"


def test_resolve_json_pointer_rejects_malformed_pointer() -> None:
    document = {"alpha": 1}

    with pytest.raises(AuthorityCatalogError, match="malformed JSON pointer"):
        resolve_json_pointer(document, "alpha")

    with pytest.raises(AuthorityCatalogError, match="malformed JSON pointer"):
        resolve_json_pointer(document, "/a~2b")

    with pytest.raises(AuthorityCatalogError, match="malformed JSON pointer"):
        resolve_json_pointer(document, "/trailing~")


def test_resolve_json_pointer_rejects_missing_key_and_array_errors() -> None:
    document = {"items": [{"name": "first"}]}

    with pytest.raises(AuthorityCatalogError, match="missing object key"):
        resolve_json_pointer(document, "/missing")

    with pytest.raises(AuthorityCatalogError, match="out of range"):
        resolve_json_pointer(document, "/items/3")

    with pytest.raises(AuthorityCatalogError, match="cannot use '-'"):
        resolve_json_pointer(document, "/items/-")

    with pytest.raises(AuthorityCatalogError, match="invalid array index"):
        resolve_json_pointer(document, "/items/foo")

    with pytest.raises(AuthorityCatalogError, match="traverses a non-container"):
        resolve_json_pointer(document, "/items/0/name/extra")


def test_load_json_authority_source_rejects_unknown_authority_id(
    authority_manifest: dict,
) -> None:
    with pytest.raises(AuthorityCatalogError, match="unknown authority_id"):
        load_json_authority_source(
            manifest=authority_manifest,
            authority_id="missing.authority",
            project_root=ROOT,
        )


def test_load_json_authority_source_rejects_path_traversal(
    tmp_path: Path,
) -> None:
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "secret.json"
    outside_file.write_text('{"leaked": true}', encoding="utf-8")

    project_root = tmp_path / "project"
    reference_dir = project_root / "reference" / "authorities" / "fixture"
    reference_dir.mkdir(parents=True)
    safe_file = reference_dir / "authority.json"
    safe_file.write_text('{"ok": true}', encoding="utf-8")

    manifest = {
        "sources": [
            {
                "authority_id": "fixture.authority",
                "local_path": "../outside/secret.json",
            }
        ]
    }

    with pytest.raises(AuthorityCatalogError, match="escapes project root"):
        load_json_authority_source(
            manifest=manifest,
            authority_id="fixture.authority",
            project_root=project_root,
        )


def test_load_json_authority_source_rejects_non_json_object(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    reference_dir = project_root / "reference" / "authorities" / "fixture"
    reference_dir.mkdir(parents=True)

    array_path = reference_dir / "array.json"
    array_bytes = b"[1, 2, 3]"
    array_path.write_bytes(array_bytes)
    array_manifest = {
        "sources": [
            _source_entry(
                authority_id="fixture.array",
                local_path="reference/authorities/fixture/array.json",
                content=array_bytes,
            )
        ]
    }
    with pytest.raises(AuthorityCatalogError, match="must be a JSON object"):
        load_json_authority_source(
            manifest=array_manifest,
            authority_id="fixture.array",
            project_root=project_root,
        )

    zip_path = reference_dir / "archive.zip"
    zip_bytes = b"not-json"
    zip_path.write_bytes(zip_bytes)
    zip_manifest = {
        "sources": [
            _source_entry(
                authority_id="fixture.zip",
                local_path="reference/authorities/fixture/archive.zip",
                content=zip_bytes,
            )
        ]
    }
    with pytest.raises(AuthorityCatalogError, match="must reference a .json file"):
        load_json_authority_source(
            manifest=zip_manifest,
            authority_id="fixture.zip",
            project_root=project_root,
        )


def test_load_json_authority_source_rechecks_manifest_digest(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    artifact_dir = project_root / "reference" / "authorities" / "fixture"
    artifact_dir.mkdir(parents=True)
    artifact_path = artifact_dir / "authority.json"
    original_bytes = b'{"value":"original"}'
    artifact_path.write_bytes(original_bytes)
    manifest = {
        "sources": [
            _source_entry(
                authority_id="fixture.authority",
                local_path="reference/authorities/fixture/authority.json",
                content=original_bytes,
            )
        ]
    }
    artifact_path.write_bytes(b'{"value":"tampered"}')

    with pytest.raises(AuthorityCatalogError, match="sha256 does not match"):
        load_json_authority_source(
            manifest=manifest,
            authority_id="fixture.authority",
            project_root=project_root,
        )


def test_load_json_authority_archive_member_loads_zip_member(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    artifact_dir = project_root / "reference" / "authorities" / "fixture"
    artifact_dir.mkdir(parents=True)
    zip_path = artifact_dir / "authority.zip"
    member_name = "fixture/pkg/authority-min.json"
    member_bytes = b'{"catalog":{"id":"fixture-catalog"}}'
    zip_bytes = _build_zip_bytes({member_name: member_bytes})
    zip_path.write_bytes(zip_bytes)
    manifest = {
        "sources": [
            _source_entry(
                authority_id="fixture.archive",
                local_path="reference/authorities/fixture/authority.zip",
                content=zip_bytes,
            )
        ]
    }

    canonical_name, document = load_json_authority_archive_member(
        manifest=manifest,
        authority_id="fixture.archive",
        project_root=project_root,
        member_suffix="authority-min.json",
    )

    assert canonical_name == member_name
    assert document["catalog"]["id"] == "fixture-catalog"


def test_load_json_authority_archive_member_rechecks_manifest_digest(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    artifact_dir = project_root / "reference" / "authorities" / "fixture"
    artifact_dir.mkdir(parents=True)
    zip_path = artifact_dir / "authority.zip"
    original_zip = _build_zip_bytes(
        {"fixture/authority-min.json": b'{"catalog":{"id":"original"}}'}
    )
    zip_path.write_bytes(original_zip)
    manifest = {
        "sources": [
            _source_entry(
                authority_id="fixture.archive",
                local_path="reference/authorities/fixture/authority.zip",
                content=original_zip,
            )
        ]
    }
    zip_path.write_bytes(
        _build_zip_bytes({"fixture/authority-min.json": b'{"catalog":{"id":"tampered"}}'})
    )

    with pytest.raises(AuthorityCatalogError, match="sha256 does not match"):
        load_json_authority_archive_member(
            manifest=manifest,
            authority_id="fixture.archive",
            project_root=project_root,
            member_suffix="authority-min.json",
        )


def test_load_json_authority_archive_member_rejects_missing_member(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    artifact_dir = project_root / "reference" / "authorities" / "fixture"
    artifact_dir.mkdir(parents=True)
    zip_path = artifact_dir / "authority.zip"
    zip_bytes = _build_zip_bytes({"fixture/other.json": b'{"ok": true}'})
    zip_path.write_bytes(zip_bytes)
    manifest = {
        "sources": [
            _source_entry(
                authority_id="fixture.archive",
                local_path="reference/authorities/fixture/authority.zip",
                content=zip_bytes,
            )
        ]
    }

    with pytest.raises(AuthorityCatalogError, match="no archive member ending with"):
        load_json_authority_archive_member(
            manifest=manifest,
            authority_id="fixture.archive",
            project_root=project_root,
            member_suffix="missing.json",
        )


def test_load_json_authority_archive_member_rejects_duplicate_suffix_match(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    artifact_dir = project_root / "reference" / "authorities" / "fixture"
    artifact_dir.mkdir(parents=True)
    zip_path = artifact_dir / "authority.zip"
    zip_bytes = _build_zip_bytes(
        {
            "fixture/a/data.json": b'{"first": true}',
            "fixture/b/data.json": b'{"second": true}',
        }
    )
    zip_path.write_bytes(zip_bytes)
    manifest = {
        "sources": [
            _source_entry(
                authority_id="fixture.archive",
                local_path="reference/authorities/fixture/authority.zip",
                content=zip_bytes,
            )
        ]
    }

    with pytest.raises(AuthorityCatalogError, match="ambiguous archive member suffix"):
        load_json_authority_archive_member(
            manifest=manifest,
            authority_id="fixture.archive",
            project_root=project_root,
            member_suffix="data.json",
        )


def test_load_json_authority_archive_member_rejects_traversal_member(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    artifact_dir = project_root / "reference" / "authorities" / "fixture"
    artifact_dir.mkdir(parents=True)
    zip_path = artifact_dir / "authority.zip"
    zip_bytes = _build_zip_bytes(
        {"../escape/data.json": b'{"leaked": true}'}
    )
    zip_path.write_bytes(zip_bytes)
    manifest = {
        "sources": [
            _source_entry(
                authority_id="fixture.archive",
                local_path="reference/authorities/fixture/authority.zip",
                content=zip_bytes,
            )
        ]
    }

    with pytest.raises(AuthorityCatalogError, match="must not contain parent segments"):
        load_json_authority_archive_member(
            manifest=manifest,
            authority_id="fixture.archive",
            project_root=project_root,
            member_suffix="data.json",
        )


def test_load_json_authority_archive_member_rejects_oversized_member(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        authority_catalog,
        "_MAX_ARCHIVE_MEMBER_UNCOMPRESSED_BYTES",
        16,
    )
    project_root = tmp_path / "project"
    artifact_dir = project_root / "reference" / "authorities" / "fixture"
    artifact_dir.mkdir(parents=True)
    zip_path = artifact_dir / "authority.zip"
    zip_bytes = _build_zip_bytes(
        {"fixture/large.json": b'{"value":"012345678901234567890"}'}
    )
    zip_path.write_bytes(zip_bytes)
    manifest = {
        "sources": [
            _source_entry(
                authority_id="fixture.archive",
                local_path="reference/authorities/fixture/authority.zip",
                content=zip_bytes,
            )
        ]
    }

    with pytest.raises(AuthorityCatalogError, match="exceeds size limit"):
        load_json_authority_archive_member(
            manifest=manifest,
            authority_id="fixture.archive",
            project_root=project_root,
            member_suffix="large.json",
        )


def test_load_json_authority_archive_member_rejects_non_object_json(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    artifact_dir = project_root / "reference" / "authorities" / "fixture"
    artifact_dir.mkdir(parents=True)
    zip_path = artifact_dir / "authority.zip"
    zip_bytes = _build_zip_bytes({"fixture/array.json": b"[1, 2, 3]"})
    zip_path.write_bytes(zip_bytes)
    manifest = {
        "sources": [
            _source_entry(
                authority_id="fixture.archive",
                local_path="reference/authorities/fixture/authority.zip",
                content=zip_bytes,
            )
        ]
    }

    with pytest.raises(AuthorityCatalogError, match="must be a JSON object"):
        load_json_authority_archive_member(
            manifest=manifest,
            authority_id="fixture.archive",
            project_root=project_root,
            member_suffix="array.json",
        )


def test_load_json_authority_archive_member_rejects_invalid_json(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    artifact_dir = project_root / "reference" / "authorities" / "fixture"
    artifact_dir.mkdir(parents=True)
    zip_path = artifact_dir / "authority.zip"
    zip_bytes = _build_zip_bytes({"fixture/broken.json": b"{not-json"})
    zip_path.write_bytes(zip_bytes)
    manifest = {
        "sources": [
            _source_entry(
                authority_id="fixture.archive",
                local_path="reference/authorities/fixture/authority.zip",
                content=zip_bytes,
            )
        ]
    }

    with pytest.raises(AuthorityCatalogError, match="is not valid JSON"):
        load_json_authority_archive_member(
            manifest=manifest,
            authority_id="fixture.archive",
            project_root=project_root,
            member_suffix="broken.json",
        )


def test_load_json_authority_archive_member_rejects_unsafe_member_suffix() -> None:
    manifest = {"sources": []}

    with pytest.raises(AuthorityCatalogError, match="member_suffix is required"):
        load_json_authority_archive_member(
            manifest=manifest,
            authority_id="fixture.archive",
            project_root=Path("."),
            member_suffix="",
        )

    with pytest.raises(AuthorityCatalogError, match="must not be absolute"):
        load_json_authority_archive_member(
            manifest=manifest,
            authority_id="fixture.archive",
            project_root=Path("."),
            member_suffix="/fixture/data.json",
        )

    with pytest.raises(AuthorityCatalogError, match="must use forward slashes"):
        load_json_authority_archive_member(
            manifest=manifest,
            authority_id="fixture.archive",
            project_root=Path("."),
            member_suffix="fixture\\data.json",
        )

    with pytest.raises(AuthorityCatalogError, match="must not contain parent segments"):
        load_json_authority_archive_member(
            manifest=manifest,
            authority_id="fixture.archive",
            project_root=Path("."),
            member_suffix="fixture/../data.json",
        )


def test_load_json_authority_archive_member_loads_real_nist_catalog(
    authority_manifest: dict,
) -> None:
    member_name, document = load_json_authority_archive_member(
        manifest=authority_manifest,
        authority_id=NIST_AUTHORITY_ID,
        project_root=ROOT,
        member_suffix=NIST_CATALOG_MEMBER_SUFFIX,
    )

    assert member_name.endswith(NIST_CATALOG_MEMBER_SUFFIX)
    assert set(document.keys()) == {"catalog"}
    catalog = document["catalog"]
    assert set(catalog.keys()) >= {"uuid", "metadata", "groups", "back-matter"}
    assert "800-53" in catalog["metadata"]["title"]
    ac1 = _find_control(catalog["groups"], "ac-1")
    assert ac1 is not None
    assert ac1["id"] == "ac-1"


def test_load_json_authority_archive_member_loads_real_nist_moderate_profile(
    authority_manifest: dict,
) -> None:
    member_name, document = load_json_authority_archive_member(
        manifest=authority_manifest,
        authority_id=NIST_AUTHORITY_ID,
        project_root=ROOT,
        member_suffix=NIST_MODERATE_PROFILE_MEMBER_SUFFIX,
    )

    assert member_name.endswith(NIST_MODERATE_PROFILE_MEMBER_SUFFIX)
    assert set(document.keys()) == {"profile"}
    profile = document["profile"]
    assert set(profile.keys()) >= {"uuid", "metadata", "imports", "merge", "back-matter"}
    assert "MODERATE IMPACT BASELINE" in profile["metadata"]["title"]
    assert profile["imports"][0]["href"].startswith("#")
