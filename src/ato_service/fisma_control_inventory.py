"""Load and validate customer FISMA security control inventory documents."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
import json
import re
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator, FormatChecker

from ato_service.authority_catalog import (
    AuthorityCatalogError,
    load_json_authority_archive_member,
)
from ato_service.authority_manifest import (
    AuthorityManifestVerificationError,
    verify_authority_manifest,
)
from ato_service.project_root import find_project_root

_SCHEMA_RELATIVE_PATH = Path("docs/contracts/fisma-control-inventory.schema.json")
_MANIFEST_RELATIVE_PATH = Path("docs/contracts/authority-manifest.json")
_NIST_AUTHORITY_ID = "nist-sp800-53-release-5.2.0"
_NIST_CATALOG_MEMBER_SUFFIX = "NIST_SP-800-53_rev5_catalog-min.json"
_CONTROL_ID_PATTERN = re.compile(r"^[A-Z]{2,3}-\d+(?:\([0-9]+\)|(?:\.[0-9]+)+)?$")
_FORMAT_CHECKER = FormatChecker()


class FismaControlInventoryError(ValueError):
    """Raised when a FISMA control inventory cannot be loaded or validated."""


@dataclass(frozen=True, slots=True)
class FismaControlInventory:
    schema_version: str
    inventory_id: str
    authority_manifest_id: str
    impact_level: Literal["low", "moderate", "high"]
    status: Literal["draft", "approved"]
    approved_at: str | None
    approved_by: str | None
    source_reference: str
    control_ids: tuple[str, ...]


def load_fisma_control_inventory(
    path: Path,
    *,
    project_root: Path | None = None,
    schema_path: Path | None = None,
) -> FismaControlInventory:
    """Load, schema-validate, and semantically validate one control inventory file."""
    resolved_path = _resolve_explicit_inventory_path(path)

    try:
        raw_bytes = resolved_path.read_bytes()
    except OSError as exc:
        raise FismaControlInventoryError(
            "control inventory is unreadable or malformed JSON"
        ) from exc

    try:
        raw_document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FismaControlInventoryError(
            "control inventory is unreadable or malformed JSON"
        ) from exc

    if not isinstance(raw_document, dict):
        raise FismaControlInventoryError("control inventory must be a JSON object")

    root = (project_root or find_project_root(resolved_path)).resolve()
    validator = _inventory_validator(project_root=root, schema_path=schema_path)
    validation_error = next(validator.iter_errors(raw_document), None)
    if validation_error is not None:
        raise FismaControlInventoryError(
            f"control inventory failed schema validation: {validation_error.message}"
        )

    manifest = _verified_authority_manifest(project_root=root)
    _validate_inventory_authority_manifest_id(
        inventory_manifest_id=str(raw_document["authority_manifest_id"]),
        manifest=manifest,
    )

    control_ids = _validated_control_ids(raw_document.get("control_ids"))
    _reject_privacy_family_control_ids(
        control_ids,
        project_root=root,
    )

    status = raw_document["status"]
    approved_at = raw_document.get("approved_at")
    approved_by = raw_document.get("approved_by")
    _validate_approval_boundary(
        status=status,
        approved_at=approved_at,
        approved_by=approved_by,
    )

    return FismaControlInventory(
        schema_version=str(raw_document["schema_version"]),
        inventory_id=str(raw_document["inventory_id"]),
        authority_manifest_id=str(raw_document["authority_manifest_id"]),
        impact_level=raw_document["impact_level"],
        status=status,
        approved_at=approved_at if isinstance(approved_at, str) else None,
        approved_by=approved_by if isinstance(approved_by, str) else None,
        source_reference=str(raw_document["source_reference"]),
        control_ids=control_ids,
    )


def privacy_family_prefixes_from_catalog(
    catalog_document: dict[str, Any],
) -> frozenset[str] | None:
    """Return privacy-family prefixes when the NIST catalog declares a clear namespace."""
    return _privacy_family_prefixes_from_catalog(catalog_document)


def _resolve_explicit_inventory_path(path: Path) -> Path:
    if "\0" in str(path):
        raise FismaControlInventoryError("control inventory path is malformed")

    expanded = path.expanduser()
    if expanded.is_symlink():
        raise FismaControlInventoryError(
            "control inventory path must not be a symlink"
        )
    resolved = expanded.resolve()
    if not resolved.is_file():
        raise FismaControlInventoryError(
            "control inventory path must be a regular file"
        )
    return resolved


@cache
def _inventory_validator(
    *,
    project_root: Path,
    schema_path: Path | None,
) -> Draft202012Validator:
    resolved_schema_path = (
        schema_path.resolve()
        if schema_path is not None
        else (project_root / _SCHEMA_RELATIVE_PATH).resolve()
    )
    if not resolved_schema_path.is_file():
        raise FismaControlInventoryError(
            f"control inventory schema not found: {resolved_schema_path}"
        )
    schema = json.loads(resolved_schema_path.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)


def _verified_authority_manifest(*, project_root: Path) -> dict[str, Any]:
    manifest_path = (project_root / _MANIFEST_RELATIVE_PATH).resolve()
    try:
        manifest = verify_authority_manifest(manifest_path, project_root=project_root)
    except AuthorityManifestVerificationError as exc:
        raise FismaControlInventoryError(
            "verified authority manifest is unavailable or invalid"
        ) from exc
    except AuthorityCatalogError as exc:
        raise FismaControlInventoryError(
            "verified authority manifest is unavailable or invalid"
        ) from exc
    except OSError as exc:
        raise FismaControlInventoryError(
            "verified authority manifest is unavailable or invalid"
        ) from exc

    if not isinstance(manifest, dict):
        raise FismaControlInventoryError(
            "verified authority manifest is unavailable or invalid"
        )
    return manifest


def _validate_inventory_authority_manifest_id(
    *,
    inventory_manifest_id: str,
    manifest: dict[str, Any],
) -> None:
    manifest_id = manifest.get("manifest_id")
    if not isinstance(manifest_id, str) or not manifest_id:
        raise FismaControlInventoryError(
            "verified authority manifest must declare manifest_id"
        )
    if inventory_manifest_id != manifest_id:
        raise FismaControlInventoryError(
            "inventory authority_manifest_id "
            f"{inventory_manifest_id!r} does not match authority manifest "
            f"manifest_id {manifest_id!r}"
        )


def _validated_control_ids(raw_control_ids: Any) -> tuple[str, ...]:
    if not isinstance(raw_control_ids, list) or not raw_control_ids:
        raise FismaControlInventoryError("control_ids must be a nonempty array")

    seen: set[str] = set()
    ordered: list[str] = []
    for index, control_id in enumerate(raw_control_ids):
        if not isinstance(control_id, str) or not control_id:
            raise FismaControlInventoryError(
                f"control_ids entry at index {index} must be a nonempty string"
            )
        if control_id != control_id.upper():
            raise FismaControlInventoryError(
                f"control_ids entry at index {index} must already be canonical uppercase"
            )
        if _CONTROL_ID_PATTERN.fullmatch(control_id) is None:
            raise FismaControlInventoryError(
                f"control_ids entry at index {index} has malformed NIST control id"
            )
        if control_id in seen:
            raise FismaControlInventoryError(
                f"duplicate control_id {control_id!r} in control_ids"
            )
        seen.add(control_id)
        ordered.append(control_id)

    return tuple(sorted(ordered))


def _validate_approval_boundary(
    *,
    status: str,
    approved_at: Any,
    approved_by: Any,
) -> None:
    if status == "approved":
        if not isinstance(approved_at, str) or not approved_at:
            raise FismaControlInventoryError(
                "approved inventories must declare approved_at"
            )
        if not isinstance(approved_by, str) or not approved_by.strip():
            raise FismaControlInventoryError(
                "approved inventories must declare approved_by"
            )
        return

    if status == "draft":
        if approved_at is not None:
            raise FismaControlInventoryError(
                "draft inventories must set approved_at to null"
            )
        if approved_by is not None:
            raise FismaControlInventoryError(
                "draft inventories must set approved_by to null"
            )
        return

    raise FismaControlInventoryError(f"unsupported inventory status: {status!r}")


def _reject_privacy_family_control_ids(
    control_ids: tuple[str, ...],
    *,
    project_root: Path,
) -> None:
    privacy_prefixes = _privacy_family_prefixes(project_root=project_root)

    for control_id in control_ids:
        family_prefix = control_id.split("-", 1)[0]
        if family_prefix in privacy_prefixes:
            raise FismaControlInventoryError(
                f"privacy-family control_id {control_id!r} is out of scope for "
                "FISMA security control inventories"
            )


@cache
def _privacy_family_prefixes(*, project_root: Path) -> frozenset[str]:
    manifest = _verified_authority_manifest(project_root=project_root)
    catalog_document = _load_nist_catalog_document(
        project_root=project_root,
        manifest=manifest,
    )
    prefixes = _privacy_family_prefixes_from_catalog(catalog_document)
    if prefixes is None:
        raise FismaControlInventoryError(
            "verified NIST catalog does not declare a recognizable privacy control "
            "family namespace"
        )
    return prefixes


def _load_nist_catalog_document(
    *,
    project_root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    try:
        _member_name, document = load_json_authority_archive_member(
            manifest=manifest,
            authority_id=_NIST_AUTHORITY_ID,
            project_root=project_root,
            member_suffix=_NIST_CATALOG_MEMBER_SUFFIX,
        )
    except AuthorityCatalogError as exc:
        raise FismaControlInventoryError(
            "verified NIST catalog is unavailable or invalid"
        ) from exc
    except (OSError, ValueError, TypeError) as exc:
        raise FismaControlInventoryError(
            "verified NIST catalog is unavailable or invalid"
        ) from exc

    if not isinstance(document, dict):
        raise FismaControlInventoryError(
            "verified NIST catalog is unavailable or invalid"
        )
    return document


def _privacy_family_prefixes_from_catalog(
    catalog_document: dict[str, Any],
) -> frozenset[str] | None:
    """Return privacy-family prefixes when the NIST catalog declares a clear namespace."""
    catalog = catalog_document.get("catalog")
    if not isinstance(catalog, dict):
        return None

    groups = catalog.get("groups")
    if not isinstance(groups, list):
        return None

    prefixes: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_id = group.get("id")
        title = group.get("title")
        if not isinstance(group_id, str) or not isinstance(title, str):
            continue
        if group_id.lower() != "pt":
            continue
        if "personally identifiable information" not in title.lower():
            continue

        label_prefix = _group_label_prefix(group)
        if label_prefix is not None:
            prefixes.add(label_prefix)
        else:
            prefixes.add("PT")

    return frozenset(prefixes) if prefixes else None


def _group_label_prefix(group: dict[str, Any]) -> str | None:
    props = group.get("props")
    if not isinstance(props, list):
        return None
    for prop in props:
        if not isinstance(prop, dict):
            continue
        if prop.get("name") != "label":
            continue
        value = prop.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return None


__all__ = [
    "FismaControlInventory",
    "FismaControlInventoryError",
    "load_fisma_control_inventory",
    "privacy_family_prefixes_from_catalog",
]
