"""Reject and strip untrusted client-supplied identity headers at the API boundary."""

from __future__ import annotations

from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ato_service.problems import PROBLEM_MEDIA_TYPE, build_problem

UNTRUSTED_IDENTITY_HEADERS = frozenset(
    {
        "x-remote-user",
        "x-forwarded-user",
        "remote-user",
        "x-user",
        "x-user-id",
        "x-auth-request-user",
        "x-groups",
        "x-forwarded-groups",
    }
)


class UntrustedIdentityHeaderError(Exception):
    """Raised when a request carries non-empty untrusted identity headers."""

    error_code = "authorization_denied"


def _header_value(headers: list[tuple[bytes, bytes]], name: str) -> str | None:
    target = name.lower().encode("ascii")
    for key, value in headers:
        if key.lower() == target:
            try:
                decoded = value.decode("latin-1").strip()
            except UnicodeDecodeError:
                return None
            return decoded or None
    return None


def detect_untrusted_identity_headers(headers: list[tuple[bytes, bytes]]) -> tuple[str, ...]:
    """Return sorted header names that carry non-empty untrusted identity values."""
    detected: list[str] = []
    for header_name in sorted(UNTRUSTED_IDENTITY_HEADERS):
        if _header_value(headers, header_name) is not None:
            detected.append(header_name)
    return tuple(detected)


def strip_untrusted_identity_headers(headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """Return request headers with untrusted identity headers removed."""
    blocked = {name.encode("ascii") for name in UNTRUSTED_IDENTITY_HEADERS}
    return [
        (key, value)
        for key, value in headers
        if key.lower() not in blocked
    ]


def _problem_response(request: Request, *, error_code: str, status: int) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    if request_id is None:
        from uuid import uuid4

        request_id = uuid4()
    problem = build_problem(
        error_code=error_code,
        status=status,
        instance=request.url.path,
        request_id=request_id,
        retryable=False,
    )
    return JSONResponse(
        status_code=status,
        content=problem.model_dump(mode="json"),
        media_type=PROBLEM_MEDIA_TYPE,
    )


class IdentityHeaderGuardMiddleware(BaseHTTPMiddleware):
    """Strip spoofed identity headers and fail closed when values are present."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        detected = detect_untrusted_identity_headers(request.scope.get("headers", []))
        if detected:
            request.scope["headers"] = strip_untrusted_identity_headers(
                request.scope.get("headers", [])
            )
            request.state.untrusted_identity_headers_detected = detected
            return _problem_response(
                request,
                error_code=UntrustedIdentityHeaderError.error_code,
                status=403,
            )
        return await call_next(request)
