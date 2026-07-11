"""Generic secret-byte credential resolution."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any

CREDENTIALS_DIRECTORY_ENV_VAR = "CREDENTIALS_DIRECTORY"
_CREDENTIAL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_MAX_ROOT_OWNED_FILE_MODE = 0o640
_MAX_SECRET_BYTES = 64 * 1024


class CredentialResolutionError(Exception):
    """Raised when protected credential bytes cannot be resolved."""


def _validate_root_owned_file_metadata(path: Path) -> None:
    try:
        stat_result = path.lstat()
    except OSError as exc:
        raise CredentialResolutionError(
            "credential file metadata could not be verified"
        ) from exc

    if stat.S_ISLNK(stat_result.st_mode):
        raise CredentialResolutionError(
            "credential file must be a regular file, not a symlink"
        )
    if not stat.S_ISREG(stat_result.st_mode):
        raise CredentialResolutionError("credential file must be a regular file")

    if not hasattr(os, "getuid"):
        return

    if stat_result.st_uid != 0:
        raise CredentialResolutionError("credential file must be owned by root")

    mode = stat_result.st_mode & 0o777
    if mode & ~_MAX_ROOT_OWNED_FILE_MODE:
        raise CredentialResolutionError(
            "credential file permissions must be 0640 or stricter"
        )


def _validate_regular_file_metadata(path: Path) -> None:
    try:
        stat_result = path.lstat()
    except OSError as exc:
        raise CredentialResolutionError(
            "credential file metadata could not be verified"
        ) from exc

    if stat.S_ISLNK(stat_result.st_mode):
        raise CredentialResolutionError(
            "credential file must be a regular file, not a symlink"
        )
    if not stat.S_ISREG(stat_result.st_mode):
        raise CredentialResolutionError("credential file must be a regular file")


def read_secret_bytes_from_file(
    path: Path,
    *,
    enforce_root_owned_file_metadata: bool = False,
) -> bytes:
    """Read non-empty secret bytes from a protected file without decoding them."""
    if not path.is_absolute():
        raise CredentialResolutionError("credential file must be an absolute path")

    if enforce_root_owned_file_metadata:
        _validate_root_owned_file_metadata(path)
    else:
        _validate_regular_file_metadata(path)

    try:
        secret_bytes = path.read_bytes()
    except OSError as exc:
        raise CredentialResolutionError("credential file must be readable") from exc

    if not secret_bytes:
        raise CredentialResolutionError("credential file must be non-empty")

    if len(secret_bytes) > _MAX_SECRET_BYTES:
        raise CredentialResolutionError("credential file exceeds maximum secret size")

    return secret_bytes


def _validate_credential_reference(reference: object) -> dict[str, Any]:
    if not isinstance(reference, dict):
        raise CredentialResolutionError(
            "credential reference must be a credential reference object"
        )

    source = reference.get("source")
    if source == "systemd_credential":
        identifier = reference.get("identifier")
        if not isinstance(identifier, str) or not identifier.strip():
            raise CredentialResolutionError(
                "credential reference systemd_credential requires an identifier"
            )
        if not _CREDENTIAL_IDENTIFIER_PATTERN.fullmatch(identifier):
            raise CredentialResolutionError("credential reference identifier is malformed")
        return reference

    if source == "root_owned_file":
        path_raw = reference.get("path")
        if not isinstance(path_raw, str) or not path_raw.strip():
            raise CredentialResolutionError(
                "credential reference root_owned_file requires a path"
            )
        return reference

    raise CredentialResolutionError(
        "credential reference has an unsupported or malformed source"
    )


def _resolve_systemd_credential_path(identifier: str) -> Path:
    cred_dir_raw = os.environ.get(CREDENTIALS_DIRECTORY_ENV_VAR)
    if not cred_dir_raw or not cred_dir_raw.strip():
        raise CredentialResolutionError(
            f"{CREDENTIALS_DIRECTORY_ENV_VAR} must be set to resolve systemd credentials"
        )

    cred_dir = Path(cred_dir_raw.strip())
    if not cred_dir.is_absolute():
        raise CredentialResolutionError(
            f"{CREDENTIALS_DIRECTORY_ENV_VAR} must be an absolute path"
        )

    credential_path = (cred_dir / identifier).resolve()
    cred_root = cred_dir.resolve()
    try:
        credential_path.relative_to(cred_root)
    except ValueError as exc:
        raise CredentialResolutionError(
            "systemd credential path must remain within the credentials directory"
        ) from exc

    return credential_path


def resolve_secret_bytes_from_credential_reference(
    reference: dict[str, Any],
    *,
    enforce_root_owned_file_metadata: bool = False,
) -> bytes:
    """Resolve secret bytes from a runtime credential reference."""
    validated = _validate_credential_reference(reference)
    source = validated["source"]

    if source == "root_owned_file":
        return read_secret_bytes_from_file(
            Path(str(validated["path"]).strip()),
            enforce_root_owned_file_metadata=enforce_root_owned_file_metadata,
        )

    identifier = str(validated["identifier"]).strip()
    return read_secret_bytes_from_file(_resolve_systemd_credential_path(identifier))
