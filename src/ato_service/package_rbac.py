"""Package-scoped RBAC on hardened OIDC principals (Component F)."""

from __future__ import annotations

from typing import Any, Protocol

from ato_service.auth_context import (
    AuthenticatedPrincipal,
    AuthorizationDeniedError,
    require_system_mutation_access,
    require_system_read_access,
)

DEFAULT_PACKAGE_ROLE_GROUPS: dict[str, tuple[str, ...]] = {
    "system_owner": ("owners", "system-owners"),
    "isso": ("isso", "owners"),
    "control_owner": ("control-owners", "owners"),
    "assessor": ("assessors",),
    "reviewer": ("reviewers", "owners"),
    "approver": ("approvers",),
    "ao_custodian": ("ao-custodians", "approvers"),
    "viewer": ("viewers", "owners"),
    "platform_admin": ("platform-admins",),
}

_PACKAGE_ROLE_GROUPS = dict(DEFAULT_PACKAGE_ROLE_GROUPS)


def configure_package_role_groups(document: dict[str, Any]) -> None:
    """Apply OIDC_GROUP_ROLE_MAPPING from runtime JSON when present."""
    global _PACKAGE_ROLE_GROUPS
    mapping = document.get("OIDC_GROUP_ROLE_MAPPING")
    if not isinstance(mapping, dict):
        _PACKAGE_ROLE_GROUPS = dict(DEFAULT_PACKAGE_ROLE_GROUPS)
        return
    resolved = dict(DEFAULT_PACKAGE_ROLE_GROUPS)
    for role, groups in mapping.items():
        if not isinstance(role, str) or role not in resolved:
            continue
        if not isinstance(groups, list) or not groups:
            continue
        normalized = tuple(
            value.strip()
            for value in groups
            if isinstance(value, str) and value.strip()
        )
        if normalized:
            resolved[role] = normalized
    _PACKAGE_ROLE_GROUPS = resolved


def package_role_groups() -> dict[str, tuple[str, ...]]:
    return dict(_PACKAGE_ROLE_GROUPS)


PACKAGE_ROLE_GROUPS = DEFAULT_PACKAGE_ROLE_GROUPS


class _SystemTarget(Protocol):
    owner_group: str
    viewer_groups: list[str] | tuple[str, ...]


class _RevisionTarget(Protocol):
    created_by: str


def principal_has_role(principal: AuthenticatedPrincipal, role: str) -> bool:
    groups = _PACKAGE_ROLE_GROUPS.get(role, ())
    return any(principal.is_member_of(group) for group in groups)


def require_package_role(
    principal: AuthenticatedPrincipal,
    *,
    system: _SystemTarget,
    revision: _RevisionTarget | None = None,
    role: str,
) -> None:
    """Default-deny package-scoped role check with owner/viewer migration compatibility."""
    if role == "viewer":
        require_system_read_access(principal, system)
        return
    if principal_has_role(principal, role):
        return
    if role in {"reviewer", "system_owner", "isso"} and principal.is_member_of(system.owner_group):
        return
    raise AuthorizationDeniedError()


def require_package_read(
    principal: AuthenticatedPrincipal,
    *,
    system: _SystemTarget,
) -> None:
    require_system_read_access(principal, system)


def require_package_mutation(
    principal: AuthenticatedPrincipal,
    *,
    system: _SystemTarget,
    role: str = "system_owner",
) -> None:
    if principal_has_role(principal, role) or principal.is_member_of(system.owner_group):
        return
    raise AuthorizationDeniedError()
