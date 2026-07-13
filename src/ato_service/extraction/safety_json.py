"""Strict JSON parsing with duplicate-key and depth rejection."""

from __future__ import annotations

import json
from typing import Any

from ato_service.extraction.errors import ExtractionError
from ato_service.extraction.limits import MAX_JSON_DEPTH


class _DuplicateKeyError(ValueError):
    pass


def _object_pairs_hook(pairs: list[tuple[str, Any]], *, depth: int) -> dict[str, Any]:
    if depth > MAX_JSON_DEPTH:
        raise _DuplicateKeyError("json depth exceeds limit")
    seen: set[str] = set()
    result: dict[str, Any] = {}
    for key, value in pairs:
        if not isinstance(key, str):
            raise _DuplicateKeyError("json object keys must be strings")
        if key in seen:
            raise _DuplicateKeyError(f"duplicate json object key: {key}")
        seen.add(key)
        result[key] = _normalize_json_value(value, depth=depth + 1)
    return result


def _normalize_json_value(value: Any, *, depth: int) -> Any:
    if depth > MAX_JSON_DEPTH:
        raise _DuplicateKeyError("json depth exceeds limit")
    if isinstance(value, dict):
        return _object_pairs_hook(list(value.items()), depth=depth)
    if isinstance(value, list):
        return [_normalize_json_value(item, depth=depth + 1) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise _DuplicateKeyError("json contains unsupported value type")


def parse_json_strict(text: str) -> Any:
    """Parse JSON rejecting duplicate keys and excessive depth."""
    try:
        return json.loads(text, object_pairs_hook=lambda pairs: _object_pairs_hook(pairs, depth=1))
    except _DuplicateKeyError as exc:
        message = str(exc)
        if "depth" in message:
            raise ExtractionError(message, error_code="package_limit_exceeded") from exc
        raise ExtractionError(message, error_code="source_parse_failed") from exc
    except json.JSONDecodeError as exc:
        raise ExtractionError("json parse failed", error_code="source_parse_failed") from exc


def encode_json_pointer(segment: str) -> str:
    """Encode one JSON pointer segment per RFC 6901."""
    return segment.replace("~", "~0").replace("/", "~1")


def build_json_pointer(path_segments: list[str]) -> str:
    """Build a domain-valid JSON pointer from key segments."""
    if not path_segments:
        return ""
    return "/" + "/".join(encode_json_pointer(segment) for segment in path_segments)


def iter_json_leaves(
    value: Any,
    *,
    path_segments: list[str] | None = None,
) -> list[tuple[str, Any]]:
    """Return leaf values with RFC 6901 JSON pointers."""
    segments = list(path_segments or [])
    leaves: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key in sorted(value):
            leaves.extend(
                iter_json_leaves(value[key], path_segments=[*segments, key]),
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            leaves.extend(
                iter_json_leaves(item, path_segments=[*segments, str(index)]),
            )
    else:
        leaves.append((build_json_pointer(segments), value))
    return leaves


def leaf_text(value: Any) -> str:
    """Serialize one JSON leaf to stable UTF-8 text."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, str):
        return value
    raise ExtractionError("json leaf has unsupported type", error_code="source_parse_failed")
