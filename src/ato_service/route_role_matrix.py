"""Deterministic per-route package role matrix (Phase 4 / EP-06)."""

from __future__ import annotations

from typing import Final

from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.package_rbac import require_any_package_role, require_package_role

# Normative roles referenced by the published API contract.
ROLE_VIEWER: Final = "viewer"
ROLE_SYSTEM_OWNER: Final = "system_owner"
ROLE_ISSO: Final = "isso"
ROLE_CONTROL_OWNER: Final = "control_owner"
ROLE_ASSESSOR: Final = "assessor"
ROLE_REVIEWER: Final = "reviewer"
ROLE_APPROVER: Final = "approver"
ROLE_AO_CUSTODIAN: Final = "ao_custodian"

_PACKAGE_OWNER_MUTATION = (ROLE_SYSTEM_OWNER, ROLE_ISSO)
_EVIDENCE_UPLOAD = (ROLE_SYSTEM_OWNER, ROLE_CONTROL_OWNER)
_PROPOSAL_REVIEW = (ROLE_SYSTEM_OWNER, ROLE_ISSO)
_RUN_START = (ROLE_SYSTEM_OWNER, ROLE_ASSESSOR)
_APPROVAL_DECISION = (ROLE_APPROVER, ROLE_AO_CUSTODIAN)
_AUTHORIZATION_ATTACH = (ROLE_AO_CUSTODIAN, ROLE_ISSO)

ROUTE_ROLE_MATRIX: dict[tuple[str, str], tuple[str, ...]] = {
    ("GET", "/systems"): (ROLE_VIEWER,),
    ("POST", "/systems"): (ROLE_SYSTEM_OWNER,),
    ("GET", "/systems/{system_id}"): (ROLE_VIEWER,),
    ("POST", "/systems/{system_id}/archive"): (ROLE_SYSTEM_OWNER,),
    ("POST", "/systems/{system_id}/package-revisions"): _PACKAGE_OWNER_MUTATION,
    ("GET", "/systems/{system_id}/package-revisions"): (ROLE_VIEWER,),
    ("GET", "/package-revisions/{id}"): (ROLE_VIEWER,),
    ("PATCH", "/package-revisions/{id}"): _PACKAGE_OWNER_MUTATION,
    ("POST", "/package-revisions/{id}/files"): _EVIDENCE_UPLOAD,
    ("POST", "/package-revisions/{id}/finalize"): _PACKAGE_OWNER_MUTATION,
    ("GET", "/package-revisions/{id}/draft"): (ROLE_VIEWER,),
    ("GET", "/package-revisions/{id}/intake-report"): (ROLE_VIEWER,),
    ("PUT", "/package-revisions/{id}/draft"): _PACKAGE_OWNER_MUTATION,
    ("POST", "/package-revisions/{id}/confirm"): _PACKAGE_OWNER_MUTATION,
    ("GET", "/package-revisions/{id}/proposals"): (ROLE_VIEWER,),
    ("POST", "/proposals/{id}/accept"): _PROPOSAL_REVIEW,
    ("POST", "/proposals/{id}/reject"): _PROPOSAL_REVIEW,
    ("POST", "/package-revisions/{id}/runs"): _RUN_START,
    ("GET", "/package-revisions/{id}/runs"): (ROLE_VIEWER,),
    ("GET", "/runs/{run_id}"): (ROLE_VIEWER,),
    ("POST", "/runs/{run_id}/cancel"): (ROLE_SYSTEM_OWNER,),
    ("GET", "/runs/{run_id}/matrix"): (ROLE_VIEWER,),
    ("GET", "/runs/{run_id}/artifacts"): (ROLE_VIEWER,),
    ("POST", "/runs/{run_id}/review-revisions"): (ROLE_REVIEWER,),
    ("POST", "/review-revisions/{id}/submit"): (ROLE_REVIEWER,),
    ("PATCH", "/review-revisions/{id}/dispositions/{row_id}"): (ROLE_REVIEWER,),
    ("POST", "/review-revisions/{id}/comments"): (ROLE_REVIEWER,),
    ("GET", "/review-revisions/{id}/comments"): (ROLE_VIEWER,),
    ("POST", "/review-revisions/{id}/export-drafts"): (ROLE_REVIEWER,),
    ("POST", "/export-drafts/{id}/submit"): (ROLE_REVIEWER,),
    ("POST", "/approvals/{id}/approve"): _APPROVAL_DECISION,
    ("POST", "/approvals/{id}/reject"): _APPROVAL_DECISION,
    ("GET", "/exports/{id}/download"): (ROLE_VIEWER,),
    ("POST", "/systems/{system_id}/authorization-decisions"): _AUTHORIZATION_ATTACH,
    ("GET", "/systems/{system_id}/authorization-decisions"): (ROLE_VIEWER,),
    ("GET", "/package-revisions/{id}/preflight"): (ROLE_VIEWER,),
    ("GET", "/package-revisions/{id}/delta"): (ROLE_VIEWER,),
    ("GET", "/package-revisions/{id}/search"): (ROLE_VIEWER,),
    ("POST", "/package-revisions/{id}/chat"): (ROLE_VIEWER,),
}

PUBLISHED_ROUTE_ROLE_MATRIX: tuple[tuple[str, str, tuple[str, ...]], ...] = tuple(
    sorted(
        (method, path, roles)
        for (method, path), roles in ROUTE_ROLE_MATRIX.items()
    )
)


class _SystemTarget:
    def __init__(self, *, owner_group: str, viewer_groups: list[str] | tuple[str, ...]) -> None:
        self.owner_group = owner_group
        self.viewer_groups = viewer_groups


class _RevisionTarget:
    def __init__(self, *, created_by: str) -> None:
        self.created_by = created_by


def route_roles(*, method: str, path: str) -> tuple[str, ...]:
    """Return the normative roles for a published route or raise KeyError."""
    return ROUTE_ROLE_MATRIX[(method.upper(), path)]


def enforce_route_roles(
    principal: AuthenticatedPrincipal,
    *,
    method: str,
    path: str,
    system: _SystemTarget,
    revision: _RevisionTarget | None = None,
) -> None:
    """Enforce the published role matrix for one route against one package scope."""
    roles = route_roles(method=method, path=path)
    if len(roles) == 1:
        require_package_role(
            principal,
            system=system,
            revision=revision,
            role=roles[0],
        )
        return
    require_any_package_role(
        principal,
        system=system,
        revision=revision,
        roles=roles,
    )
