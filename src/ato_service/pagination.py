"""Opaque cursor pagination helpers."""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from ato_service.domain_mapping import format_uuid

CURSOR_VERSION = 1
DEFAULT_PAGE_LIMIT = 50
MIN_PAGE_LIMIT = 1
MAX_PAGE_LIMIT = 100
MAX_CURSOR_LENGTH = 2048
_MAX_CURSOR_JSON_BYTES = 512

_UUID_V4_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_UTC_DATETIME_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
_URLSAFE_BASE64_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class InvalidPaginationCursorError(Exception):
    """Raised when a pagination cursor is malformed."""

    error_code = "malformed_request"


class InvalidPageLimitError(Exception):
    """Raised when a page limit is outside the supported range."""

    error_code = "malformed_request"


@dataclass(frozen=True, slots=True)
class PaginationCursor:
    """Decoded pagination position keyed by created_at and item id."""

    created_at: datetime
    item_id: UUID


def validate_page_limit(limit: int | None) -> int:
    """Validate list page size with default 50 and bounds 1..100."""
    if limit is None:
        return DEFAULT_PAGE_LIMIT
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise InvalidPageLimitError()
    if limit < MIN_PAGE_LIMIT or limit > MAX_PAGE_LIMIT:
        raise InvalidPageLimitError()
    return limit


def _format_cursor_created_at(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")
    utc_value = value.astimezone(timezone.utc).replace(tzinfo=None)
    text = utc_value.isoformat(timespec="microseconds")
    if text.endswith("+00:00"):
        text = text[: -len("+00:00")]
    if text.endswith(".000000"):
        text = text[: -len(".000000")]
    return f"{text}Z"


def _parse_cursor_created_at(value: str) -> datetime:
    if not _UTC_DATETIME_PATTERN.fullmatch(value):
        raise ValueError("created_at must be a UTC datetime")
    date_part, time_part = value[:-1].split("T", maxsplit=1)
    year, month, day = (int(part) for part in date_part.split("-"))
    time_main, _, fraction = time_part.partition(".")
    hour, minute, second = (int(part) for part in time_main.split(":"))
    try:
        parsed = datetime(
            year,
            month,
            day,
            hour,
            minute,
            second,
            int(fraction.ljust(6, "0")[:6]) if fraction else 0,
            tzinfo=timezone.utc,
        )
    except ValueError:
        raise ValueError("created_at must be a valid UTC datetime") from None
    if (
        parsed.year != year
        or parsed.month != month
        or parsed.day != day
        or parsed.hour != hour
        or parsed.minute != minute
        or parsed.second != second
    ):
        raise ValueError("created_at must be a valid UTC datetime")
    if fraction:
        if len(fraction) > 6:
            raise ValueError("created_at fractional seconds exceed microsecond precision")
        if fraction != fraction.rstrip("0"):
            raise ValueError("created_at must use canonical fractional seconds")
    return parsed


def _validate_cursor_payload(payload: Any) -> PaginationCursor:
    if not isinstance(payload, dict):
        raise InvalidPaginationCursorError()
    if set(payload.keys()) != {"v", "created_at", "id"}:
        raise InvalidPaginationCursorError()
    version = payload.get("v")
    if type(version) is not int or version != CURSOR_VERSION:
        raise InvalidPaginationCursorError()
    created_at = payload.get("created_at")
    item_id = payload.get("id")
    if not isinstance(created_at, str) or not isinstance(item_id, str):
        raise InvalidPaginationCursorError()
    if not _UTC_DATETIME_PATTERN.fullmatch(created_at):
        raise InvalidPaginationCursorError()
    if not _UUID_V4_PATTERN.fullmatch(item_id):
        raise InvalidPaginationCursorError()
    try:
        parsed_created_at = _parse_cursor_created_at(created_at)
    except ValueError:
        raise InvalidPaginationCursorError() from None
    return PaginationCursor(
        created_at=parsed_created_at,
        item_id=UUID(item_id),
    )


def _decode_canonical_urlsafe_base64(cursor: str) -> bytes:
    if "=" in cursor:
        raise InvalidPaginationCursorError()
    if _URLSAFE_BASE64_CURSOR_PATTERN.fullmatch(cursor) is None:
        raise InvalidPaginationCursorError()
    padding = "=" * (-len(cursor) % 4)
    try:
        decoded = base64.urlsafe_b64decode((cursor + padding).encode("ascii"))
    except (binascii.Error, ValueError):
        raise InvalidPaginationCursorError() from None
    canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    if canonical != cursor:
        raise InvalidPaginationCursorError()
    return decoded


def encode_pagination_cursor(created_at: datetime, item_id: UUID) -> str:
    """Encode a stable opaque pagination cursor."""
    payload = {
        "v": CURSOR_VERSION,
        "created_at": _format_cursor_created_at(created_at),
        "id": format_uuid(item_id),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    return encoded.rstrip("=")


def decode_pagination_cursor(cursor: str) -> PaginationCursor:
    """Decode an opaque pagination cursor."""
    if not isinstance(cursor, str):
        raise InvalidPaginationCursorError()
    if not cursor or len(cursor) > MAX_CURSOR_LENGTH:
        raise InvalidPaginationCursorError()
    decoded = _decode_canonical_urlsafe_base64(cursor)
    if len(decoded) > _MAX_CURSOR_JSON_BYTES:
        raise InvalidPaginationCursorError()
    try:
        payload = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InvalidPaginationCursorError() from None
    return _validate_cursor_payload(payload)
