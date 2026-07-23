"""Pinned deterministic analysis profile loading and digest verification."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from ato_service.analysis_profile_validation import (
    AnalysisProfileSemanticError,
    validate_analysis_profile_semantics,
)
from ato_service.authority_manifest import (
    AuthorityManifestVerificationError,
    verify_authority_manifest,
)
from ato_service.idempotency import canonical_json_bytes
from ato_service.runtime_config import RuntimeConfig

_SCHEMA_RELATIVE_PATH = Path("docs/contracts/analysis-profile.schema.json")
_FIXTURES_RELATIVE_DIR = Path("docs/contracts/fixtures")
_PROFILES_RELATIVE_DIR = Path("reference/profiles")
_DEFAULT_AUTHORITY_MANIFEST_RELATIVE_PATH = Path(
    "docs/contracts/authority-manifest.json"
)
_PROFILE_FIXTURE_FILENAMES: dict[str, str] = {
    "fisma_agency_security": "analysis-profile.valid.fisma-synthetic.json",
    "fedramp_20x_program": "analysis-profile.valid.fedramp-class-c.json",
    "fedramp_rev5_transition": "analysis-profile.valid.fedramp-rev5.json",
}
_BUNDLED_PROFILE_FILENAMES: dict[tuple[str, str | None, str | None], str] = {
    ("fedramp_20x_program", "C", None): "fedramp-20x-program-class-c.json",
    ("fedramp_rev5_transition", None, "low"): "fedramp-rev5-transition-low.json",
    (
        "fedramp_rev5_transition",
        None,
        "moderate",
    ): "fedramp-rev5-transition-moderate.json",
    ("fedramp_rev5_transition", None, "high"): "fedramp-rev5-transition-high.json",
}
_FORMAT_CHECKER = FormatChecker()
_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class FismaAnalysisProfileReference:
    path: Path
    expected_sha256: str


class AnalysisProfileError(ValueError):
    """Raised when the pinned analysis profile cannot be loaded or validated."""


@cache
def _analysis_profile_validator(*, project_root: Path) -> Draft202012Validator:
    schema_path = (project_root / _SCHEMA_RELATIVE_PATH).resolve()
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)


def bundled_profile_path(
    *,
    profile_id: str,
    certification_class: str | None,
    impact_level: str | None,
    project_root: Path,
) -> Path:
    """Return the pinned compiled profile path for one bundled candidate."""
    if profile_id == "fisma_agency_security":
        raise AnalysisProfileError(
            "fisma_agency_security requires an explicit customer profile path"
        )
    if profile_id == "fedramp_20x_program":
        if certification_class == "B":
            raise AnalysisProfileError(
                "fedramp_20x_program Class B is not available as a bundled profile"
            )
        if certification_class != "C" or impact_level is not None:
            raise AnalysisProfileError(
                "fedramp_20x_program bundled profile requires certification_class C "
                "and impact_level null"
            )
    elif profile_id == "fedramp_rev5_transition":
        if certification_class is not None:
            raise AnalysisProfileError(
                "fedramp_rev5_transition bundled profile requires "
                "certification_class null"
            )
        if impact_level not in {"low", "moderate", "high"}:
            raise AnalysisProfileError(
                "fedramp_rev5_transition bundled profile requires impact_level "
                "low, moderate, or high"
            )
    else:
        raise AnalysisProfileError(f"unsupported bundled analysis profile id: {profile_id}")

    filename = _BUNDLED_PROFILE_FILENAMES[(profile_id, certification_class, impact_level)]
    return (project_root / _PROFILES_RELATIVE_DIR / filename).resolve()


def profile_fixture_path(*, profile_id: str, project_root: Path) -> Path:
    """Return the pinned contract fixture path for one supported analysis profile."""
    filename = _PROFILE_FIXTURE_FILENAMES.get(profile_id)
    if filename is None:
        raise AnalysisProfileError(f"unsupported analysis profile id: {profile_id}")
    return (project_root / _FIXTURES_RELATIVE_DIR / filename).resolve()


def default_fisma_synthetic_profile_path(*, project_root: Path) -> Path:
    """Return the pinned FISMA synthetic analysis profile fixture path."""
    return profile_fixture_path(
        profile_id="fisma_agency_security",
        project_root=project_root,
    )


def _default_authority_manifest_path(*, project_root: Path) -> Path:
    return (project_root / _DEFAULT_AUTHORITY_MANIFEST_RELATIVE_PATH).resolve()


def load_fisma_analysis_profile_reference(
    document: dict[str, Any] | None,
) -> FismaAnalysisProfileReference | None:
    """Return a configured FISMA analysis profile reference from runtime JSON, if present."""
    if document is None:
        return None
    reference = document.get("FISMA_ANALYSIS_PROFILE_FILE_REFERENCE")
    if reference is None:
        return None
    if not isinstance(reference, dict):
        raise AnalysisProfileError(
            "FISMA_ANALYSIS_PROFILE_FILE_REFERENCE must be an object"
        )
    path_raw = reference.get("path")
    digest_raw = reference.get("expected_sha256")
    if not isinstance(path_raw, str) or not path_raw.strip():
        raise AnalysisProfileError("analysis profile path is required")
    expected_sha256 = _normalize_expected_sha256(digest_raw)
    return FismaAnalysisProfileReference(
        path=Path(path_raw.strip()),
        expected_sha256=expected_sha256,
    )


def _normalize_expected_sha256(value: Any) -> str:
    if not isinstance(value, str) or not _SHA256_HEX_PATTERN.fullmatch(value):
        raise AnalysisProfileError(
            "analysis profile expected_sha256 must be a 64-character lowercase hex digest"
        )
    return value


def _resolve_explicit_customer_profile_path(profile_path: Path) -> Path:
    if "\0" in str(profile_path):
        raise AnalysisProfileError("analysis profile path is malformed")

    expanded = profile_path.expanduser()
    if expanded.is_symlink():
        raise AnalysisProfileError("analysis profile path must not be a symlink")
    resolved = expanded.resolve()
    if not resolved.is_file():
        raise AnalysisProfileError("analysis profile path must be a regular file")
    return resolved


def _require_profile_identity_match(
    document: dict[str, Any],
    *,
    profile_id: str,
    certification_class: str | None,
    impact_level: str | None,
) -> None:
    if document.get("profile_id") != profile_id:
        raise AnalysisProfileError("analysis profile id does not match request")
    if document.get("certification_class") != certification_class:
        raise AnalysisProfileError(
            "analysis profile certification_class does not match request"
        )
    if document.get("impact_level") != impact_level:
        raise AnalysisProfileError("analysis profile impact_level does not match request")


def load_pinned_profile(
    *,
    profile_id: str,
    project_root: Path,
    certification_class: str | None = None,
    impact_level: str | None = None,
    profile_path: Path | None = None,
    expected_sha256: str | None = None,
    require_qualified: bool = False,
    authority_manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Load, schema-validate, and semantically verify one analysis profile."""
    root = project_root.resolve()
    if profile_path is not None:
        if expected_sha256 is None:
            raise AnalysisProfileError(
                "analysis profile expected_sha256 is required for explicit profile paths"
            )
        resolved_profile_path = _resolve_explicit_customer_profile_path(profile_path)
        pinned_digest = _normalize_expected_sha256(expected_sha256)
    else:
        if expected_sha256 is not None:
            raise AnalysisProfileError(
                "analysis profile expected_sha256 must not be supplied for bundled profiles"
            )
        resolved_profile_path = bundled_profile_path(
            profile_id=profile_id,
            certification_class=certification_class,
            impact_level=impact_level,
            project_root=root,
        )
        pinned_digest = None

    try:
        raw_bytes = resolved_profile_path.read_bytes()
    except OSError as exc:
        raise AnalysisProfileError("analysis profile is unreadable") from exc

    if pinned_digest is not None:
        actual_digest = hashlib.sha256(raw_bytes).hexdigest()
        if actual_digest != pinned_digest:
            raise AnalysisProfileError("analysis profile digest mismatch")

    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalysisProfileError("analysis profile is unreadable") from exc

    if not isinstance(document, dict):
        raise AnalysisProfileError("analysis profile must be a JSON object")

    _require_profile_identity_match(
        document,
        profile_id=profile_id,
        certification_class=certification_class,
        impact_level=impact_level,
    )

    validator = _analysis_profile_validator(project_root=root)
    error = next(validator.iter_errors(document), None)
    if error is not None:
        raise AnalysisProfileError(
            f"analysis profile failed schema validation: {error.message}"
        )

    resolved_manifest_path = (
        authority_manifest_path.resolve()
        if authority_manifest_path is not None
        else _default_authority_manifest_path(project_root=root)
    )
    try:
        validate_analysis_profile_semantics(
            document,
            manifest_path=resolved_manifest_path,
            project_root=root,
        )
    except AnalysisProfileSemanticError as exc:
        raise AnalysisProfileError(str(exc)) from exc

    if require_qualified:
        if document.get("qualification_status") != "qualified":
            raise AnalysisProfileError(
                "analysis profile qualification_status must be qualified"
            )
        try:
            manifest = verify_authority_manifest(
                resolved_manifest_path,
                project_root=root,
            )
        except AuthorityManifestVerificationError as exc:
            raise AnalysisProfileError(str(exc)) from exc
        if manifest.get("status") != "approved":
            raise AnalysisProfileError(
                "qualified analysis profiles require an approved authority manifest"
            )

    return document


def load_runtime_profile(
    *,
    profile_id: str,
    certification_class: str | None,
    impact_level: str | None,
    project_root: Path,
    config: RuntimeConfig,
) -> dict[str, Any]:
    """Load the runtime-selected analysis profile for one package revision identity."""
    require_qualified = config.runtime_profile == "onprem_production"
    if profile_id == "fisma_agency_security":
        reference = load_fisma_analysis_profile_reference(config.document)
        if reference is None:
            raise AnalysisProfileError(
                "fisma_agency_security requires FISMA_ANALYSIS_PROFILE_FILE_REFERENCE "
                "in runtime config"
            )
        return load_pinned_profile(
            profile_id=profile_id,
            project_root=project_root,
            certification_class=certification_class,
            impact_level=impact_level,
            profile_path=reference.path,
            expected_sha256=reference.expected_sha256,
            require_qualified=require_qualified,
        )
    return load_pinned_profile(
        profile_id=profile_id,
        project_root=project_root,
        certification_class=certification_class,
        impact_level=impact_level,
        require_qualified=require_qualified,
    )


def load_pinned_fisma_synthetic_profile(*, project_root: Path) -> dict[str, Any]:
    """Load and schema-validate the pinned FISMA synthetic contract fixture."""
    profile_path = profile_fixture_path(
        profile_id="fisma_agency_security",
        project_root=project_root,
    )
    try:
        document = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisProfileError("pinned analysis profile is unreadable") from exc

    if document.get("profile_id") != "fisma_agency_security":
        raise AnalysisProfileError("pinned analysis profile id does not match request")

    validator = _analysis_profile_validator(project_root=project_root)
    error = next(validator.iter_errors(document), None)
    if error is not None:
        raise AnalysisProfileError(
            f"pinned analysis profile failed schema validation: {error.message}"
        )
    return document


def analysis_profile_sha256(profile: dict[str, Any]) -> str:
    """Return the SHA-256 digest of canonical profile JSON bytes."""
    return hashlib.sha256(canonical_json_bytes(profile)).hexdigest()


def expected_assessment_item_ids(profile: dict[str, Any]) -> tuple[str, ...]:
    """Return sorted unique assessment item identifiers from a profile."""
    items = profile.get("assessment_items")
    if not isinstance(items, list):
        raise AnalysisProfileError("assessment_items must be a list")
    identifiers: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            raise AnalysisProfileError("assessment item must be an object")
        identifier = item.get("assessment_item_id")
        if not isinstance(identifier, str) or not identifier:
            raise AnalysisProfileError("assessment_item_id must be a nonempty string")
        identifiers.append(identifier)
    return tuple(sorted(set(identifiers)))


def assessment_item_type_for_id(profile: dict[str, Any], assessment_item_id: str) -> str:
    """Return the assessment item type for one profile identifier."""
    for item in profile["assessment_items"]:
        if item["assessment_item_id"] == assessment_item_id:
            assessment_item_type = item["assessment_item_type"]
            if not isinstance(assessment_item_type, str):
                raise AnalysisProfileError("assessment_item_type must be a string")
            return assessment_item_type
    raise AnalysisProfileError(f"unknown assessment item id: {assessment_item_id}")
