"""Authenticated principal and object authorization helpers for package routes."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Protocol

from starlette.requests import Request

MAX_ACTOR_ID_LENGTH = 255
MAX_GROUP_ID_LENGTH = 255
MIN_CSRF_TOKEN_LENGTH = 32
MAX_CSRF_TOKEN_LENGTH = 512


class AuthenticationRequiredError(Exception):
    """Raised when a route requires an injected authenticated principal."""

    error_code = "authentication_required"


class CsrfValidationError(Exception):
    """Raised when CSRF token or Origin validation fails."""

    error_code = "csrf_validation_failed"


class AuthorizationDeniedError(Exception):
    """Raised when the principal lacks required object authorization."""

    error_code = "authorization_denied"


class _SystemAuthorizationTarget(Protocol):
    owner_group: str
    viewer_groups: list[str] | tuple[str, ...]


def _normalize_group_id(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("group id must be nonempty")
    if len(normalized) > MAX_GROUP_ID_LENGTH:
        raise ValueError("group id exceeds maximum length")
    return normalized


def _normalize_groups(groups: Any) -> tuple[str, ...]:
    if not isinstance(groups, (list, tuple)):
        raise ValueError("groups must be a sequence")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_group in groups:
        if not isinstance(raw_group, str):
            raise ValueError("group id must be a string")
        group = _normalize_group_id(raw_group)
        if group in seen:
            continue
        seen.add(group)
        normalized.append(group)
    if not normalized:
        raise ValueError("groups must be nonempty")
    return tuple(normalized)


def _normalize_origin(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("origin must be nonempty")
    return normalized


def _normalize_allowed_origins(origins: Any) -> tuple[str, ...]:
    if not isinstance(origins, (list, tuple)):
        raise ValueError("allowed_origins must be a sequence")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_origin in origins:
        if not isinstance(raw_origin, str):
            raise ValueError("origin must be a string")
        origin = _normalize_origin(raw_origin)
        if origin in seen:
            continue
        seen.add(origin)
        normalized.append(origin)
    if not normalized:
        raise ValueError("allowed_origins must be nonempty")
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Injected authenticated identity for package routes."""

    actor_id: str
    groups: tuple[str, ...]
    csrf_token: str
    allowed_origins: tuple[str, ...]

    def __post_init__(self) -> None:
        actor_id = self.actor_id.strip() if isinstance(self.actor_id, str) else ""
        if not actor_id:
            raise ValueError("actor_id must be nonempty")
        if len(actor_id) > MAX_ACTOR_ID_LENGTH:
            raise ValueError("actor_id exceeds maximum length")

        if (
            not isinstance(self.csrf_token, str)
            or len(self.csrf_token) < MIN_CSRF_TOKEN_LENGTH
            or len(self.csrf_token) > MAX_CSRF_TOKEN_LENGTH
        ):
            raise ValueError("csrf_token length is out of bounds")

        normalized_groups = _normalize_groups(self.groups)
        normalized_origins = _normalize_allowed_origins(self.allowed_origins)

        object.__setattr__(self, "actor_id", actor_id)
        object.__setattr__(self, "groups", normalized_groups)
        object.__setattr__(self, "allowed_origins", normalized_origins)

    def is_member_of(self, group: str) -> bool:
        normalized_group = _normalize_group_id(group)
        return normalized_group in self.groups

    def can_read_system(self, system: _SystemAuthorizationTarget) -> bool:
        if self.is_member_of(system.owner_group):
            return True
        return any(self.is_member_of(group) for group in system.viewer_groups)


def require_authenticated_principal(request: Request) -> AuthenticatedPrincipal:
    """Return the injected principal or raise authentication_required."""
    principal = getattr(request.state, "authenticated_principal", None)
    if not isinstance(principal, AuthenticatedPrincipal):
        raise AuthenticationRequiredError()
    return principal


def require_mutation_context(
    request: Request,
    x_csrf_token: str | None,
    origin: str | None,
) -> AuthenticatedPrincipal:
    """Validate CSRF and Origin for a mutating package request."""
    principal = require_authenticated_principal(request)
    if x_csrf_token is None or origin is None:
        raise CsrfValidationError()
    if not secrets.compare_digest(x_csrf_token, principal.csrf_token):
        raise CsrfValidationError()
    if origin not in principal.allowed_origins:
        raise CsrfValidationError()
    return principal


def require_system_read_access(
    principal: AuthenticatedPrincipal,
    system: _SystemAuthorizationTarget,
) -> None:
    """Require owner or viewer membership for read access."""
    if not principal.can_read_system(system):
        raise AuthorizationDeniedError()


def require_system_mutation_access(
    principal: AuthenticatedPrincipal,
    system: _SystemAuthorizationTarget,
) -> None:
    """Require owner membership for mutation access."""
    if not principal.is_member_of(system.owner_group):
        raise AuthorizationDeniedError()
