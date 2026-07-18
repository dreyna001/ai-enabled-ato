"""Package-scoped RBAC on hardened OIDC principals (Component F)."""

from __future__ import annotations

from typing import Any, Protocol

from ato_service.auth_context import (
    AuthenticatedPrincipal,
    AuthorizationDeniedError,
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


def _extend_groups(*groups: tuple[str, ...]) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for group_tuple in groups:
        for group in group_tuple:
            if group not in seen:
                seen.add(group)
                merged.append(group)
    return tuple(merged)


def _apply_single_user_role_mapping(resolved: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    """Grant approver roles to configured owner/reviewer IdP groups in single-user mode."""
    owner_groups = resolved.get("system_owner", ())
    reviewer_groups = resolved.get("reviewer", ())
    approver_groups = _extend_groups(
        resolved.get("approver", ()),
        owner_groups,
        reviewer_groups,
    )
    updated = dict(resolved)
    updated["approver"] = approver_groups
    updated["ao_custodian"] = _extend_groups(
        resolved.get("ao_custodian", ()),
        approver_groups,
    )
    return updated


def configure_package_role_groups(document: dict[str, Any]) -> None:
    """Apply OIDC_GROUP_ROLE_MAPPING from runtime JSON when present."""
    global _PACKAGE_ROLE_GROUPS
    mapping = document.get("OIDC_GROUP_ROLE_MAPPING")
    if not isinstance(mapping, dict):
        resolved = dict(DEFAULT_PACKAGE_ROLE_GROUPS)
    else:
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
    if document.get("SINGLE_USER_MODE_ENABLED") is True:
        resolved = _apply_single_user_role_mapping(resolved)
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
    require_any_package_role(principal, system=system, role=role)


def require_any_package_role(
    principal: AuthenticatedPrincipal,
    *,
    system: _SystemTarget,
    revision: _RevisionTarget | None = None,
    roles: tuple[str, ...] | None = None,
    role: str | None = None,
) -> None:
    """Default-deny check that succeeds when any listed package role is satisfied."""
    resolved_roles = roles if roles is not None else ((role,) if role is not None else ())
    if not resolved_roles:
        raise AuthorizationDeniedError()
    last_error: AuthorizationDeniedError | None = None
    for candidate in resolved_roles:
        try:
            require_package_role(
                principal,
                system=system,
                revision=revision,
                role=candidate,
            )
            return
        except AuthorizationDeniedError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise AuthorizationDeniedError()
