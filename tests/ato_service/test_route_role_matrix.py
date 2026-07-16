"""Deterministic route role matrix contract tests."""

from __future__ import annotations

import pytest

from ato_service.route_role_matrix import (
    PUBLISHED_ROUTE_ROLE_MATRIX,
    ROUTE_ROLE_MATRIX,
    route_roles,
)


def test_route_role_matrix_covers_all_published_package_routes() -> None:
    expected = {
        ("GET", "/systems"),
        ("POST", "/systems"),
        ("GET", "/systems/{system_id}"),
        ("POST", "/systems/{system_id}/package-revisions"),
        ("GET", "/systems/{system_id}/package-revisions"),
        ("GET", "/package-revisions/{id}"),
        ("POST", "/package-revisions/{id}/files"),
        ("POST", "/package-revisions/{id}/finalize"),
        ("GET", "/package-revisions/{id}/draft"),
        ("PUT", "/package-revisions/{id}/draft"),
        ("POST", "/package-revisions/{id}/confirm"),
        ("GET", "/package-revisions/{id}/proposals"),
        ("POST", "/proposals/{id}/accept"),
        ("POST", "/proposals/{id}/reject"),
        ("POST", "/package-revisions/{id}/runs"),
        ("GET", "/package-revisions/{id}/runs"),
        ("GET", "/runs/{run_id}"),
        ("POST", "/runs/{run_id}/cancel"),
        ("GET", "/runs/{run_id}/matrix"),
        ("GET", "/runs/{run_id}/artifacts"),
        ("POST", "/runs/{run_id}/review-revisions"),
        ("POST", "/review-revisions/{id}/submit"),
        ("PATCH", "/review-revisions/{id}/dispositions/{row_id}"),
        ("POST", "/review-revisions/{id}/comments"),
        ("GET", "/review-revisions/{id}/comments"),
        ("POST", "/review-revisions/{id}/export-drafts"),
        ("POST", "/export-drafts/{id}/submit"),
        ("POST", "/approvals/{id}/approve"),
        ("POST", "/approvals/{id}/reject"),
        ("GET", "/exports/{id}/download"),
        ("POST", "/systems/{system_id}/authorization-decisions"),
        ("GET", "/systems/{system_id}/authorization-decisions"),
        ("GET", "/package-revisions/{id}/preflight"),
        ("GET", "/package-revisions/{id}/delta"),
        ("GET", "/package-revisions/{id}/search"),
        ("POST", "/package-revisions/{id}/chat"),
    }
    assert set(ROUTE_ROLE_MATRIX) == expected
    assert len(PUBLISHED_ROUTE_ROLE_MATRIX) == len(expected)


@pytest.mark.parametrize(
    ("method", "path", "roles"),
    PUBLISHED_ROUTE_ROLE_MATRIX,
)
def test_route_roles_are_nonempty_and_known(method: str, path: str, roles: tuple[str, ...]) -> None:
    assert roles
    assert route_roles(method=method, path=path) == roles


def test_approval_routes_require_approver_or_ao_custodian() -> None:
    approve_roles = route_roles(method="POST", path="/approvals/{id}/approve")
    reject_roles = route_roles(method="POST", path="/approvals/{id}/reject")
    assert approve_roles == ("approver", "ao_custodian")
    assert reject_roles == approve_roles
