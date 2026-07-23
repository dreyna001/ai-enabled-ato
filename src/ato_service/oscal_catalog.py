"""Shared NIST OSCAL catalog control indexing and statement extraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class OscalCatalogError(ValueError):
    """Raised when OSCAL catalog control indexing or extraction fails."""


@dataclass(frozen=True)
class OscalControlRecord:
    normalized_id: str
    title: str
    requirement_text: str
    catalog_pointer: str


def normalize_oscal_control_id(control_id: str) -> str:
    """Normalize an OSCAL control identifier by uppercasing only."""
    if not isinstance(control_id, str) or not control_id.strip():
        raise OscalCatalogError("control id must be a non-empty string")
    return control_id.strip().upper()


def index_oscal_catalog_controls(
    catalog_document: dict[str, Any],
) -> dict[str, OscalControlRecord]:
    """Index all controls and enhancements from an OSCAL catalog document."""
    catalog = catalog_document.get("catalog")
    if not isinstance(catalog, dict):
        raise OscalCatalogError("catalog document must include catalog")

    groups = catalog.get("groups")
    if not isinstance(groups, list):
        raise OscalCatalogError("catalog must declare groups")

    index: dict[str, OscalControlRecord] = {}
    _index_catalog_groups(
        groups,
        pointer_prefix="/catalog/groups",
        index=index,
    )
    if not index:
        raise OscalCatalogError("catalog index is empty")
    return index


def _index_catalog_groups(
    groups: list[Any],
    *,
    pointer_prefix: str,
    index: dict[str, OscalControlRecord],
) -> None:
    for group_index, group in enumerate(groups):
        if not isinstance(group, dict):
            raise OscalCatalogError(
                f"catalog group at {pointer_prefix}/{group_index} must be an object"
            )
        group_pointer = f"{pointer_prefix}/{group_index}"

        controls = group.get("controls")
        if isinstance(controls, list):
            _index_catalog_controls_list(
                controls,
                pointer_prefix=f"{group_pointer}/controls",
                index=index,
            )

        nested_groups = group.get("groups")
        if isinstance(nested_groups, list):
            _index_catalog_groups(
                nested_groups,
                pointer_prefix=f"{group_pointer}/groups",
                index=index,
            )


def _index_catalog_controls_list(
    controls: list[Any],
    *,
    pointer_prefix: str,
    index: dict[str, OscalControlRecord],
) -> None:
    for control_index, control in enumerate(controls):
        if not isinstance(control, dict):
            raise OscalCatalogError(
                f"catalog control at {pointer_prefix}/{control_index} must be an object"
            )
        control_pointer = f"{pointer_prefix}/{control_index}"
        raw_control_id = control.get("id")
        if not isinstance(raw_control_id, str) or not raw_control_id.strip():
            raise OscalCatalogError(
                f"catalog control at {control_pointer} must declare id"
            )

        normalized_id = normalize_oscal_control_id(raw_control_id)
        if normalized_id in index:
            raise OscalCatalogError(
                f"duplicate catalog control id {normalized_id!r} at {control_pointer}"
            )

        title = control.get("title")
        if not isinstance(title, str) or not title.strip():
            raise OscalCatalogError(
                f"catalog control at {control_pointer} must declare a nonempty title"
            )

        try:
            requirement_text = _build_requirement_text(
                control,
                pointer=control_pointer,
            )
        except OscalCatalogError:
            requirement_text = ""

        index[normalized_id] = OscalControlRecord(
            normalized_id=normalized_id,
            title=title,
            requirement_text=requirement_text,
            catalog_pointer=control_pointer,
        )

        enhancements = control.get("controls")
        if isinstance(enhancements, list):
            _index_catalog_controls_list(
                enhancements,
                pointer_prefix=f"{control_pointer}/controls",
                index=index,
            )


def _build_requirement_text(control: dict[str, Any], *, pointer: str) -> str:
    parts = control.get("parts")
    if not isinstance(parts, list):
        raise OscalCatalogError(
            f"catalog control at {pointer} must declare parts for statement prose"
        )

    statement_part = _find_named_part(parts, "statement")
    if statement_part is None:
        raise OscalCatalogError(
            f"catalog control at {pointer} must declare a statement part"
        )

    segments = _collect_statement_segments(statement_part)
    if not segments:
        raise OscalCatalogError(
            f"catalog control at {pointer} has malformed statement prose"
        )
    return " ".join(segments)


def _find_named_part(parts: list[Any], name: str) -> dict[str, Any] | None:
    for part in parts:
        if not isinstance(part, dict):
            raise OscalCatalogError("catalog control parts must be objects")
        if part.get("name") == name:
            return part
    return None


def _collect_statement_segments(part: dict[str, Any]) -> list[str]:
    segments: list[str] = []

    prose = part.get("prose")
    if isinstance(prose, str) and prose.strip():
        label = _part_label(part)
        text = prose.strip()
        segments.append(f"{label} {text}".strip() if label else text)

    nested_parts = part.get("parts")
    if isinstance(nested_parts, list):
        for nested in nested_parts:
            if not isinstance(nested, dict):
                raise OscalCatalogError("catalog statement parts must be objects")
            segments.extend(_collect_statement_segments(nested))

    return segments


def _part_label(part: dict[str, Any]) -> str | None:
    props = part.get("props")
    if not isinstance(props, list):
        return None
    for prop in props:
        if not isinstance(prop, dict):
            raise OscalCatalogError("catalog part props must be objects")
        if prop.get("name") == "label":
            value = prop.get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


__all__ = [
    "OscalCatalogError",
    "OscalControlRecord",
    "index_oscal_catalog_controls",
    "normalize_oscal_control_id",
]
