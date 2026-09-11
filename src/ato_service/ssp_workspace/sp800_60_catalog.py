"""Pinned NIST SP 800-60 information type catalog for agency FISMA workspaces."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Literal

from ato_service.project_root import ProjectRootError, find_project_root

ImpactLevel = Literal["low", "moderate", "high"]

_CATALOG_FILENAME = "sp800-60-rev2-information-types.json"
_CATALOG_RELATIVE = Path(
    "reference/authorities/nist/sp800-60-rev2-information-types.json"
)


class Sp80060CatalogError(RuntimeError):
    """Raised when the bundled or reference catalog cannot be loaded."""


@dataclass(frozen=True, slots=True)
class Sp80060InformationType:
    identifier: str
    title: str
    confidentiality: ImpactLevel
    integrity: ImpactLevel
    availability: ImpactLevel


@dataclass(frozen=True, slots=True)
class Sp80060Catalog:
    source_id: str
    title: str
    version: str
    reference: str
    information_types: tuple[Sp80060InformationType, ...]

    def by_identifier(self) -> dict[str, Sp80060InformationType]:
        return {item.identifier: item for item in self.information_types}


def _load_catalog_document() -> dict[str, object]:
    bundled = files("ato_service.ssp_workspace").joinpath(_CATALOG_FILENAME)
    try:
        return _catalog_document_from_text(bundled.read_text(encoding="utf-8"))
    except (OSError, Sp80060CatalogError):
        pass
    try:
        path = find_project_root() / _CATALOG_RELATIVE
    except ProjectRootError as exc:
        raise Sp80060CatalogError("SP 800-60 catalog is not available") from exc
    return _catalog_document_from_path(path)


def _catalog_document_from_text(raw: str) -> dict[str, object]:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Sp80060CatalogError("SP 800-60 catalog is invalid JSON") from exc
    if not isinstance(document, dict):
        raise Sp80060CatalogError("SP 800-60 catalog must be a JSON object")
    return document


def _catalog_document_from_path(path: Path) -> dict[str, object]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Sp80060CatalogError(
            f"SP 800-60 catalog at {path} is unreadable"
        ) from exc
    return _catalog_document_from_text(raw)


@cache
def load_sp800_60_catalog() -> Sp80060Catalog:
    document = _load_catalog_document()
    items = document.get("information_types")
    if not isinstance(items, list) or not items:
        raise Sp80060CatalogError("SP 800-60 catalog must include information_types")
    parsed: list[Sp80060InformationType] = []
    for entry in items:
        if not isinstance(entry, dict):
            raise Sp80060CatalogError("SP 800-60 catalog entries must be objects")
        identifier = str(entry.get("identifier") or "").strip()
        title = str(entry.get("title") or "").strip()
        for field in ("confidentiality", "integrity", "availability"):
            value = entry.get(field)
            if value not in {"low", "moderate", "high"}:
                raise Sp80060CatalogError(
                    f"SP 800-60 catalog entry {identifier!r} has invalid {field}"
                )
        if not identifier or not title:
            raise Sp80060CatalogError(
                "SP 800-60 catalog entries require identifier and title"
            )
        parsed.append(
            Sp80060InformationType(
                identifier=identifier,
                title=title,
                confidentiality=entry["confidentiality"],
                integrity=entry["integrity"],
                availability=entry["availability"],
            )
        )
    return Sp80060Catalog(
        source_id=str(document.get("source_id") or "nist-sp-800-60-rev2"),
        title=str(document.get("title") or "NIST SP 800-60"),
        version=str(document.get("version") or "2.0.0"),
        reference=str(document.get("reference") or ""),
        information_types=tuple(parsed),
    )


def catalog_document_for_api() -> dict[str, object]:
    catalog = load_sp800_60_catalog()
    return {
        "source_id": catalog.source_id,
        "title": catalog.title,
        "version": catalog.version,
        "reference": catalog.reference,
        "information_types": [
            {
                "identifier": item.identifier,
                "title": item.title,
                "confidentiality": item.confidentiality,
                "integrity": item.integrity,
                "availability": item.availability,
            }
            for item in catalog.information_types
        ],
    }
