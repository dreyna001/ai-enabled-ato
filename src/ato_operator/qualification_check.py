"""Deterministic qualification corpus validation for ato-operator."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
import hashlib
import json
from pathlib import Path
import stat
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

_HASH_BLOCK_SIZE = 1024 * 1024
_FORMAT_CHECKER = FormatChecker()
_CORPUS_ROOT = "data/qualification"
_REQUIRED_PROFILES = (
    "fedramp_20x_program",
    "fedramp_rev5_transition",
    "fisma_agency_security",
)
_REQUIRED_SCENARIO_BEHAVIORS = (
    "idempotent_replay",
    "lease_recovery",
    "duplicate_detected",
    "crash_safe_resume",
)
_REQUIRED_HOSTILE_BEHAVIORS = (
    "refuse_injection",
    "reject_xxe",
    "parse_reject",
)


class QualificationCorpusError(ValueError):
    """Base error for qualification corpus validation."""


@dataclass(frozen=True, slots=True)
class QualificationCheckReport:
    passed: bool
    errors: tuple[str, ...]
    fixture_count: int
    profiles_covered: tuple[str, ...]
    hostile_fixture_count: int
    replay_fixture_count: int
    hard_stops_governed: tuple[str, ...]
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "errors": list(self.errors),
            "fixture_count": self.fixture_count,
            "profiles_covered": list(self.profiles_covered),
            "hostile_fixture_count": self.hostile_fixture_count,
            "replay_fixture_count": self.replay_fixture_count,
            "hard_stops_governed": list(self.hard_stops_governed),
            "note": self.note,
        }


def _find_project_root(start: Path | None = None) -> Path:
    candidate = (start or Path.cwd()).resolve()
    for path in (candidate, *candidate.parents):
        if (path / "pyproject.toml").is_file():
            return path
    raise QualificationCorpusError("Could not locate project root (pyproject.toml not found)")


@cache
def _load_schema(schema_path: Path) -> dict[str, Any]:
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _qualification_manifest_validator(
    *,
    schema_path: Path | None,
    project_root: Path,
) -> Draft202012Validator:
    resolved_schema_path = schema_path
    if resolved_schema_path is None:
        resolved_schema_path = (
            project_root / "docs" / "contracts" / "qualification-manifest.schema.json"
        )
    try:
        schema = _load_schema(resolved_schema_path.resolve())
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError, SchemaError) as exc:
        raise QualificationCorpusError(
            "qualification manifest schema is invalid or unreadable"
        ) from exc
    return Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as blob_file:
        while True:
            chunk = blob_file.read(_HASH_BLOCK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _format_schema_error(error: ValidationError) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    if path:
        return f"qualification manifest failed schema validation at {path}: {error.message}"
    return f"qualification manifest failed schema validation: {error.message}"


def _is_safe_relative_path(relative_path: str) -> bool:
    if not relative_path or relative_path.startswith("/"):
        return False
    parts = Path(relative_path).parts
    return ".." not in parts


def run_qualification_check(
    *,
    project_root: Path | None = None,
    manifest_path: Path | None = None,
    schema_path: Path | None = None,
) -> QualificationCheckReport:
    """Validate qualification manifest schema, path safety, digests, and coverage."""
    root = (project_root or _find_project_root()).resolve()
    resolved_manifest_path = (
        manifest_path or (root / _CORPUS_ROOT / "manifest.json")
    ).resolve()
    note = (
        "Corpus validation only; HS-001..009 remain governed by hard-stops.yaml "
        "and claim_metadata.closes_hard_stops must remain false."
    )

    try:
        manifest = json.loads(resolved_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return QualificationCheckReport(
            passed=False,
            errors=("qualification manifest is missing or unreadable",),
            fixture_count=0,
            profiles_covered=(),
            hostile_fixture_count=0,
            replay_fixture_count=0,
            hard_stops_governed=(),
            note=note,
        )

    if not isinstance(manifest, dict):
        return QualificationCheckReport(
            passed=False,
            errors=("qualification manifest must be a JSON object",),
            fixture_count=0,
            profiles_covered=(),
            hostile_fixture_count=0,
            replay_fixture_count=0,
            hard_stops_governed=(),
            note=note,
        )

    validator = _qualification_manifest_validator(schema_path=schema_path, project_root=root)
    validation_error = next(validator.iter_errors(manifest), None)
    if validation_error is not None:
        return QualificationCheckReport(
            passed=False,
            errors=(_format_schema_error(validation_error),),
            fixture_count=0,
            profiles_covered=(),
            hostile_fixture_count=0,
            replay_fixture_count=0,
            hard_stops_governed=tuple(manifest.get("hard_stops_governed", ())),
            note=note,
        )

    corpus_root = manifest["corpus_root"]
    if corpus_root != _CORPUS_ROOT:
        return QualificationCheckReport(
            passed=False,
            errors=(f"unsupported corpus_root {corpus_root!r}",),
            fixture_count=0,
            profiles_covered=(),
            hostile_fixture_count=0,
            replay_fixture_count=0,
            hard_stops_governed=tuple(manifest["hard_stops_governed"]),
            note=note,
        )

    corpus_dir = (root / corpus_root).resolve()
    try:
        corpus_dir.relative_to(root)
    except ValueError:
        return QualificationCheckReport(
            passed=False,
            errors=("corpus_root escapes repository",),
            fixture_count=0,
            profiles_covered=(),
            hostile_fixture_count=0,
            replay_fixture_count=0,
            hard_stops_governed=tuple(manifest["hard_stops_governed"]),
            note=note,
        )

    fixtures = manifest["fixtures"]
    errors: list[str] = []
    seen_paths: dict[str, str] = {}
    seen_fixture_ids: dict[str, str] = {}
    profiles_covered: set[str] = set()
    hostile_behaviors: set[str] = set()
    scenario_behaviors: set[str] = set()
    hostile_fixture_count = 0
    replay_fixture_count = 0

    for fixture in fixtures:
        fixture_id = fixture["fixture_id"]
        relative_path = fixture["relative_path"]

        if fixture_id in seen_fixture_ids:
            errors.append(
                f"duplicate fixture_id {fixture_id} "
                f"(paths {seen_fixture_ids[fixture_id]!r} and {relative_path!r})"
            )
        else:
            seen_fixture_ids[fixture_id] = relative_path

        if relative_path in seen_paths:
            errors.append(
                f"duplicate relative_path {relative_path!r} "
                f"(fixture_ids {seen_paths[relative_path]!r} and {fixture_id!r})"
            )
        else:
            seen_paths[relative_path] = fixture_id

        if not _is_safe_relative_path(relative_path):
            errors.append(f"path traversal or absolute path rejected: {relative_path!r}")
            continue

        fixture_path = (corpus_dir / relative_path).resolve()
        try:
            fixture_path.relative_to(corpus_dir)
        except ValueError:
            errors.append(f"fixture path escapes corpus_root: {relative_path!r}")
            continue

        try:
            artifact_stat = fixture_path.stat()
        except OSError:
            errors.append(f"missing fixture file: {relative_path!r}")
            continue

        if not stat.S_ISREG(artifact_stat.st_mode):
            errors.append(f"fixture is not a regular file: {relative_path!r}")
            continue

        if artifact_stat.st_size != fixture["size_bytes"]:
            errors.append(
                f"{relative_path!r} size_bytes {fixture['size_bytes']} "
                f"does not match actual {artifact_stat.st_size}"
            )

        try:
            actual_digest = _hash_file(fixture_path)
        except OSError:
            errors.append(f"fixture is unreadable: {relative_path!r}")
            continue

        if actual_digest != fixture["sha256"]:
            errors.append(
                f"{relative_path!r} sha256 mismatch "
                f"(expected {fixture['sha256']}, actual {actual_digest})"
            )

        if fixture["claim_metadata"]["closes_hard_stops"] is not False:
            errors.append(f"{fixture_id} claim_metadata.closes_hard_stops must be false")

        profile_id = fixture["profile_id"]
        split = fixture["split"]
        expected_behavior = fixture["expected_behavior"]

        if split == "qualification" and profile_id in _REQUIRED_PROFILES:
            profiles_covered.add(profile_id)

        if split == "hostile":
            hostile_fixture_count += 1
            hostile_behaviors.add(expected_behavior)

        if expected_behavior == "idempotent_replay":
            replay_fixture_count += 1

        if split == "scenario":
            scenario_behaviors.add(expected_behavior)

    for profile_id in _REQUIRED_PROFILES:
        if profile_id not in profiles_covered:
            errors.append(f"missing qualification fixture for profile {profile_id}")

    for behavior in _REQUIRED_HOSTILE_BEHAVIORS:
        if behavior not in hostile_behaviors:
            errors.append(f"missing hostile fixture with expected_behavior {behavior}")

    for behavior in _REQUIRED_SCENARIO_BEHAVIORS:
        if behavior not in scenario_behaviors:
            errors.append(f"missing scenario fixture with expected_behavior {behavior}")

    if not fixtures:
        errors.append("qualification corpus contains no fixtures")

    return QualificationCheckReport(
        passed=not errors,
        errors=tuple(errors),
        fixture_count=len(fixtures),
        profiles_covered=tuple(sorted(profiles_covered)),
        hostile_fixture_count=hostile_fixture_count,
        replay_fixture_count=replay_fixture_count,
        hard_stops_governed=tuple(manifest["hard_stops_governed"]),
        note=note,
    )


__all__ = ["QualificationCheckReport", "QualificationCorpusError", "run_qualification_check"]
