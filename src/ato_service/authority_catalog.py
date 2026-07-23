"""Authority catalog helpers for manifest sources and JSON pointer resolution."""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

_JSON_POINTER_PATTERN = re.compile(r"^(/([^~/]|~[01])*)*$")
_AUTHORITY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
_WINDOWS_DRIVE_PREFIX_PATTERN = re.compile(r"^[A-Za-z]:")
_MAX_ARCHIVE_MEMBER_UNCOMPRESSED_BYTES = 64 * 1024 * 1024


class AuthorityCatalogError(ValueError):
    """Raised when authority catalog lookup or JSON pointer resolution fails."""


def authority_sources_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return manifest sources keyed by authority_id."""
    sources = manifest.get("sources")
    if not isinstance(sources, list):
        raise AuthorityCatalogError("authority manifest must include a sources list")

    by_id: dict[str, dict[str, Any]] = {}
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise AuthorityCatalogError(
                f"authority manifest source at index {index} must be an object"
            )

        authority_id = source.get("authority_id")
        if not isinstance(authority_id, str) or not authority_id:
            raise AuthorityCatalogError(
                f"authority manifest source at index {index} must declare authority_id"
            )
        if _AUTHORITY_ID_PATTERN.fullmatch(authority_id) is None:
            raise AuthorityCatalogError(
                f"authority manifest source at index {index} has malformed authority_id"
            )
        if authority_id in by_id:
            raise AuthorityCatalogError(
                f"duplicate authority_id {authority_id!r} in authority manifest"
            )
        by_id[authority_id] = source

    return by_id


def load_json_authority_source(
    *,
    manifest: dict[str, Any],
    authority_id: str,
    project_root: Path,
) -> dict[str, Any]:
    """Load a vendored JSON authority artifact referenced by the manifest."""
    source = _lookup_manifest_source(manifest=manifest, authority_id=authority_id)
    authority_path = _resolve_authority_artifact_path(
        source=source,
        authority_id=authority_id,
        project_root=project_root,
    )

    if authority_path.suffix.lower() != ".json":
        raise AuthorityCatalogError(
            f"{authority_id} local_path must reference a .json file"
        )

    artifact_bytes = _read_verified_authority_artifact_bytes(
        authority_path=authority_path,
        source=source,
        authority_id=authority_id,
    )
    return _parse_json_object(
        artifact_bytes,
        authority_id=authority_id,
        artifact_description="authority artifact",
    )


def load_json_authority_archive_member(
    *,
    manifest: dict[str, Any],
    authority_id: str,
    project_root: Path,
    member_suffix: str,
) -> tuple[str, dict[str, Any]]:
    """Load one verified JSON object from a pinned ZIP authority artifact."""
    _validate_archive_member_suffix(member_suffix)

    source = _lookup_manifest_source(manifest=manifest, authority_id=authority_id)
    authority_path = _resolve_authority_artifact_path(
        source=source,
        authority_id=authority_id,
        project_root=project_root,
    )

    if authority_path.suffix.lower() != ".zip":
        raise AuthorityCatalogError(
            f"{authority_id} local_path must reference a .zip file"
        )

    artifact_bytes = _read_verified_authority_artifact_bytes(
        authority_path=authority_path,
        source=source,
        authority_id=authority_id,
    )
    return _load_json_from_verified_zip_bytes(
        artifact_bytes,
        authority_id=authority_id,
        member_suffix=member_suffix,
    )


def resolve_json_pointer(document: Any, pointer: str) -> Any:
    """Resolve an RFC 6901 JSON pointer against document."""
    if pointer == "":
        return document
    if _JSON_POINTER_PATTERN.fullmatch(pointer) is None:
        raise AuthorityCatalogError(f"malformed JSON pointer {pointer!r}")

    current: Any = document
    for raw_token in pointer[1:].split("/"):
        token = _decode_json_pointer_token(raw_token)
        if isinstance(current, dict):
            if token not in current:
                raise AuthorityCatalogError(
                    f"JSON pointer {pointer!r} missing object key {token!r}"
                )
            current = current[token]
            continue
        if isinstance(current, list):
            if token == "-":
                raise AuthorityCatalogError(
                    f"JSON pointer {pointer!r} cannot use '-' as an array index"
                )
            if not token.isdigit() or (len(token) > 1 and token[0] == "0"):
                raise AuthorityCatalogError(
                    f"JSON pointer {pointer!r} has invalid array index {token!r}"
                )
            index = int(token)
            if index >= len(current):
                raise AuthorityCatalogError(
                    f"JSON pointer {pointer!r} array index {index} is out of range"
                )
            current = current[index]
            continue
        raise AuthorityCatalogError(
            f"JSON pointer {pointer!r} traverses a non-container value"
        )

    return current


def _lookup_manifest_source(
    *,
    manifest: dict[str, Any],
    authority_id: str,
) -> dict[str, Any]:
    if not isinstance(authority_id, str) or not authority_id:
        raise AuthorityCatalogError("authority_id is required")

    sources_by_id = authority_sources_by_id(manifest)
    source = sources_by_id.get(authority_id)
    if source is None:
        raise AuthorityCatalogError(
            f"unknown authority_id {authority_id!r} in authority manifest"
        )
    return source


def _resolve_authority_artifact_path(
    *,
    source: dict[str, Any],
    authority_id: str,
    project_root: Path,
) -> Path:
    local_path = source.get("local_path")
    if not isinstance(local_path, str) or not local_path:
        raise AuthorityCatalogError(
            f"{authority_id} must declare local_path for JSON authority loading"
        )

    root = project_root.resolve()
    authority_path = (root / local_path).resolve()
    try:
        authority_path.relative_to(root)
    except ValueError as error:
        raise AuthorityCatalogError(
            f"{authority_id} local_path escapes project root"
        ) from error
    return authority_path


def _read_verified_authority_artifact_bytes(
    *,
    authority_path: Path,
    source: dict[str, Any],
    authority_id: str,
) -> bytes:
    try:
        artifact_stat = authority_path.stat()
    except OSError as error:
        local_path = source.get("local_path")
        raise AuthorityCatalogError(
            f"missing authority file for {authority_id}: {local_path}"
        ) from error

    if not stat.S_ISREG(artifact_stat.st_mode):
        raise AuthorityCatalogError(
            f"authority artifact is not a regular file for {authority_id}"
        )

    try:
        artifact_bytes = authority_path.read_bytes()
    except OSError as error:
        raise AuthorityCatalogError(
            f"authority artifact is unreadable for {authority_id}"
        ) from error

    expected_size = source.get("size_bytes")
    if not isinstance(expected_size, int) or expected_size < 0:
        raise AuthorityCatalogError(
            f"{authority_id} must declare a valid size_bytes value"
        )
    if len(artifact_bytes) != expected_size:
        raise AuthorityCatalogError(
            f"{authority_id} size_bytes does not match local artifact"
        )

    expected_digest = source.get("sha256")
    if not isinstance(expected_digest, str) or len(expected_digest) != 64:
        raise AuthorityCatalogError(
            f"{authority_id} must declare a valid sha256 digest"
        )
    if hashlib.sha256(artifact_bytes).hexdigest() != expected_digest:
        raise AuthorityCatalogError(
            f"{authority_id} sha256 does not match local artifact"
        )

    return artifact_bytes


def _parse_json_object(
    artifact_bytes: bytes,
    *,
    authority_id: str,
    artifact_description: str,
) -> dict[str, Any]:
    try:
        document = json.loads(artifact_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AuthorityCatalogError(
            f"{artifact_description} for {authority_id} is not valid JSON"
        ) from error

    if not isinstance(document, dict):
        raise AuthorityCatalogError(
            f"{artifact_description} for {authority_id} must be a JSON object"
        )
    return document


def _validate_archive_member_suffix(member_suffix: str) -> None:
    if not isinstance(member_suffix, str) or not member_suffix:
        raise AuthorityCatalogError("member_suffix is required")
    if member_suffix.startswith("/"):
        raise AuthorityCatalogError("member_suffix must not be absolute")
    if "\\" in member_suffix:
        raise AuthorityCatalogError("member_suffix must use forward slashes")
    if _WINDOWS_DRIVE_PREFIX_PATTERN.match(member_suffix) is not None:
        raise AuthorityCatalogError("member_suffix must not include a drive prefix")
    if ".." in PurePosixPath(member_suffix).parts:
        raise AuthorityCatalogError("member_suffix must not contain parent segments")


def _validate_archive_member_name(member_name: str) -> None:
    if not member_name:
        raise AuthorityCatalogError("archive member name is empty")
    normalized_name = member_name[:-1] if member_name.endswith("/") else member_name
    if not normalized_name:
        raise AuthorityCatalogError("archive member name is empty")
    if normalized_name.startswith(("/", "\\")):
        raise AuthorityCatalogError("archive member name must be relative")
    if "\\" in normalized_name:
        raise AuthorityCatalogError("archive member name must use forward slashes")
    if _WINDOWS_DRIVE_PREFIX_PATTERN.match(normalized_name) is not None:
        raise AuthorityCatalogError("archive member name must not include a drive prefix")
    if ".." in PurePosixPath(normalized_name).parts:
        raise AuthorityCatalogError("archive member name must not contain parent segments")


def _archive_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == stat.S_IFLNK


def _load_json_from_verified_zip_bytes(
    artifact_bytes: bytes,
    *,
    authority_id: str,
    member_suffix: str,
) -> tuple[str, dict[str, Any]]:
    matches: list[tuple[str, zipfile.ZipInfo]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(artifact_bytes)) as archive:
            for info in archive.infolist():
                member_name = info.filename
                _validate_archive_member_name(member_name)
                if info.is_dir():
                    continue
                if _archive_member_is_symlink(info):
                    raise AuthorityCatalogError(
                        f"archive member {member_name!r} must not be a symlink"
                    )
                if info.flag_bits & 0x1:
                    raise AuthorityCatalogError(
                        f"archive member {member_name!r} must not be encrypted"
                    )
                posix_name = PurePosixPath(member_name).as_posix()
                if posix_name != member_suffix and not posix_name.endswith(
                    f"/{member_suffix}"
                ):
                    continue
                matches.append((member_name, info))

            if not matches:
                raise AuthorityCatalogError(
                    f"no archive member ending with {member_suffix!r} "
                    f"for {authority_id}"
                )
            if len(matches) > 1:
                raise AuthorityCatalogError(
                    f"ambiguous archive member suffix {member_suffix!r} "
                    f"for {authority_id}"
                )

            member_name, info = matches[0]
            if info.file_size > _MAX_ARCHIVE_MEMBER_UNCOMPRESSED_BYTES:
                raise AuthorityCatalogError(
                    f"archive member {member_name!r} exceeds size limit "
                    f"for {authority_id}"
                )

            try:
                member_bytes = archive.read(member_name)
            except RuntimeError as error:
                raise AuthorityCatalogError(
                    f"archive member {member_name!r} is unreadable for {authority_id}"
                ) from error
    except zipfile.BadZipFile as error:
        raise AuthorityCatalogError(
            f"authority archive for {authority_id} is not a valid zip file"
        ) from error

    if len(member_bytes) > _MAX_ARCHIVE_MEMBER_UNCOMPRESSED_BYTES:
        raise AuthorityCatalogError(
            f"archive member {member_name!r} exceeds size limit for {authority_id}"
        )

    document = _parse_json_object(
        member_bytes,
        authority_id=authority_id,
        artifact_description=f"archive member {member_name!r}",
    )
    return member_name, document


def _decode_json_pointer_token(token: str) -> str:
    decoded: list[str] = []
    index = 0
    while index < len(token):
        char = token[index]
        if char != "~":
            decoded.append(char)
            index += 1
            continue
        if index + 1 >= len(token):
            raise AuthorityCatalogError(
                f"malformed JSON pointer escape in token {token!r}"
            )
        escape = token[index + 1]
        if escape == "0":
            decoded.append("~")
        elif escape == "1":
            decoded.append("/")
        else:
            raise AuthorityCatalogError(
                f"malformed JSON pointer escape ~{escape} in token {token!r}"
            )
        index += 2
    return "".join(decoded)
