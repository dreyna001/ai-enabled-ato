"""Immutable validation drill record schema, redaction, and append-only writer."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from ato_service.runtime_config import RuntimeConfig

SCHEMA_VERSION = "1.0.0"
RECORDS_SUBDIR = "records"
TEMP_PREFIX = "drill-record-staging-"
_STAGING_TOKEN_BYTES = 16

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(authorization|auth_header|password|secret|token|api_key|credential|dsn|"
    r"client_secret|hmac_key|private_key|cookie|session_id|bearer)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"Basic\s+\S+", re.IGNORECASE),
    re.compile(r"Authorization:\s*Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"postgresql(\+\w+)?://\S+", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]+-----[\s\S]*?-----END [A-Z ]+-----"),
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
)


class DrillRecordError(ValueError):
    """Raised when drill record validation or persistence fails."""


class DrillPathError(OSError):
    """Raised when drill record paths are unsafe."""


class DrillOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    INVALID = "invalid"


class HardStopClaimStatus(StrEnum):
    NOT_CLAIMED = "not_claimed"
    STILL_OPEN = "still_open"
    VERIFIED_CLOSED = "verified_closed"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class HardStopClaim:
    hard_stop_id: str
    claim_status: str
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "hard_stop_id": self.hard_stop_id,
            "claim_status": self.claim_status,
        }
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


def _contracts_dir(*, project_root: Path) -> Path:
    return project_root / "docs" / "contracts"


@cache
def _drill_record_validator(*, project_root: Path) -> Draft202012Validator:
    schema_path = (
        _contracts_dir(project_root=project_root)
        / "validation-drill-record.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_drill_record_schema(
    document: dict[str, Any],
    *,
    project_root: Path,
) -> None:
    """Validate ``document`` against the published drill record schema."""
    validator = _drill_record_validator(project_root=project_root)
    errors = sorted(validator.iter_errors(document), key=lambda item: item.path)
    if errors:
        raise DrillRecordError(errors[0].message)


def contains_sensitive_material(value: Any) -> bool:
    """Return whether ``value`` includes forbidden secret or auth material."""
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and _SENSITIVE_KEY_PATTERN.search(key):
                return True
            if contains_sensitive_material(nested):
                return True
        return False
    if isinstance(value, list):
        return any(contains_sensitive_material(item) for item in value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return False
        for pattern in _SENSITIVE_VALUE_PATTERNS:
            if pattern.search(stripped):
                return True
    return False


def redact_drill_value(value: Any) -> Any:
    """Return a redacted copy safe for drill record persistence."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, nested in value.items():
            if isinstance(key, str) and _SENSITIVE_KEY_PATTERN.search(key):
                redacted[key] = "[REDACTED]"
                continue
            redacted[key] = redact_drill_value(nested)
        return redacted
    if isinstance(value, list):
        return [redact_drill_value(item) for item in value]
    if isinstance(value, str):
        redacted_value = value
        for pattern in _SENSITIVE_VALUE_PATTERNS:
            redacted_value = pattern.sub("[REDACTED]", redacted_value)
        return redacted_value
    return value


def canonical_record_bytes(document: dict[str, Any]) -> bytes:
    """Return canonical UTF-8 bytes for digest binding (excluding record_digest)."""
    payload = {key: value for key, value in document.items() if key != "record_digest"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_record_digest(document: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 digest for a drill record document."""
    return hashlib.sha256(canonical_record_bytes(document)).hexdigest()


def bind_record_digest(document: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``document`` with ``record_digest`` bound to canonical bytes."""
    bound = deepcopy(document)
    bound.pop("record_digest", None)
    bound["record_digest"] = compute_record_digest(bound)
    return bound


def validate_drill_record_semantics(
    document: dict[str, Any],
    *,
    project_root: Path,
) -> None:
    """Validate schema, digest binding, and redaction safety."""
    validate_drill_record_schema(document, project_root=project_root)
    expected = compute_record_digest(document)
    actual = document.get("record_digest")
    if not isinstance(actual, str) or actual != expected:
        raise DrillRecordError("record_digest does not match canonical document bytes")
    if contains_sensitive_material(document.get("results")):
        raise DrillRecordError(
            "results contain sensitive material that must be redacted"
        )


def _validate_path_part(part: str) -> str:
    if (
        not isinstance(part, str)
        or not part
        or part in {".", ".."}
        or "/" in part
        or "\\" in part
        or "\x00" in part
        or Path(part).is_absolute()
        or Path(part).name != part
    ):
        raise DrillPathError("drill record path part is invalid")
    return part


def _resolve_records_root(records_root: Path) -> Path:
    try:
        resolved = records_root.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise DrillPathError("records root could not be resolved") from exc
    return resolved


def drill_record_path(
    records_root: Path,
    *,
    drill_id: str,
    record_id: str,
) -> Path:
    """Return the normalized append-only path for one drill record."""
    root = _resolve_records_root(records_root)
    safe_drill_id = _validate_path_part(drill_id)
    safe_record_id = _validate_path_part(record_id)
    candidate = root / RECORDS_SUBDIR / safe_drill_id / f"{safe_record_id}.json"
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise DrillPathError("drill record path escapes records root") from exc
    return resolved


def _staging_path(records_root: Path) -> Path:
    root = _resolve_records_root(records_root)
    temp_dir = root / "_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    name = f"{TEMP_PREFIX}{secrets.token_hex(_STAGING_TOKEN_BYTES)}"
    validated_name = _validate_path_part(name)
    candidate = temp_dir / validated_name
    if candidate.parent != temp_dir:
        raise DrillPathError("generated staging path is invalid")
    return candidate


def _fsync_directory(path: Path) -> None:
    """Persist a renamed directory entry on the Linux production target."""
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_drill_record(
    records_root: Path,
    document: dict[str, Any],
    *,
    project_root: Path,
) -> Path:
    """Append one validated drill record using atomic write-once semantics."""
    redacted = bind_record_digest(redact_drill_value(deepcopy(document)))
    validate_drill_record_semantics(redacted, project_root=project_root)

    record_id = redacted.get("record_id")
    drill_id = redacted.get("drill_id")
    if not isinstance(record_id, str) or not isinstance(drill_id, str):
        raise DrillRecordError("record_id and drill_id are required")

    final_path = drill_record_path(records_root, drill_id=drill_id, record_id=record_id)
    if final_path.exists():
        raise DrillRecordError(f"drill record already exists: {final_path.name}")

    final_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path = _staging_path(records_root)
    payload = json.dumps(redacted, indent=2, sort_keys=True) + "\n"

    try:
        with staging_path.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging_path, final_path)
        staging_path = None
        _fsync_directory(final_path.parent)
    finally:
        if staging_path is not None and staging_path.exists():
            staging_path.unlink(missing_ok=True)

    return final_path


def read_drill_record(path: Path, *, project_root: Path) -> dict[str, Any]:
    """Load and validate one drill record from disk."""
    document = json.loads(path.read_text(encoding="utf-8"))
    validate_drill_record_semantics(document, project_root=project_root)
    return document


def list_drill_record_paths(
    records_root: Path,
    *,
    drill_id: str | None = None,
) -> list[Path]:
    """List persisted drill record paths in deterministic order."""
    root = _resolve_records_root(records_root)
    records_dir = root / RECORDS_SUBDIR
    if not records_dir.is_dir():
        return []

    if drill_id is not None:
        safe_drill_id = _validate_path_part(drill_id)
        target = records_dir / safe_drill_id
        if not target.is_dir():
            return []
        return sorted(target.glob("*.json"))

    paths: list[Path] = []
    for drill_dir in sorted(records_dir.iterdir()):
        if drill_dir.is_dir():
            paths.extend(sorted(drill_dir.glob("*.json")))
    return paths


def compute_config_digest(config: RuntimeConfig) -> str:
    """Hash normalized runtime JSON with credential references only."""
    document = deepcopy(config.document)
    for key in list(document):
        if key.endswith("_CREDENTIAL_REFERENCE"):
            reference = document[key]
            if isinstance(reference, dict):
                document[key] = {
                    "identifier": reference.get("identifier"),
                    "source": reference.get("source"),
                }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_application_digest(*, project_root: Path) -> str:
    """Hash bounded application identity bytes for drill binding."""
    paths = (
        project_root / "pyproject.toml",
        project_root / "docs" / "contracts" / "validation-drill-record.schema.json",
    )
    hasher = hashlib.sha256()
    for path in paths:
        hasher.update(path.name.encode("utf-8"))
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def format_utc_timestamp(value: datetime) -> str:
    normalized = value.astimezone(UTC)
    text = normalized.strftime("%Y-%m-%dT%H:%M:%S.%f")
    return f"{text}Z"


def build_drill_record(
    *,
    record_id: str,
    drill_id: str,
    drill_version: str,
    environment_type: str,
    execution_mode: str,
    started_at: datetime,
    completed_at: datetime,
    application_digest: str,
    config_digest: str,
    fixture_digest: str | None,
    operator_identifier: str,
    approver_identifier: str | None,
    outcome: str,
    hard_stop_claims: tuple[HardStopClaim, ...],
    results: dict[str, Any],
) -> dict[str, Any]:
    """Build a schema-compatible drill record with bound digest."""
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "record_id": record_id,
        "drill_id": drill_id,
        "drill_version": drill_version,
        "environment_type": environment_type,
        "execution_mode": execution_mode,
        "started_at_utc": format_utc_timestamp(started_at),
        "completed_at_utc": format_utc_timestamp(completed_at),
        "application_digest": application_digest,
        "config_digest": config_digest,
        "fixture_digest": fixture_digest,
        "operator_identifier": operator_identifier,
        "approver_identifier": approver_identifier,
        "outcome": outcome,
        "hard_stop_claims": [claim.to_dict() for claim in hard_stop_claims],
        "results": redact_drill_value(results),
    }
    return bind_record_digest(document)
