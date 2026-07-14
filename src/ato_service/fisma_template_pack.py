"""Digest-verified agency FISMA template pack loading and validation."""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from functools import cache
from io import BytesIO
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class FismaTemplatePackError(ValueError):
    """Raised when a template pack cannot be loaded or validated safely."""

    def __init__(self, message: str, *, error_code: str = "template_pack_invalid") -> None:
        self.error_code = error_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class FismaTemplatePackReference:
    path: Path
    expected_sha256: str


@dataclass(frozen=True, slots=True)
class LoadedFismaTemplatePack:
    pack_id: str
    pack_version: str
    approval_status: str
    archive_sha256: str
    manifest: dict[str, Any]
    members: dict[str, bytes]


def load_template_pack_reference(
    document: dict[str, Any] | None,
) -> FismaTemplatePackReference | None:
    """Return a configured template pack reference from runtime JSON, if present."""
    if document is None:
        return None
    reference = document.get("FISMA_TEMPLATE_PACK_FILE_REFERENCE")
    if reference is None:
        return None
    if not isinstance(reference, dict):
        raise FismaTemplatePackError("FISMA_TEMPLATE_PACK_FILE_REFERENCE must be an object")
    path_raw = reference.get("path")
    digest_raw = reference.get("expected_sha256")
    if not isinstance(path_raw, str) or not path_raw.strip():
        raise FismaTemplatePackError("template pack path is required")
    if not isinstance(digest_raw, str) or len(digest_raw) != 64:
        raise FismaTemplatePackError("template pack expected_sha256 must be a 64-character hex digest")
    return FismaTemplatePackReference(
        path=Path(path_raw.strip()),
        expected_sha256=digest_raw.lower(),
    )


def load_verified_template_pack(
    reference: FismaTemplatePackReference,
) -> LoadedFismaTemplatePack:
    """Load and validate one digest-verified template pack archive."""
    if not reference.path.is_file():
        raise FismaTemplatePackError(f"template pack file not found: {reference.path}")
    archive_bytes = reference.path.read_bytes()
    actual_digest = hashlib.sha256(archive_bytes).hexdigest()
    if actual_digest != reference.expected_sha256:
        raise FismaTemplatePackError("template pack digest mismatch")

    members = _read_zip_members(archive_bytes)
    manifest_bytes = members.get("pack-manifest.json")
    if manifest_bytes is None:
        raise FismaTemplatePackError("template pack is missing pack-manifest.json")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FismaTemplatePackError("template pack manifest is not valid JSON") from exc

    _validate_manifest(manifest)
    _validate_required_members(manifest=manifest, members=members)
    _validate_mappings_and_schemas(manifest=manifest, members=members)

    return LoadedFismaTemplatePack(
        pack_id=str(manifest["pack_id"]),
        pack_version=str(manifest["pack_version"]),
        approval_status=str(manifest["approval_status"]),
        archive_sha256=actual_digest,
        manifest=manifest,
        members=members,
    )


def is_template_pack_rendering_eligible(pack: LoadedFismaTemplatePack | None) -> bool:
    """Return whether an approved pack is available for deterministic rendering."""
    return pack is not None and pack.approval_status == "approved"


def render_human_template(
    *,
    pack: LoadedFismaTemplatePack,
    artifact_id: str,
    field_values: dict[str, Any],
) -> str | None:
    """Render one human-readable template without overwriting pack bytes."""
    template_member = _human_template_member(pack=pack, artifact_id=artifact_id)
    if template_member is None:
        return None
    template_text = pack.members[template_member].decode("utf-8")
    rendered = template_text
    for key, value in sorted(field_values.items()):
        rendered = rendered.replace(f"{{{{{key}}}}}", _stringify_template_value(value))
    return rendered


def _human_template_member(*, pack: LoadedFismaTemplatePack, artifact_id: str) -> str | None:
    for entry in pack.manifest.get("artifact_templates", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("artifact_id") != artifact_id:
            continue
        member = entry.get("human_template_member")
        return member if isinstance(member, str) else None
    return None


def _read_zip_members(archive_bytes: bytes) -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/").lstrip("./")
            if ".." in name.split("/"):
                raise FismaTemplatePackError(f"unsafe archive member path: {info.filename}")
            members[name] = archive.read(info.filename)
    return members


def _validate_manifest(manifest: Any) -> None:
    errors = sorted(
        _manifest_validator().iter_errors(manifest),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        raise FismaTemplatePackError(errors[0].message)


def _validate_required_members(*, manifest: dict[str, Any], members: dict[str, bytes]) -> None:
    required = manifest.get("required_members")
    if not isinstance(required, list):
        raise FismaTemplatePackError("required_members must be a list")
    missing = [member for member in required if member not in members]
    if missing:
        raise FismaTemplatePackError(
            "template pack is missing required archive members: " + ", ".join(sorted(missing))
        )


def _validate_mappings_and_schemas(*, manifest: dict[str, Any], members: dict[str, bytes]) -> None:
    artifact_ids = {
        entry.get("artifact_id")
        for entry in manifest.get("artifact_templates", [])
        if isinstance(entry, dict) and isinstance(entry.get("artifact_id"), str)
    }
    for mapping in manifest.get("field_mappings", []):
        if not isinstance(mapping, dict):
            raise FismaTemplatePackError("field_mappings entries must be objects")
        target_artifact = mapping.get("target_artifact")
        if not isinstance(target_artifact, str) or target_artifact not in artifact_ids:
            raise FismaTemplatePackError(
                f"field mapping references unknown artifact: {target_artifact!r}"
            )

    for entry in manifest.get("artifact_templates", []):
        if not isinstance(entry, dict):
            raise FismaTemplatePackError("artifact_templates entries must be objects")
        schema_member = entry.get("machine_schema_member")
        if not isinstance(schema_member, str):
            continue
        if schema_member not in members:
            raise FismaTemplatePackError(
                f"template pack is missing machine schema member: {schema_member}"
            )
        try:
            schema = json.loads(members[schema_member].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FismaTemplatePackError("machine schema member is not valid JSON") from exc
        Draft202012Validator(schema).check_schema(schema)


def _stringify_template_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return json.dumps(value, sort_keys=True)


@cache
def _manifest_schema_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "contracts"
        / "fisma-template-pack.schema.json"
    )


@cache
def _manifest_validator() -> Draft202012Validator:
    schema = json.loads(_manifest_schema_path().read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    validator.check_schema(schema)
    return validator


__all__ = [
    "FismaTemplatePackError",
    "FismaTemplatePackReference",
    "LoadedFismaTemplatePack",
    "is_template_pack_rendering_eligible",
    "load_template_pack_reference",
    "load_verified_template_pack",
    "render_human_template",
]
