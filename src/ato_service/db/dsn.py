"""Protected database DSN resolution helpers."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any

from ato_service.db.session import DatabaseConfigurationError, require_postgresql_url

DATABASE_DSN_FILE_ENV_VAR = "ATO_DATABASE_DSN_FILE"
CREDENTIALS_DIRECTORY_ENV_VAR = "CREDENTIALS_DIRECTORY"
_CREDENTIAL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_MAX_ROOT_OWNED_FILE_MODE = 0o640


class DatabaseDsnError(DatabaseConfigurationError):
    """Raised when a protected database DSN cannot be resolved."""


def _validate_root_owned_file_metadata(path: Path) -> None:
    try:
        stat_result = path.lstat()
    except OSError as exc:
        raise DatabaseDsnError(
            "database DSN file metadata could not be verified"
        ) from exc

    if stat.S_ISLNK(stat_result.st_mode):
        raise DatabaseDsnError(
            "database DSN file must be a regular file, not a symlink"
        )
    if not stat.S_ISREG(stat_result.st_mode):
        raise DatabaseDsnError("database DSN file must be a regular file")

    if not hasattr(os, "getuid"):
        return

    if stat_result.st_uid != 0:
        raise DatabaseDsnError("database DSN file must be owned by root")

    mode = stat_result.st_mode & 0o777
    if mode & ~_MAX_ROOT_OWNED_FILE_MODE:
        raise DatabaseDsnError(
            "database DSN file permissions must be 0640 or stricter"
        )


def read_database_dsn_from_file(
    path: Path,
    *,
    enforce_root_owned_file_metadata: bool = False,
) -> str:
    """Read and validate a PostgreSQL DSN from a protected UTF-8 file."""
    if not path.is_absolute():
        raise DatabaseDsnError(
            f"database DSN file must be an absolute path; got {path!s}"
        )

    if enforce_root_owned_file_metadata:
        _validate_root_owned_file_metadata(path)

    try:
        dsn = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise DatabaseDsnError(
            f"database DSN file must be readable; got {path!s}"
        ) from exc

    if not dsn:
        raise DatabaseDsnError(
            f"database DSN file must be non-empty; got {path!s}"
        )

    try:
        return require_postgresql_url(dsn)
    except DatabaseConfigurationError as exc:
        raise DatabaseDsnError(str(exc)) from exc


def require_database_dsn_from_env(
    env_var: str = DATABASE_DSN_FILE_ENV_VAR,
) -> str:
    """Resolve a PostgreSQL DSN from the protected-file environment variable."""
    raw = os.environ.get(env_var)
    if not raw or not raw.strip():
        raise DatabaseDsnError(
            f"{env_var} must be set to an absolute path "
            "to a UTF-8 file containing the database DSN"
        )
    return read_database_dsn_from_file(Path(raw.strip()))


def _validate_credential_reference(reference: object) -> dict[str, Any]:
    if not isinstance(reference, dict):
        raise DatabaseDsnError(
            "DATABASE_DSN_CREDENTIAL_REFERENCE must be a credential reference object"
        )

    source = reference.get("source")
    if source == "systemd_credential":
        identifier = reference.get("identifier")
        if not isinstance(identifier, str) or not identifier.strip():
            raise DatabaseDsnError(
                "DATABASE_DSN_CREDENTIAL_REFERENCE systemd_credential "
                "requires an identifier"
            )
        if not _CREDENTIAL_IDENTIFIER_PATTERN.fullmatch(identifier):
            raise DatabaseDsnError(
                "DATABASE_DSN_CREDENTIAL_REFERENCE identifier is malformed"
            )
        return reference

    if source == "root_owned_file":
        path_raw = reference.get("path")
        if not isinstance(path_raw, str) or not path_raw.strip():
            raise DatabaseDsnError(
                "DATABASE_DSN_CREDENTIAL_REFERENCE root_owned_file requires a path"
            )
        return reference

    raise DatabaseDsnError(
        "DATABASE_DSN_CREDENTIAL_REFERENCE has an unsupported or malformed source"
    )


def _resolve_systemd_credential_path(identifier: str) -> Path:
    cred_dir_raw = os.environ.get(CREDENTIALS_DIRECTORY_ENV_VAR)
    if not cred_dir_raw or not cred_dir_raw.strip():
        raise DatabaseDsnError(
            f"{CREDENTIALS_DIRECTORY_ENV_VAR} must be set to resolve systemd credentials"
        )

    cred_dir = Path(cred_dir_raw.strip())
    if not cred_dir.is_absolute():
        raise DatabaseDsnError(
            f"{CREDENTIALS_DIRECTORY_ENV_VAR} must be an absolute path"
        )

    return cred_dir / identifier


def resolve_database_dsn_from_credential_reference(
    reference: dict[str, Any],
    *,
    enforce_root_owned_file_metadata: bool = False,
) -> str:
    """Resolve a PostgreSQL DSN from a runtime credential reference."""
    validated = _validate_credential_reference(reference)
    source = validated["source"]

    if source == "root_owned_file":
        return read_database_dsn_from_file(
            Path(str(validated["path"]).strip()),
            enforce_root_owned_file_metadata=enforce_root_owned_file_metadata,
        )

    identifier = str(validated["identifier"]).strip()
    return read_database_dsn_from_file(_resolve_systemd_credential_path(identifier))
