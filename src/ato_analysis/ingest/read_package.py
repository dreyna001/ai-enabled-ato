"""Read and parse incoming evidence package files."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ato_analysis.config import Settings

ALLOWED_EXTENSIONS = frozenset({".json", ".txt"})
_PATH_TRAVERSAL_PATTERN = re.compile(r"(?:\.\.|[/\\])")


class PackageReadError(ValueError):
    """Raised when an incoming package file fails boundary checks."""


def _reject_path_traversal(name: str, *, label: str = "filename") -> None:
    if not name or _PATH_TRAVERSAL_PATTERN.search(name):
        raise PackageReadError(f"Invalid {label}: path traversal or empty name rejected")


def read_package_file(path: Path, settings: Settings) -> tuple[bytes, str]:
    """Read raw bytes and return the normalized extension (e.g. ``.json``)."""
    resolved = path.resolve()
    _reject_path_traversal(path.name)

    suffix = path.suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise PackageReadError(
            f"Unsupported file type {suffix!r}; allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    if not resolved.is_file():
        raise PackageReadError(f"Package file not found: {resolved}")

    size = resolved.stat().st_size
    if size > settings.max_input_file_bytes:
        raise PackageReadError(
            f"File size {size} exceeds MAX_INPUT_FILE_BYTES ({settings.max_input_file_bytes})"
        )

    return resolved.read_bytes(), suffix


def find_incoming_package(package_id: str, settings: Settings) -> Path:
    """Locate ``<package_id>.json`` or ``<package_id>.txt`` under the incoming dir."""
    _reject_path_traversal(package_id, label="package_id")

    for suffix in (".json", ".txt"):
        candidate = settings.incoming_dir / f"{package_id}{suffix}"
        if candidate.is_file():
            return candidate.resolve()

    raise FileNotFoundError(
        f"No incoming package for {package_id!r} in {settings.incoming_dir} "
        f"(expected {package_id}.json or {package_id}.txt)"
    )


def parse_raw_json_or_text(raw: bytes) -> dict[str, object] | str:
    """Parse JSON when possible; otherwise return decoded text for LLM normalize."""
    text = raw.decode("utf-8")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text

    if not isinstance(parsed, dict):
        raise PackageReadError(
            f"JSON root must be an object, got {type(parsed).__name__}"
        )
    return parsed
