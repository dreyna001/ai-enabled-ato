"""Object-scope authorization and cross-system guessing tests."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from ato_service.auth_context import AuthenticatedPrincipal, AuthorizationDeniedError
from ato_service.object_authorization import authorize_package_revision_read
from ato_service.package_revisions import PackageRevisionNotFoundError

PACKAGE_REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
SYSTEM_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


class _System:
    def __init__(self) -> None:
        self.system_id = SYSTEM_ID
        self.owner_group = "secret-owners"
        self.viewer_groups = ["secret-viewers"]


class _Revision:
    def __init__(self) -> None:
        self.package_revision_id = PACKAGE_REVISION_ID
        self.system_id = SYSTEM_ID
        self.created_by = "owner@example.test"


def _principal(*, groups: tuple[str, ...]) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        actor_id="outsider@example.test",
        groups=groups,
        csrf_token="c" * 32,
        allowed_origins=("https://portal.example",),
    )


def _run(awaitable):
    return asyncio.run(awaitable)


def test_authorize_package_revision_read_returns_not_found_for_missing_revision() -> None:
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )

    with pytest.raises(PackageRevisionNotFoundError):
        _run(
            authorize_package_revision_read(
                session,
                principal=_principal(groups=("secret-viewers",)),
                package_revision_id=PACKAGE_REVISION_ID,
                not_found_error=PackageRevisionNotFoundError,
            )
        )


def test_authorize_package_revision_read_denies_without_leaking_owner_groups() -> None:
    revision = _Revision()
    system = _System()
    session = AsyncMock()
    call_count = {"value": 0}

    async def _execute(_stmt):
        call_count["value"] += 1
        if call_count["value"] == 1:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=revision))
        return MagicMock(scalar_one_or_none=MagicMock(return_value=system))

    session.execute = AsyncMock(side_effect=_execute)

    with pytest.raises(AuthorizationDeniedError) as exc_info:
        _run(
            authorize_package_revision_read(
                session,
                principal=_principal(groups=("public",)),
                package_revision_id=PACKAGE_REVISION_ID,
                not_found_error=PackageRevisionNotFoundError,
            )
        )

    message = str(exc_info.value)
    assert exc_info.value.error_code == "authorization_denied"
    assert "secret-owners" not in message
    assert "secret-viewers" not in message


def test_authorize_package_revision_read_allows_viewer_membership() -> None:
    revision = _Revision()
    system = _System()
    session = AsyncMock()
    call_count = {"value": 0}

    async def _execute(_stmt):
        call_count["value"] += 1
        if call_count["value"] == 1:
            return MagicMock(scalar_one_or_none=MagicMock(return_value=revision))
        return MagicMock(scalar_one_or_none=MagicMock(return_value=system))

    session.execute = AsyncMock(side_effect=_execute)

    scope = _run(
        authorize_package_revision_read(
            session,
            principal=_principal(groups=("secret-viewers",)),
            package_revision_id=PACKAGE_REVISION_ID,
            not_found_error=PackageRevisionNotFoundError,
        )
    )

    assert scope.package_revision is revision
    assert scope.system is system
