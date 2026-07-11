"""Tests for opaque cursor pagination helpers."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from uuid import UUID

import pytest

from ato_service.pagination import (
    DEFAULT_PAGE_LIMIT,
    InvalidPageLimitError,
    InvalidPaginationCursorError,
    PaginationCursor,
    decode_pagination_cursor,
    encode_pagination_cursor,
    validate_page_limit,
)

_ITEM_ID = UUID("44444444-4444-4444-8444-444444444444")
_CREATED_AT = datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc)


def test_validate_page_limit_defaults_and_bounds() -> None:
    assert validate_page_limit(None) == DEFAULT_PAGE_LIMIT
    assert validate_page_limit(1) == 1
    assert validate_page_limit(100) == 100


@pytest.mark.parametrize("limit", [0, 101, -1, True, "50"])
def test_validate_page_limit_rejects_invalid_values(limit: object) -> None:
    with pytest.raises(InvalidPageLimitError) as exc_info:
        validate_page_limit(limit)  # type: ignore[arg-type]

    assert exc_info.value.error_code == "malformed_request"


def test_encode_and_decode_pagination_cursor_round_trip() -> None:
    cursor = encode_pagination_cursor(_CREATED_AT, _ITEM_ID)
    decoded = decode_pagination_cursor(cursor)

    assert decoded == PaginationCursor(created_at=_CREATED_AT, item_id=_ITEM_ID)
    assert decode_pagination_cursor(cursor) == decoded


def test_encode_pagination_cursor_is_stable() -> None:
    first = encode_pagination_cursor(_CREATED_AT, _ITEM_ID)
    second = encode_pagination_cursor(_CREATED_AT, _ITEM_ID)

    assert first == second
    assert "=" not in first


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "not-base64!!!",
        base64.urlsafe_b64encode(b"not-json").decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(
            json.dumps({"v": 2, "created_at": "2026-07-10T20:00:00Z", "id": str(_ITEM_ID)}).encode(
                "utf-8"
            )
        )
        .decode("ascii")
        .rstrip("="),
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "v": 1,
                    "created_at": "2026-07-10T20:00:00+00:00",
                    "id": str(_ITEM_ID),
                }
            ).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("="),
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "v": 1,
                    "created_at": "2026-07-10T20:00:00Z",
                    "id": "not-a-uuid",
                }
            ).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("="),
        "A" * 2049,
    ],
)
def test_decode_pagination_cursor_rejects_malformed_values(cursor: str) -> None:
    with pytest.raises(InvalidPaginationCursorError) as exc_info:
        decode_pagination_cursor(cursor)

    assert exc_info.value.error_code == "malformed_request"


def test_decode_pagination_cursor_rejects_tampered_payload() -> None:
    cursor = encode_pagination_cursor(_CREATED_AT, _ITEM_ID)
    raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
    payload = json.loads(raw.decode("utf-8"))
    payload["forged"] = True
    tampered = (
        base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )

    with pytest.raises(InvalidPaginationCursorError):
        decode_pagination_cursor(tampered)
