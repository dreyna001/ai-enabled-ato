"""Tests for authenticated principal and authorization helpers."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from starlette.requests import Request

from ato_service.auth_context import (
    AuthenticationRequiredError,
    AuthenticatedPrincipal,
    AuthorizationDeniedError,
    CsrfValidationError,
    require_authenticated_principal,
    require_mutation_context,
    require_system_mutation_access,
    require_system_read_access,
)


def _principal(
    *,
    actor_id: str = "actor-1",
    groups: tuple[str, ...] = ("owners",),
    csrf_token: str = "a" * 32,
    allowed_origins: tuple[str, ...] = ("https://portal.example",),
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        actor_id=actor_id,
        groups=groups,
        csrf_token=csrf_token,
        allowed_origins=allowed_origins,
    )


def _request(
    principal: AuthenticatedPrincipal | None = None,
    *,
    x_user_id: str | None = "header-actor",
    x_groups: str | None = "header-group",
) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/systems",
        "headers": [],
    }
    if x_user_id is not None:
        scope["headers"].append((b"x-user-id", x_user_id.encode()))
    if x_groups is not None:
        scope["headers"].append((b"x-groups", x_groups.encode()))
    request = Request(scope)
    if principal is not None:
        request.state.authenticated_principal = principal
    return request


@dataclass
class _System:
    owner_group: str
    viewer_groups: list[str]


def test_authenticated_principal_normalizes_groups_and_origins() -> None:
    principal = AuthenticatedPrincipal(
        actor_id=" actor-1 ",
        groups=(" owners ", "owners", " viewers "),
        csrf_token="b" * 40,
        allowed_origins=(" https://portal.example ", "https://portal.example"),
    )

    assert principal.actor_id == "actor-1"
    assert principal.groups == ("owners", "viewers")
    assert principal.allowed_origins == ("https://portal.example",)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"actor_id": ""},
        {"actor_id": "x" * 256},
        {"groups": ()},
        {"groups": [" "]},
        {"csrf_token": "short"},
        {"csrf_token": "x" * 513},
        {"allowed_origins": ()},
        {"allowed_origins": [" "]},
    ],
)
def test_authenticated_principal_rejects_invalid_fields(kwargs: dict) -> None:
    base = {
        "actor_id": "actor-1",
        "groups": ("owners",),
        "csrf_token": "c" * 32,
        "allowed_origins": ("https://portal.example",),
    }
    base.update(kwargs)
    with pytest.raises(ValueError):
        AuthenticatedPrincipal(**base)


def test_require_authenticated_principal_reads_only_request_state() -> None:
    principal = _principal()
    request = _request(principal)

    assert require_authenticated_principal(request) is principal


def test_require_authenticated_principal_ignores_identity_headers() -> None:
    request = _request(None, x_user_id="spoofed", x_groups="admins")

    with pytest.raises(AuthenticationRequiredError) as exc_info:
        require_authenticated_principal(request)

    assert exc_info.value.error_code == "authentication_required"


def test_require_mutation_context_validates_csrf_and_origin() -> None:
    principal = _principal(csrf_token="d" * 32)
    request = _request(principal)

    assert (
        require_mutation_context(
            request,
            "d" * 32,
            "https://portal.example",
        )
        is principal
    )


@pytest.mark.parametrize(
    ("csrf_token", "origin"),
    [
        (None, "https://portal.example"),
        ("d" * 32, None),
        ("wrong-token" + ("e" * 24), "https://portal.example"),
        ("d" * 32, "https://evil.example"),
        ("d" * 32, "https://portal.example/"),
    ],
)
def test_require_mutation_context_rejects_invalid_csrf_or_origin(
    csrf_token: str | None,
    origin: str | None,
) -> None:
    principal = _principal(csrf_token="d" * 32)
    request = _request(principal)

    with pytest.raises(CsrfValidationError) as exc_info:
        require_mutation_context(request, csrf_token, origin)

    assert exc_info.value.error_code == "csrf_validation_failed"


def test_require_mutation_context_uses_constant_time_csrf_compare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal = _principal(csrf_token="f" * 32)
    request = _request(principal)
    calls: list[tuple[str, str]] = []

    def _record_compare(a: str, b: str) -> bool:
        calls.append((a, b))
        return a == b

    monkeypatch.setattr("ato_service.auth_context.secrets.compare_digest", _record_compare)

    require_mutation_context(request, "f" * 32, "https://portal.example")

    assert calls == [("f" * 32, "f" * 32)]


def test_system_read_and_mutation_authorization() -> None:
    system = _System(owner_group="owners", viewer_groups=["viewers"])
    owner = _principal(groups=("owners",))
    viewer = _principal(groups=("viewers",))
    outsider = _principal(groups=("other",))

    require_system_read_access(owner, system)
    require_system_read_access(viewer, system)
    require_system_mutation_access(owner, system)

    with pytest.raises(AuthorizationDeniedError) as read_exc:
        require_system_read_access(outsider, system)
    with pytest.raises(AuthorizationDeniedError) as mutation_exc:
        require_system_mutation_access(viewer, system)

    assert read_exc.value.error_code == "authorization_denied"
    assert mutation_exc.value.error_code == "authorization_denied"
    assert "owners" not in str(read_exc.value)
    assert "viewers" not in str(mutation_exc.value)


def test_authorization_errors_do_not_include_object_details() -> None:
    system = _System(owner_group="secret-owner-group", viewer_groups=["secret-viewers"])
    outsider = _principal(groups=("public",))

    with pytest.raises(AuthorizationDeniedError) as exc_info:
        require_system_read_access(outsider, system)

    message = str(exc_info.value)
    assert "secret-owner-group" not in message
    assert "secret-viewers" not in message
