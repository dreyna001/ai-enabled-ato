"""JSON pointer and strict parsing helpers for normalize_proposal."""

from __future__ import annotations

import json
import re
from typing import Any

from ato_service.extraction.safety_json import parse_json_strict

_JSON_POINTER_PATTERN = re.compile(r"^(/([^~/]|~[01])*)*$")


class NormalizeJsonError(ValueError):
    """Raised when JSON input cannot be parsed safely."""


def parse_response_json(text: str) -> Any:
    """Parse model JSON rejecting duplicate keys."""
    try:
        return parse_json_strict(text)
    except Exception as exc:
        raise NormalizeJsonError(str(exc)) from exc


def is_valid_json_pointer(pointer: str) -> bool:
    return len(pointer) <= 2000 and _JSON_POINTER_PATTERN.fullmatch(pointer) is not None


def value_at_json_pointer(document: dict[str, Any], pointer: str) -> Any:
    current: Any = document
    if pointer == "":
        return document
    for raw_part in pointer.lstrip("/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current[part]
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            raise KeyError(pointer)
    return current


def set_json_pointer(document: dict[str, Any], pointer: str, value: Any) -> None:
    if pointer == "":
        raise ValueError("cannot set the empty JSON pointer")
    parts = pointer.lstrip("/").split("/")
    current: Any = document
    for index, raw_part in enumerate(parts):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        is_last = index == len(parts) - 1
        if is_last:
            if isinstance(current, dict):
                current[part] = value
            elif isinstance(current, list):
                current[int(part)] = value
            else:
                raise KeyError(pointer)
            return
        if isinstance(current, dict):
            next_value = current.get(part)
            if next_value is None:
                next_value = {}
                current[part] = next_value
            current = next_value
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            raise KeyError(pointer)


def stable_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
