"""Safe in-memory ZIP member access without path extraction."""

from __future__ import annotations

import posixpath
import re
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from io import BytesIO

from ato_service.extraction.errors import ExtractionError
from ato_service.extraction.types import ExtractionLimits

_ARCHIVE_EXTENSIONS = frozenset(
    {
        ".zip",
        ".jar",
        ".apk",
        ".7z",
        ".rar",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
    }
)
_MACRO_PARTS = frozenset(
    {
        "vbaproject.bin",
        "vbadata.bin",
        "vbaprojectsignature.bin",
        "vbaprojectsignatureagile.bin",
    }
)
_ACTIVE_X_PARTS = frozenset({"activex"})
_EXTERNAL_LINK_PREFIX = "xl/externallinks/"
_DRIVE_PATH_PATTERN = re.compile(r"^[A-Za-z]:")
_UNICODE_SLASH_LOOKALIKES = frozenset({"\u2044", "\u2215", "\u29f5", "\u29f8", "\uff0f", "\uff3c"})
_ARCHIVE_MAGICS = (
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"7z\xbc\xaf\x27\x1c",
    b"Rar!\x1a\x07",
    b"\x1f\x8b",
    b"BZh",
    b"\xfd7zXZ\x00",
)
_READ_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class ZipMember:
    """One validated archive member read entirely into memory."""

    normalized_path: str
    data: bytes


def _normalize_member_path(raw_path: str) -> str:
    if "\x00" in raw_path:
        raise ExtractionError("archive member path contains NUL", error_code="unsafe_archive")
    if any(character in raw_path for character in _UNICODE_SLASH_LOOKALIKES):
        raise ExtractionError(
            "archive member path contains Unicode slash lookalike",
            error_code="unsafe_archive",
        )
    raw_path = unicodedata.normalize("NFKC", raw_path)
    if "\\" in raw_path:
        raise ExtractionError("archive member path contains backslash", error_code="unsafe_archive")
    if raw_path.startswith("/"):
        raise ExtractionError("archive member path is absolute", error_code="unsafe_archive")
    if _DRIVE_PATH_PATTERN.match(raw_path):
        raise ExtractionError("archive member path contains drive letter", error_code="unsafe_archive")
    is_directory = raw_path.endswith("/")
    components = raw_path.removesuffix("/").split("/")
    if any(component.endswith((".", " ")) for component in components):
        raise ExtractionError(
            "archive member path has Windows-ambiguous trailing dot or space",
            error_code="unsafe_archive",
        )
    normalized = posixpath.normpath(raw_path)
    if normalized in {"", "."}:
        raise ExtractionError("archive member path is empty", error_code="unsafe_archive")
    if normalized.startswith("../") or normalized == "..":
        raise ExtractionError("archive member path traverses upward", error_code="unsafe_archive")
    if is_directory:
        normalized = normalized.removesuffix("/") + "/"
    return normalized


def _is_unsafe_special_member(info: zipfile.ZipInfo) -> bool:
    if info.create_system != 3:
        return False
    file_type = stat.S_IFMT(info.external_attr >> 16)
    return file_type not in {0, stat.S_IFREG, stat.S_IFDIR}


def _reject_nested_archive_path(path: str) -> None:
    lower = path.lower()
    for ext in _ARCHIVE_EXTENSIONS:
        if lower.endswith(ext):
            raise ExtractionError(
                f"nested archive member rejected: {path}",
                error_code="unsafe_archive",
            )


def _reject_office_unsafe_member(path: str) -> None:
    basename = posixpath.basename(path).casefold()
    lower_path = path.casefold()
    if basename in _MACRO_PARTS:
        raise ExtractionError("macro-enabled Office member rejected", error_code="unsafe_archive")
    for part in path.split("/"):
        if part.casefold() in _ACTIVE_X_PARTS:
            raise ExtractionError("ActiveX Office member rejected", error_code="unsafe_archive")
    if lower_path.startswith(_EXTERNAL_LINK_PREFIX):
        raise ExtractionError("external spreadsheet link rejected", error_code="unsafe_archive")


def open_safe_zip(
    content: bytes,
    *,
    limits: ExtractionLimits,
    office_container: bool = False,
) -> dict[str, ZipMember]:
    """Validate and read every ZIP member into memory keyed by normalized path."""
    try:
        archive = zipfile.ZipFile(BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ExtractionError("archive is not a valid ZIP", error_code="source_parse_failed") from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > limits.max_zip_members_per_archive:
            raise ExtractionError(
                "archive member count exceeds configured limit",
                error_code="package_limit_exceeded",
            )

        total_uncompressed = 0
        seen_paths: set[str] = set()
        members: dict[str, ZipMember] = {}

        for info in infos:
            normalized = _normalize_member_path(info.filename)
            casefolded = normalized.removesuffix("/").casefold()
            if casefolded in seen_paths:
                raise ExtractionError(
                    "duplicate normalized archive member path",
                    error_code="unsafe_archive",
                )
            seen_paths.add(casefolded)
            _reject_nested_archive_path(normalized)
            if office_container:
                _reject_office_unsafe_member(normalized)

            if _is_unsafe_special_member(info):
                raise ExtractionError(
                    "archive symlink or special member rejected",
                    error_code="unsafe_archive",
                )
            if info.is_dir():
                continue

            compressed_size = info.compress_size
            file_size = info.file_size
            if file_size > 0 and compressed_size == 0:
                raise ExtractionError(
                    "archive member has zero compressed size",
                    error_code="package_limit_exceeded",
                )
            if file_size > compressed_size * limits.max_zip_decompression_ratio:
                raise ExtractionError(
                    "archive decompression ratio exceeds configured limit",
                    error_code="package_limit_exceeded",
                )

            total_uncompressed += file_size
            if total_uncompressed > limits.max_zip_uncompressed_bytes_per_archive:
                raise ExtractionError(
                    "archive uncompressed bytes exceed configured limit",
                    error_code="package_limit_exceeded",
                )

            try:
                data_parts: list[bytes] = []
                bytes_read = 0
                member_limit = min(
                    file_size,
                    limits.max_zip_uncompressed_bytes_per_archive - (total_uncompressed - file_size),
                )
                with archive.open(info, "r") as member_stream:
                    while True:
                        chunk = member_stream.read(
                            min(_READ_CHUNK_BYTES, member_limit - bytes_read + 1)
                        )
                        if not chunk:
                            break
                        bytes_read += len(chunk)
                        if bytes_read > member_limit or bytes_read > file_size:
                            raise ExtractionError(
                                "archive member exceeds declared or configured size",
                                error_code="package_limit_exceeded",
                            )
                        data_parts.append(chunk)
            except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
                raise ExtractionError(
                    "failed to read archive member",
                    error_code="source_parse_failed",
                ) from exc
            data = b"".join(data_parts)

            if len(data) != file_size:
                raise ExtractionError(
                    "archive member size mismatch after read",
                    error_code="source_parse_failed",
                )
            if any(data.startswith(magic) for magic in _ARCHIVE_MAGICS):
                raise ExtractionError(
                    f"nested archive member rejected: {normalized}",
                    error_code="unsafe_archive",
                )
            members[normalized] = ZipMember(normalized_path=normalized, data=data)

    return members


def read_zip_member(members: dict[str, ZipMember], path: str) -> bytes:
    """Return one validated member by normalized path."""
    normalized = _normalize_member_path(path)
    member = members.get(normalized)
    if member is None:
        raise ExtractionError(
            f"missing archive member: {normalized}",
            error_code="source_parse_failed",
        )
    return member.data
