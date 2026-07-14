"""Identity header guard middleware tests."""

from __future__ import annotations

import asyncio
import uuid

from starlette.requests import Request

from ato_service.identity_header_guard import (
    IdentityHeaderGuardMiddleware,
    detect_untrusted_identity_headers,
    strip_untrusted_identity_headers,
)


def _run(awaitable):
    return asyncio.run(awaitable)


def _request(*, headers: list[tuple[str, str]] | None = None) -> Request:
    encoded = [
        (key.lower().encode("ascii"), value.encode("ascii"))
        for key, value in (headers or [])
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/systems",
        "headers": encoded,
    }
    request = Request(scope)
    request.state.request_id = uuid.uuid4()
    return request


def test_detect_untrusted_identity_headers_ignores_empty_values() -> None:
    headers = [(b"x-remote-user", b""), (b"x-user-id", b"   ")]
    assert detect_untrusted_identity_headers(headers) == ()


def test_detect_untrusted_identity_headers_finds_spoofed_values() -> None:
    headers = [(b"x-user-id", b"spoofed-actor"), (b"x-groups", b"admins")]
    assert detect_untrusted_identity_headers(headers) == ("x-groups", "x-user-id")


def test_strip_untrusted_identity_headers_removes_known_headers() -> None:
    headers = [
        (b"x-user-id", b"spoofed"),
        (b"authorization", b"Bearer secret"),
        (b"x-csrf-token", b"token"),
    ]
    stripped = strip_untrusted_identity_headers(headers)
    assert (b"x-user-id", b"spoofed") not in stripped
    assert (b"authorization", b"Bearer secret") in stripped


def test_identity_header_guard_rejects_spoofed_headers() -> None:
    middleware = IdentityHeaderGuardMiddleware(app=object())  # type: ignore[arg-type]
    request = _request(headers=[("X-User-Id", "spoofed-actor")])

    async def call_next(_request: Request):
        raise AssertionError("downstream handler must not run")

    response = _run(middleware.dispatch(request, call_next))
    assert response.status_code == 403
