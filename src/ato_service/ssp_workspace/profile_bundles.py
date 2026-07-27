"""Validated, immutable, offline SSP profile bundles."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from zipfile import BadZipFile, ZipFile, ZipInfo

from jsonschema import Draft202012Validator

from ato_service.oscal_catalog import (
    OscalCatalogError,
    index_oscal_catalog_controls,
)

ImpactLevel = Literal["low", "moderate", "high"]

_MANIFEST_NAME = "manifest.json"
_SCHEMA_DIR = Path(__file__).with_name("schemas")
_REQUIRED_ROLES = frozenset({"catalog", "baselines", "ssp_requirements"})
_MAX_MANIFEST_BYTES = 1_048_576
_MAX_MEMBER_BYTES = 10_485_760
_MAX_ARCHIVE_BYTES = 52_428_800
_MAX_ARCHIVE_MEMBERS = 64
_MAX_COMPRESSION_RATIO = 200


class ProfileBundleError(ValueError):
    """Raised when a local SSP profile bundle is invalid."""


@dataclass(frozen=True, slots=True)
class ProfileSource:
    source_id: str
    title: str
    version: str
    reference: str


@dataclass(frozen=True, slots=True)
class ProfileBundleFile:
    role: str
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ProfileManifest:
    schema_version: str
    profile_id: str
    profile_version: str
    display_name: str
    sources: tuple[ProfileSource, ...]
    files: tuple[ProfileBundleFile, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class ProfileControl:
    control_id: str
    title: str
    requirement_text: str
    catalog_pointer: str


@dataclass(frozen=True, slots=True)
class SspRequiredItem:
    item_id: str
    title: str
    value_type: Literal["string", "string_list"]
    min_length: int | None
    allowed_values: tuple[str, ...]
    evidence_required_for_agent: bool


@dataclass(frozen=True, slots=True)
class ProfileBundle:
    """Validated bundle content detached from mutable JSON documents."""

    manifest: ProfileManifest
    catalog_controls: tuple[ProfileControl, ...]
    low_control_ids: tuple[str, ...]
    moderate_control_ids: tuple[str, ...]
    high_control_ids: tuple[str, ...]
    ssp_required_items: tuple[SspRequiredItem, ...]

    def control_ids_for(self, impact_level: ImpactLevel) -> tuple[str, ...]:
        if impact_level == "low":
            return self.low_control_ids
        if impact_level == "moderate":
            return self.moderate_control_ids
        if impact_level == "high":
            return self.high_control_ids
        raise ProfileBundleError(
            f"unsupported impact level {impact_level!r}; expected low, moderate, or high"
        )


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    profile_id: str
    profile_version: str
    manifest_sha256: str
    impact_level: ImpactLevel
    sources: tuple[ProfileSource, ...]
    controls: tuple[ProfileControl, ...]
    ssp_required_items: tuple[SspRequiredItem, ...]


@dataclass(frozen=True, slots=True)
class SourceVersionChange:
    source_id: str
    old_version: str | None
    new_version: str | None


@dataclass(frozen=True, slots=True)
class ProfileDiff:
    old_profile_version: str
    new_profile_version: str
    impact_level: ImpactLevel
    added_control_ids: tuple[str, ...]
    removed_control_ids: tuple[str, ...]
    changed_control_ids: tuple[str, ...]
    added_ssp_item_ids: tuple[str, ...]
    removed_ssp_item_ids: tuple[str, ...]
    changed_ssp_item_ids: tuple[str, ...]
    source_version_changes: tuple[SourceVersionChange, ...]


def load_profile_bundle(path: Path) -> ProfileBundle:
    """Load and validate a local directory or ZIP profile bundle.

    The function performs no network access and does not extract archives.
    """
    bundle_path = _resolve_local_bundle_path(path)
    if bundle_path.is_dir():
        manifest_bytes, read_member = _open_directory_bundle(bundle_path)
    else:
        manifest_bytes, archive_members = _open_zip_bundle(bundle_path)

        def read_member(member_path: str) -> bytes:
            try:
                return archive_members[member_path]
            except KeyError as exc:
                raise ProfileBundleError(
                    f"profile bundle file is missing: {member_path}"
                ) from exc

    manifest_document = _load_json_object(
        manifest_bytes,
        label="profile bundle manifest",
    )
    _validate_schema(
        manifest_document,
        schema_name="profile-bundle-manifest.schema.json",
        label="profile bundle manifest",
    )
    manifest = _build_manifest(manifest_document, manifest_bytes=manifest_bytes)

    documents: dict[str, dict[str, Any]] = {}
    for entry in manifest.files:
        member_bytes = read_member(entry.path)
        actual_digest = hashlib.sha256(member_bytes).hexdigest()
        if actual_digest != entry.sha256:
            raise ProfileBundleError(
                f"checksum mismatch for profile bundle file {entry.path!r}"
            )
        if entry.role in _REQUIRED_ROLES:
            documents[entry.role] = _load_json_object(
                member_bytes,
                label=f"profile bundle file {entry.path!r}",
            )

    catalog_controls = _load_catalog_controls(documents["catalog"])
    control_ids_by_level = _load_baselines(
        documents["baselines"],
        catalog_controls=catalog_controls,
    )
    ssp_required_items = _load_ssp_requirements(documents["ssp_requirements"])

    return ProfileBundle(
        manifest=manifest,
        catalog_controls=catalog_controls,
        low_control_ids=control_ids_by_level["low"],
        moderate_control_ids=control_ids_by_level["moderate"],
        high_control_ids=control_ids_by_level["high"],
        ssp_required_items=ssp_required_items,
    )


def resolve_profile(
    bundle: ProfileBundle,
    impact_level: ImpactLevel,
) -> ResolvedProfile:
    """Resolve one immutable Low, Moderate, or High profile."""
    selected_ids = bundle.control_ids_for(impact_level)
    controls_by_id = {
        control.control_id: control for control in bundle.catalog_controls
    }
    return ResolvedProfile(
        profile_id=bundle.manifest.profile_id,
        profile_version=bundle.manifest.profile_version,
        manifest_sha256=bundle.manifest.sha256,
        impact_level=impact_level,
        sources=bundle.manifest.sources,
        controls=tuple(controls_by_id[control_id] for control_id in selected_ids),
        ssp_required_items=bundle.ssp_required_items,
    )


def diff_profiles(old: ResolvedProfile, new: ResolvedProfile) -> ProfileDiff:
    """Return a deterministic semantic diff between two resolved versions."""
    if old.profile_id != new.profile_id:
        raise ProfileBundleError("profile diff requires matching profile_id values")
    if old.impact_level != new.impact_level:
        raise ProfileBundleError("profile diff requires matching impact levels")

    old_controls = {control.control_id: control for control in old.controls}
    new_controls = {control.control_id: control for control in new.controls}
    old_control_ids = set(old_controls)
    new_control_ids = set(new_controls)

    old_items = {item.item_id: item for item in old.ssp_required_items}
    new_items = {item.item_id: item for item in new.ssp_required_items}
    old_item_ids = set(old_items)
    new_item_ids = set(new_items)

    old_sources = {source.source_id: source.version for source in old.sources}
    new_sources = {source.source_id: source.version for source in new.sources}

    return ProfileDiff(
        old_profile_version=old.profile_version,
        new_profile_version=new.profile_version,
        impact_level=old.impact_level,
        added_control_ids=tuple(sorted(new_control_ids - old_control_ids)),
        removed_control_ids=tuple(sorted(old_control_ids - new_control_ids)),
        changed_control_ids=tuple(
            sorted(
                control_id
                for control_id in old_control_ids & new_control_ids
                if _control_semantics(old_controls[control_id])
                != _control_semantics(new_controls[control_id])
            )
        ),
        added_ssp_item_ids=tuple(sorted(new_item_ids - old_item_ids)),
        removed_ssp_item_ids=tuple(sorted(old_item_ids - new_item_ids)),
        changed_ssp_item_ids=tuple(
            sorted(
                item_id
                for item_id in old_item_ids & new_item_ids
                if old_items[item_id] != new_items[item_id]
            )
        ),
        source_version_changes=tuple(
            SourceVersionChange(
                source_id=source_id,
                old_version=old_sources.get(source_id),
                new_version=new_sources.get(source_id),
            )
            for source_id in sorted(set(old_sources) | set(new_sources))
            if old_sources.get(source_id) != new_sources.get(source_id)
        ),
    )


def _control_semantics(control: ProfileControl) -> tuple[str, str]:
    return control.title, control.requirement_text


def _resolve_local_bundle_path(path: Path) -> Path:
    if not isinstance(path, Path):
        raise ProfileBundleError("profile bundle path must be a pathlib.Path")
    if "\0" in str(path):
        raise ProfileBundleError("profile bundle path is malformed")
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ProfileBundleError("profile bundle path must not be a symlink")
    resolved = expanded.resolve()
    if not resolved.exists():
        raise ProfileBundleError("profile bundle path does not exist")
    if not resolved.is_dir() and not resolved.is_file():
        raise ProfileBundleError(
            "profile bundle path must be a directory or ZIP archive"
        )
    return resolved


def _open_directory_bundle(
    root: Path,
) -> tuple[bytes, Callable[[str], bytes]]:
    manifest_path = root / _MANIFEST_NAME
    manifest_bytes = _read_safe_directory_member(
        root,
        manifest_path,
        maximum_bytes=_MAX_MANIFEST_BYTES,
    )

    def read_member(member_path: str) -> bytes:
        normalized = _validated_member_path(member_path)
        return _read_safe_directory_member(
            root,
            root.joinpath(*normalized.parts),
            maximum_bytes=_MAX_MEMBER_BYTES,
        )

    return manifest_bytes, read_member


def _read_safe_directory_member(
    root: Path,
    path: Path,
    *,
    maximum_bytes: int,
) -> bytes:
    if path.is_symlink():
        raise ProfileBundleError(
            f"profile bundle file must not be a symlink: {path.name}"
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ProfileBundleError(
            f"profile bundle file is missing or unreadable: {path.name}"
        ) from exc
    if root not in resolved.parents:
        raise ProfileBundleError("profile bundle file path escapes bundle directory")
    if not resolved.is_file():
        raise ProfileBundleError(
            f"profile bundle member must be a regular file: {path.name}"
        )
    try:
        size = resolved.stat().st_size
        if size > maximum_bytes:
            raise ProfileBundleError(
                f"profile bundle file exceeds {maximum_bytes} bytes: {path.name}"
            )
        return resolved.read_bytes()
    except OSError as exc:
        raise ProfileBundleError(
            f"profile bundle file is unreadable: {path.name}"
        ) from exc


def _open_zip_bundle(path: Path) -> tuple[bytes, dict[str, bytes]]:
    try:
        archive_size = path.stat().st_size
        if archive_size > _MAX_ARCHIVE_BYTES:
            raise ProfileBundleError(
                f"profile bundle archive exceeds {_MAX_ARCHIVE_BYTES} bytes"
            )
        with ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > _MAX_ARCHIVE_MEMBERS:
                raise ProfileBundleError(
                    f"profile bundle archive exceeds {_MAX_ARCHIVE_MEMBERS} members"
                )
            total_uncompressed_bytes = sum(
                info.file_size for info in infos if not info.is_dir()
            )
            if total_uncompressed_bytes > _MAX_ARCHIVE_BYTES:
                raise ProfileBundleError(
                    "profile bundle archive has excessive uncompressed content"
                )
            members: dict[str, bytes] = {}
            for info in infos:
                if info.is_dir():
                    continue
                normalized = _validated_member_path(info.filename).as_posix()
                if normalized in members:
                    raise ProfileBundleError(
                        f"profile bundle archive has duplicate member {normalized!r}"
                    )
                _validate_zip_member(info)
                members[normalized] = archive.read(info)
    except ProfileBundleError:
        raise
    except (BadZipFile, OSError, RuntimeError) as exc:
        raise ProfileBundleError(
            "profile bundle archive is unreadable or is not a valid ZIP archive"
        ) from exc

    manifest_bytes = members.get(_MANIFEST_NAME)
    if manifest_bytes is None:
        raise ProfileBundleError("profile bundle manifest.json is missing")
    if len(manifest_bytes) > _MAX_MANIFEST_BYTES:
        raise ProfileBundleError(
            f"profile bundle manifest exceeds {_MAX_MANIFEST_BYTES} bytes"
        )
    return manifest_bytes, members


def _validate_zip_member(info: ZipInfo) -> None:
    if info.flag_bits & 0x1:
        raise ProfileBundleError(
            f"encrypted profile bundle member is not supported: {info.filename!r}"
        )
    if info.file_size > _MAX_MEMBER_BYTES:
        raise ProfileBundleError(
            f"profile bundle member exceeds {_MAX_MEMBER_BYTES} bytes: {info.filename!r}"
        )
    if (
        info.compress_size > 0
        and info.file_size / info.compress_size > _MAX_COMPRESSION_RATIO
    ):
        raise ProfileBundleError(
            f"profile bundle member has an unsafe compression ratio: {info.filename!r}"
        )
    unix_mode = (info.external_attr >> 16) & 0o170000
    if unix_mode == 0o120000:
        raise ProfileBundleError(
            f"profile bundle archive must not contain symlinks: {info.filename!r}"
        )


def _validated_member_path(raw_path: str) -> PurePosixPath:
    if not isinstance(raw_path, str) or not raw_path or "\0" in raw_path:
        raise ProfileBundleError("profile bundle member path is malformed")
    if "\\" in raw_path:
        raise ProfileBundleError(
            f"profile bundle member path must use forward slashes: {raw_path!r}"
        )
    path = PurePosixPath(raw_path)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ProfileBundleError(f"profile bundle member path is unsafe: {raw_path!r}")
    if path.as_posix() != raw_path:
        raise ProfileBundleError(
            f"profile bundle member path is not canonical: {raw_path!r}"
        )
    return path


def _build_manifest(
    document: dict[str, Any],
    *,
    manifest_bytes: bytes,
) -> ProfileManifest:
    sources = tuple(
        ProfileSource(
            source_id=source["source_id"],
            title=source["title"],
            version=source["version"],
            reference=source["reference"],
        )
        for source in document["sources"]
    )
    source_ids = [source.source_id for source in sources]
    if len(source_ids) != len(set(source_ids)):
        raise ProfileBundleError("profile bundle source_id values must be unique")

    files = tuple(
        ProfileBundleFile(
            role=file_entry["role"],
            path=_validated_member_path(file_entry["path"]).as_posix(),
            sha256=file_entry["sha256"],
        )
        for file_entry in document["files"]
    )
    roles = [entry.role for entry in files]
    if len(roles) != len(set(roles)):
        raise ProfileBundleError("profile bundle file roles must be unique")
    if not _REQUIRED_ROLES.issubset(roles):
        raise ProfileBundleError(
            "profile bundle must declare exactly one catalog, baselines, "
            "and ssp_requirements file"
        )
    paths = [entry.path for entry in files]
    if len(paths) != len(set(paths)):
        raise ProfileBundleError("profile bundle file paths must be unique")
    if _MANIFEST_NAME in paths:
        raise ProfileBundleError("manifest.json cannot checksum itself")

    return ProfileManifest(
        schema_version=document["schema_version"],
        profile_id=document["profile_id"],
        profile_version=document["profile_version"],
        display_name=document["display_name"],
        sources=tuple(sorted(sources, key=lambda source: source.source_id)),
        files=tuple(sorted(files, key=lambda entry: entry.role)),
        sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )


def _load_catalog_controls(
    document: dict[str, Any],
) -> tuple[ProfileControl, ...]:
    try:
        indexed = index_oscal_catalog_controls(document)
    except OscalCatalogError as exc:
        raise ProfileBundleError(f"profile catalog is invalid: {exc}") from exc
    return tuple(
        ProfileControl(
            control_id=record.normalized_id,
            title=record.title.strip(),
            requirement_text=record.requirement_text.strip(),
            catalog_pointer=record.catalog_pointer,
        )
        for _, record in sorted(indexed.items())
    )


def _load_baselines(
    document: dict[str, Any],
    *,
    catalog_controls: tuple[ProfileControl, ...],
) -> dict[ImpactLevel, tuple[str, ...]]:
    _validate_schema(
        document,
        schema_name="profile-baselines.schema.json",
        label="profile baselines",
    )
    catalog_by_id = {control.control_id: control for control in catalog_controls}
    catalog_ids = set(catalog_by_id)
    resolved: dict[ImpactLevel, tuple[str, ...]] = {}
    for impact_level in ("low", "moderate", "high"):
        control_ids = tuple(sorted(document[impact_level]))
        missing_ids = tuple(sorted(set(control_ids) - catalog_ids))
        if missing_ids:
            raise ProfileBundleError(
                f"{impact_level} baseline references controls missing from catalog: "
                + ", ".join(missing_ids)
            )
        missing_statements = tuple(
            control_id
            for control_id in control_ids
            if not catalog_by_id[control_id].requirement_text
        )
        if missing_statements:
            raise ProfileBundleError(
                f"{impact_level} baseline controls have missing statement prose: "
                + ", ".join(missing_statements)
            )
        resolved[impact_level] = control_ids
    return resolved


def _load_ssp_requirements(
    document: dict[str, Any],
) -> tuple[SspRequiredItem, ...]:
    _validate_schema(
        document,
        schema_name="profile-ssp-requirements.schema.json",
        label="SSP requirements",
    )
    items = tuple(
        SspRequiredItem(
            item_id=item["item_id"],
            title=item["title"].strip(),
            value_type=item["value_type"],
            min_length=item.get("min_length"),
            allowed_values=tuple(item.get("allowed_values", ())),
            evidence_required_for_agent=item["evidence_required_for_agent"],
        )
        for item in document["items"]
    )
    item_ids = [item.item_id for item in items]
    if len(item_ids) != len(set(item_ids)):
        raise ProfileBundleError("SSP requirement item_id values must be unique")
    for item in items:
        if item.value_type == "string_list" and item.min_length is not None:
            raise ProfileBundleError(
                f"SSP requirement {item.item_id!r} cannot set min_length "
                "for value_type 'string_list'"
            )
    return tuple(sorted(items, key=lambda item: item.item_id))


def _load_json_object(raw_bytes: bytes, *, label: str) -> dict[str, Any]:
    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileBundleError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise ProfileBundleError(f"{label} must be a JSON object")
    return document


@cache
def _schema_validator(schema_name: str) -> Draft202012Validator:
    schema_path = _SCHEMA_DIR / schema_name
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileBundleError(
            f"profile bundle validator schema is unavailable: {schema_name}"
        ) from exc
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_schema(
    document: dict[str, Any],
    *,
    schema_name: str,
    label: str,
) -> None:
    validator = _schema_validator(schema_name)
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    if not errors:
        return
    error = errors[0]
    location = "/".join(str(part) for part in error.absolute_path) or "<root>"
    raise ProfileBundleError(
        f"{label} failed schema validation at {location}: {error.message}"
    )


__all__ = [
    "ImpactLevel",
    "ProfileBundle",
    "ProfileBundleError",
    "ProfileBundleFile",
    "ProfileControl",
    "ProfileDiff",
    "ProfileManifest",
    "ProfileSource",
    "ResolvedProfile",
    "SourceVersionChange",
    "SspRequiredItem",
    "diff_profiles",
    "load_profile_bundle",
    "resolve_profile",
]
