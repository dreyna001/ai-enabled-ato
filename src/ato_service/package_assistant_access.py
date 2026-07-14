"""Shared authorization and capability checks for package assistant routes."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.auth_context import AuthenticatedPrincipal, AuthorizationDeniedError
from ato_service.db.models import PackageRevision, System
from ato_service.process_capabilities import ProcessCapabilities, resolve_process_capabilities
from ato_service.runtime_config import RuntimeConfig


class CapabilityDisabledError(Exception):
    error_code = "capability_disabled"


class PackageRevisionAccessError(Exception):
    error_code = "resource_not_found"


async def load_authorized_package_revision(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    package_revision_id: uuid.UUID,
) -> tuple[PackageRevision, System]:
    """Load one revision after package/system read authorization without cross-revision leakage."""
    from ato_service.auth_context import require_system_read_access

    row = (
        await session.execute(
            select(PackageRevision, System)
            .join(System, System.system_id == PackageRevision.system_id)
            .where(PackageRevision.package_revision_id == package_revision_id)
        )
    ).one_or_none()
    if row is None:
        raise PackageRevisionAccessError()
    revision, system = row
    try:
        require_system_read_access(principal, system)
    except AuthorizationDeniedError as exc:
        raise PackageRevisionAccessError() from exc
    return revision, system


def require_process_capability(
    config: RuntimeConfig,
    *,
    capability: str,
) -> ProcessCapabilities | None:
    capabilities = resolve_process_capabilities(config.document)
    if capabilities is None:
        return None
    if not getattr(capabilities, capability):
        raise CapabilityDisabledError()
    return capabilities


__all__ = [
    "CapabilityDisabledError",
    "PackageRevisionAccessError",
    "load_authorized_package_revision",
    "require_process_capability",
]
