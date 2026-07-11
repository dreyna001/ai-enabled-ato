"""PostgreSQL persistence foundation for durable ATO domain state.

The ``jobs`` table is intentionally omitted: persistent job status and attempt
semantics remain unresolved in the technical specification (Section 20).
"""

from ato_service.db.base import Base
from ato_service.db.models import (
    AnalysisRun,
    AuditEvent,
    FactProposal,
    IdempotencyRecord,
    PackageRevision,
    RunStep,
    SourceArtifact,
    System,
)
from ato_service.db.dsn import (
    DATABASE_DSN_FILE_ENV_VAR,
    DatabaseDsnError,
    read_database_dsn_from_file,
    require_database_dsn_from_env,
    resolve_database_dsn_from_credential_reference,
)
from ato_service.db.session import (
    DatabaseConfigurationError,
    create_async_engine_from_url,
    create_session_factory,
    probe_database_connectivity,
    require_postgresql_url,
)

__all__ = [
    "AnalysisRun",
    "AuditEvent",
    "Base",
    "DATABASE_DSN_FILE_ENV_VAR",
    "DatabaseConfigurationError",
    "DatabaseDsnError",
    "FactProposal",
    "IdempotencyRecord",
    "PackageRevision",
    "RunStep",
    "SourceArtifact",
    "System",
    "create_async_engine_from_url",
    "create_session_factory",
    "probe_database_connectivity",
    "read_database_dsn_from_file",
    "require_database_dsn_from_env",
    "require_postgresql_url",
    "resolve_database_dsn_from_credential_reference",
]
