"""Shared object-scope authorization loaders for package routes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.auth_context import (
    AuthenticatedPrincipal,
    AuthorizationDeniedError,
    require_system_read_access,
)
from ato_service.package_rbac import require_any_package_role, require_package_role
from ato_service.route_role_matrix import ROLE_VIEWER


@dataclass(frozen=True, slots=True)
class PackageRevisionScope:
    package_revision: Any
    system: Any


@dataclass(frozen=True, slots=True)
class AnalysisRunScope:
    analysis_run: Any
    package_revision: Any
    system: Any


@dataclass(frozen=True, slots=True)
class ReviewRevisionScope:
    review_revision: Any
    analysis_run: Any
    package_revision: Any
    system: Any


def _deny_without_leakage() -> AuthorizationDeniedError:
    return AuthorizationDeniedError()


async def load_package_revision_scope(
    session: AsyncSession,
    *,
    package_revision_id: uuid.UUID,
    not_found_error: type[Exception],
) -> PackageRevisionScope:
    from ato_service.db.models import PackageRevision, System

    def _raise_not_found() -> None:
        try:
            raise not_found_error(package_revision_id=package_revision_id)
        except TypeError:
            raise not_found_error() from None

    revision_result = await session.execute(
        select(PackageRevision).where(PackageRevision.package_revision_id == package_revision_id)
    )
    package_revision = revision_result.scalar_one_or_none()
    if package_revision is None:
        _raise_not_found()
    system_result = await session.execute(
        select(System).where(System.system_id == package_revision.system_id)
    )
    system = system_result.scalar_one_or_none()
    if system is None:
        _raise_not_found()
    return PackageRevisionScope(package_revision=package_revision, system=system)


async def authorize_package_revision_read(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    package_revision_id: uuid.UUID,
    not_found_error: type[Exception],
) -> PackageRevisionScope:
    scope = await load_package_revision_scope(
        session,
        package_revision_id=package_revision_id,
        not_found_error=not_found_error,
    )
    try:
        require_package_role(
            principal,
            system=scope.system,
            revision=scope.package_revision,
            role=ROLE_VIEWER,
        )
    except AuthorizationDeniedError:
        raise _deny_without_leakage() from None
    return scope


async def authorize_package_revision_roles(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    package_revision_id: uuid.UUID,
    roles: tuple[str, ...],
    not_found_error: type[Exception],
) -> PackageRevisionScope:
    scope = await load_package_revision_scope(
        session,
        package_revision_id=package_revision_id,
        not_found_error=not_found_error,
    )
    try:
        if len(roles) == 1:
            require_package_role(
                principal,
                system=scope.system,
                revision=scope.package_revision,
                role=roles[0],
            )
        else:
            require_any_package_role(
                principal,
                system=scope.system,
                revision=scope.package_revision,
                roles=roles,
            )
    except AuthorizationDeniedError:
        raise _deny_without_leakage() from None
    return scope


async def load_analysis_run_scope(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    not_found_error: type[Exception],
) -> AnalysisRunScope:
    from ato_service.db.models import AnalysisRun, PackageRevision, System

    run_result = await session.execute(select(AnalysisRun).where(AnalysisRun.run_id == run_id))
    analysis_run = run_result.scalar_one_or_none()
    if analysis_run is None:
        raise not_found_error()
    revision_result = await session.execute(
        select(PackageRevision).where(
            PackageRevision.package_revision_id == analysis_run.package_revision_id
        )
    )
    package_revision = revision_result.scalar_one_or_none()
    if package_revision is None:
        raise not_found_error()
    system_result = await session.execute(
        select(System).where(System.system_id == package_revision.system_id)
    )
    system = system_result.scalar_one_or_none()
    if system is None:
        raise not_found_error()
    return AnalysisRunScope(
        analysis_run=analysis_run,
        package_revision=package_revision,
        system=system,
    )


async def authorize_analysis_run_read(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    run_id: uuid.UUID,
    not_found_error: type[Exception],
) -> AnalysisRunScope:
    scope = await load_analysis_run_scope(
        session,
        run_id=run_id,
        not_found_error=not_found_error,
    )
    try:
        require_system_read_access(principal, scope.system)
    except AuthorizationDeniedError:
        raise _deny_without_leakage() from None
    return scope


async def load_review_revision_scope(
    session: AsyncSession,
    *,
    review_revision_id: uuid.UUID,
    not_found_error: type[Exception],
) -> ReviewRevisionScope:
    from ato_service.db.models import ReviewRevision

    review_result = await session.execute(
        select(ReviewRevision).where(ReviewRevision.review_revision_id == review_revision_id)
    )
    review_revision = review_result.scalar_one_or_none()
    if review_revision is None:
        raise not_found_error()
    run_scope = await load_analysis_run_scope(
        session,
        run_id=review_revision.run_id,
        not_found_error=not_found_error,
    )
    return ReviewRevisionScope(
        review_revision=review_revision,
        analysis_run=run_scope.analysis_run,
        package_revision=run_scope.package_revision,
        system=run_scope.system,
    )


async def authorize_review_revision_read(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    review_revision_id: uuid.UUID,
    not_found_error: type[Exception],
) -> ReviewRevisionScope:
    scope = await load_review_revision_scope(
        session,
        review_revision_id=review_revision_id,
        not_found_error=not_found_error,
    )
    try:
        require_package_role(
            principal,
            system=scope.system,
            revision=scope.package_revision,
            role=ROLE_VIEWER,
        )
    except AuthorizationDeniedError:
        raise _deny_without_leakage() from None
    return scope
