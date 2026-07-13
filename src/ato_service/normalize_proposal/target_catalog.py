"""Closed profile-aware target catalog for normalize_proposal."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal

from ato_service.normalize_proposal.constants import PROHIBITED_TARGET_PREFIXES
from ato_service.normalize_proposal.json_utils import value_at_json_pointer

ValueKind = Literal["string", "nullable_string", "enum"]

_POINTER_TERM_RE = re.compile(r"[a-z][a-z0-9_]*")


@dataclass(frozen=True, slots=True)
class TargetSpec:
    pointer: str
    description: str
    value_kind: ValueKind
    max_length: int | None = None
    enum_values: frozenset[str] | None = None

    def search_terms(self) -> frozenset[str]:
        terms: set[str] = set()
        for part in self.pointer.strip("/").split("/"):
            decoded = part.replace("~1", "/").replace("~0", "~")
            for token in _POINTER_TERM_RE.findall(decoded.casefold()):
                if len(token) >= 3:
                    terms.add(token)
        for token in _POINTER_TERM_RE.findall(self.description.casefold()):
            if len(token) >= 3:
                terms.add(token)
        return frozenset(terms)

    def prompt_entry(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "pointer": self.pointer,
            "description": self.description,
            "value_kind": self.value_kind,
        }
        if self.enum_values is not None:
            entry["enum_values"] = sorted(self.enum_values)
        if self.max_length is not None:
            entry["max_length"] = self.max_length
        return entry


_FISMA_TARGETS: tuple[TargetSpec, ...] = (
    TargetSpec("/package/title", "Package document title", "string", max_length=500),
    TargetSpec("/package/prepared_for", "Agency or customer the package was prepared for", "string", max_length=500),
    TargetSpec("/package/reporting_period", "Reporting period label or date range", "nullable_string", max_length=128),
    TargetSpec(
        "/system/authorization_boundary",
        "System authorization boundary narrative",
        "string",
        max_length=8000,
    ),
    TargetSpec("/system/mission_summary", "System mission or business purpose summary", "string", max_length=8000),
    TargetSpec(
        "/system/impact_level",
        "FIPS 199 impact level",
        "enum",
        enum_values=frozenset({"low", "moderate", "high"}),
    ),
    TargetSpec("/system/authorization_path", "Authorization path such as agency or fedramp", "string", max_length=500),
)

_FEDRAMP_20X_TARGETS: tuple[TargetSpec, ...] = (
    TargetSpec("/package/title", "Package document title", "string", max_length=500),
    TargetSpec("/package/prepared_for", "Program office or customer the package was prepared for", "string", max_length=500),
    TargetSpec("/package/reporting_period", "Reporting period label or date range", "nullable_string", max_length=128),
    TargetSpec(
        "/system/authorization_boundary",
        "System authorization boundary narrative",
        "string",
        max_length=8000,
    ),
    TargetSpec("/system/mission_summary", "System mission or business purpose summary", "string", max_length=8000),
    TargetSpec("/system/authorization_path", "Authorization path such as fedramp", "string", max_length=500),
)

_FEDRAMP_REV5_TARGETS: tuple[TargetSpec, ...] = (
    TargetSpec("/package/title", "Package document title", "string", max_length=500),
    TargetSpec("/package/prepared_for", "Customer the package was prepared for", "string", max_length=500),
    TargetSpec("/package/reporting_period", "Reporting period label or date range", "nullable_string", max_length=128),
    TargetSpec(
        "/system/authorization_boundary",
        "System authorization boundary narrative",
        "string",
        max_length=8000,
    ),
    TargetSpec("/system/mission_summary", "System mission or business purpose summary", "string", max_length=8000),
    TargetSpec("/system/authorization_path", "Authorization path such as fedramp", "string", max_length=500),
)

_PROFILE_CATALOGS: dict[str, tuple[TargetSpec, ...]] = {
    "fisma_agency_security": _FISMA_TARGETS,
    "fedramp_20x_program": _FEDRAMP_20X_TARGETS,
    "fedramp_rev5_transition": _FEDRAMP_REV5_TARGETS,
}


def catalog_for_profile(profile_id: str) -> tuple[TargetSpec, ...]:
    try:
        return _PROFILE_CATALOGS[profile_id]
    except KeyError as exc:
        raise ValueError(f"unsupported profile_id for normalize_proposal: {profile_id}") from exc


def target_spec_for_pointer(*, profile_id: str, pointer: str) -> TargetSpec | None:
    for spec in catalog_for_profile(profile_id):
        if spec.pointer == pointer:
            return spec
    return None


def catalog_entries_for_empty_targets(
    *,
    profile_id: str,
    empty_targets: tuple[str, ...] | list[str],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for pointer in empty_targets:
        spec = target_spec_for_pointer(profile_id=profile_id, pointer=pointer)
        if spec is None:
            continue
        entries.append(spec.prompt_entry())
    return entries


def search_terms_for_empty_targets(
    *,
    profile_id: str,
    empty_targets: tuple[str, ...] | list[str],
) -> frozenset[str]:
    terms: set[str] = set()
    for pointer in empty_targets:
        spec = target_spec_for_pointer(profile_id=profile_id, pointer=pointer)
        if spec is not None:
            terms.update(spec.search_terms())
    return frozenset(terms)


def is_prohibited_target(pointer: str) -> bool:
    for prefix in PROHIBITED_TARGET_PREFIXES:
        if pointer == prefix or pointer.startswith(prefix + "/"):
            return True
    return False


def is_target_allowed(*, profile_id: str, pointer: str) -> bool:
    if is_prohibited_target(pointer):
        return False
    return target_spec_for_pointer(profile_id=profile_id, pointer=pointer) is not None


def is_target_empty(document: dict[str, Any], pointer: str) -> bool:
    try:
        value = value_at_json_pointer(document, pointer)
    except (KeyError, IndexError, TypeError):
        return True
    if value is None:
        return True
    if isinstance(value, str) and value == "":
        return True
    if isinstance(value, list) and len(value) == 0:
        return True
    if isinstance(value, dict) and len(value) == 0:
        return True
    return False


def list_empty_targets(
    *,
    profile_id: str,
    document: dict[str, Any],
    field_provenance: dict[str, Any],
) -> tuple[str, ...]:
    """Return catalog targets that are empty and not deterministically filled."""
    empty: list[str] = []
    for spec in catalog_for_profile(profile_id):
        if not is_target_empty(document, spec.pointer):
            continue
        provenance = field_provenance.get(spec.pointer)
        if isinstance(provenance, dict):
            method = provenance.get("extraction_method")
            if method in {"deterministic", "text", "vision"}:
                continue
        empty.append(spec.pointer)
    return tuple(empty)


def allowed_target_set(profile_id: str) -> frozenset[str]:
    return frozenset(spec.pointer for spec in catalog_for_profile(profile_id))
