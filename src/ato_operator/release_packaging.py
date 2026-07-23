"""Deterministic customer release archive build and offline verification."""

from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import tomllib
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from ato_operator.release_allowlist import (
    ALLOWLIST_ID,
    BUNDLED_PROFILE_DIRECTORY,
    EXECUTABLE_SCRIPT_PREFIXES,
    FORBIDDEN_PATH_SEGMENTS,
    FORBIDDEN_SECRET_PATTERNS,
    ReleaseBuildOptions,
    bundled_profile_relative_paths,
    collect_allowlisted_files,
    is_allowlisted_relative_path,
    is_excluded_relative_path,
    is_safe_relative_path,
)

_HASH_BLOCK_SIZE = 1024 * 1024
_FORMAT_CHECKER = FormatChecker()
_RELEASE_PREFIX = "release/"
_MANIFEST_RELATIVE = f"{_RELEASE_PREFIX}package-manifest.json"
_CHECKSUMS_RELATIVE = f"{_RELEASE_PREFIX}checksums.sha256"
_SBOM_RELATIVE = f"{_RELEASE_PREFIX}sbom.json"
_ONPREM_CONFIG_RELATIVE = "deployment/config/runtime-config.onprem.example.json"
_RUNTIME_CONFIG_SCHEMA_RELATIVE = "docs/contracts/runtime-config.schema.json"
_ANALYSIS_PROFILE_SCHEMA_RELATIVE = "docs/contracts/analysis-profile.schema.json"
_AUTHORITY_MANIFEST_RELATIVE = "docs/contracts/authority-manifest.json"
_PACKAGE_MANIFEST_SCHEMA_RELATIVE = "docs/contracts/release-package-manifest.schema.json"
_BUILDER_ID = "ato-operator release-packaging"
MAX_TAR_MEMBER_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_TAR_AGGREGATE_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
_BUNDLED_PROFILE_DRAFT_WARNING = (
    "bundled analysis profiles have qualification_status=draft; "
    "HS-001 authority review remains open and profiles must not be "
    "represented as qualified"
)


class ReleasePackagingError(ValueError):
    """Base error for release packaging."""


@dataclass(frozen=True, slots=True)
class ReleaseBuildReport:
    archive_path: Path
    package_version: str
    file_count: int
    archive_sha256: str
    migration_head: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "archive_path": str(self.archive_path),
            "package_version": self.package_version,
            "file_count": self.file_count,
            "archive_sha256": self.archive_sha256,
            "migration_head": self.migration_head,
        }


@dataclass(frozen=True, slots=True)
class ReleaseVerifyReport:
    passed: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    file_count: int
    migration_head: str | None
    signature_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "file_count": self.file_count,
            "migration_head": self.migration_head,
            "signature_status": self.signature_status,
        }


def _find_project_root(start: Path | None = None) -> Path:
    candidate = (start or Path.cwd()).resolve()
    for path in (candidate, *candidate.parents):
        if (path / "pyproject.toml").is_file():
            return path
    raise ReleasePackagingError("Could not locate project root (pyproject.toml not found)")


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as blob_file:
        while True:
            chunk = blob_file.read(_HASH_BLOCK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_package_version(project_root: Path) -> str:
    document = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    version = document.get("project", {}).get("version")
    if not isinstance(version, str) or not version.strip():
        raise ReleasePackagingError("pyproject.toml is missing [project].version")
    return version.strip()


def _resolve_migration_head(project_root: Path) -> str:
    alembic_cfg = Config(str(project_root / "alembic.ini"))
    script = ScriptDirectory.from_config(alembic_cfg)
    head = script.get_current_head()
    if not head:
        raise ReleasePackagingError("alembic migration head is unavailable")
    return head


def _load_json_schema(project_root: Path, relative_path: str) -> dict[str, Any]:
    schema_path = project_root / relative_path
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError, SchemaError) as exc:
        raise ReleasePackagingError(f"schema is invalid or unreadable: {relative_path}") from exc
    return schema


def _format_schema_error(error: ValidationError) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    if path:
        return f"schema validation failed at {path}: {error.message}"
    return f"schema validation failed: {error.message}"


def _parse_pyproject_dependencies(project_root: Path) -> dict[str, Any]:
    document = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    project = document.get("project", {})
    dependencies = project.get("dependencies", [])
    optional = project.get("optional-dependencies", {})
    if not isinstance(dependencies, list):
        dependencies = []
    if not isinstance(optional, dict):
        optional = {}
    return {
        "name": project.get("name", "ato_service"),
        "version": project.get("version"),
        "requires_python": project.get("requires-python"),
        "dependencies": dependencies,
        "optional_dependencies": optional,
    }


def _parse_portal_lockfile(project_root: Path) -> dict[str, Any]:
    lock_path = project_root / "portal" / "package-lock.json"
    if not lock_path.is_file():
        raise ReleasePackagingError("portal/package-lock.json is required for SBOM generation")
    lock_document = json.loads(lock_path.read_text(encoding="utf-8"))
    packages: list[dict[str, str | None]] = []
    for package_key, metadata in sorted(lock_document.get("packages", {}).items()):
        if not isinstance(metadata, dict):
            continue
        if package_key == "":
            continue
        name = package_key.removeprefix("node_modules/")
        packages.append(
            {
                "name": name,
                "version": metadata.get("version"),
                "integrity": metadata.get("integrity"),
                "resolved": metadata.get("resolved"),
            }
        )
    root_meta = lock_document.get("packages", {}).get("", {})
    return {
        "name": lock_document.get("name", "ato-portal"),
        "version": root_meta.get("version") if isinstance(root_meta, dict) else None,
        "lockfile_version": lock_document.get("lockfileVersion"),
        "package_lock_sha256": _hash_file(lock_path),
        "packages": packages,
    }


def build_sbom(
    *,
    project_root: Path,
    package_version: str,
    git_revision: str | None,
    archive_sha256: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "builder": _BUILDER_ID,
        "package_version": package_version,
        "git_revision": git_revision,
        "archive_sha256": archive_sha256,
        "python": _parse_pyproject_dependencies(project_root),
        "portal": _parse_portal_lockfile(project_root),
        "notes": (
            "Practical offline evidence derived from pinned Python and npm lock metadata. "
            "Not a SPDX or CycloneDX export."
        ),
    }


def _render_checksum_manifest(file_digests: dict[str, str]) -> str:
    lines = [f"{digest}  {relative_path}" for relative_path, digest in sorted(file_digests.items())]
    return "\n".join(lines) + "\n"


def _parse_checksum_manifest(payload: str) -> dict[str, str]:
    digests: dict[str, str] = {}
    for line_number, raw_line in enumerate(payload.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([a-f0-9]{64})\s{2}(.+)$", line)
        if not match:
            raise ReleasePackagingError(
                f"invalid checksum manifest line {line_number}: {raw_line!r}"
            )
        digest, relative_path = match.groups()
        if relative_path in digests:
            raise ReleasePackagingError(f"duplicate checksum manifest entry: {relative_path}")
        if not is_safe_relative_path(relative_path):
            raise ReleasePackagingError(f"unsafe checksum manifest path: {relative_path}")
        digests[relative_path] = digest
    return digests


def _archive_member_mode(relative_path: str) -> int:
    if relative_path.startswith(EXECUTABLE_SCRIPT_PREFIXES) and relative_path.endswith(".sh"):
        return 0o755
    return 0o644


def _deterministic_tarinfo(
    *,
    relative_path: str,
    size: int,
    source_date_epoch: int,
) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=relative_path)
    info.size = size
    info.mtime = source_date_epoch
    info.mode = _archive_member_mode(relative_path)
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.type = tarfile.REGTYPE
    return info


def _write_deterministic_gzip_tar(
    *,
    archive_path: Path,
    members: dict[str, bytes],
    source_date_epoch: int,
) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for relative_path in sorted(members):
            data = members[relative_path]
            info = _deterministic_tarinfo(
                relative_path=relative_path,
                size=len(data),
                source_date_epoch=source_date_epoch,
            )
            tar.addfile(info, io.BytesIO(data))
    compressed = gzip.compress(payload.getvalue(), compresslevel=9, mtime=0)
    archive_path.write_bytes(compressed)


def _load_compile_analysis_profiles_module(project_root: Path):
    script_path = (project_root / "scripts" / "compile_analysis_profiles.py").resolve()
    if not script_path.is_file():
        raise ReleasePackagingError(
            "missing profile compiler script: scripts/compile_analysis_profiles.py"
        )
    module_name = "ato_operator_release_compile_analysis_profiles"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise ReleasePackagingError(
            "failed to load scripts/compile_analysis_profiles.py for release build"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _read_committed_bundled_profile_qualification_status(
    project_root: Path,
) -> str:
    """Return the uniform qualification_status across committed bundled profiles."""
    statuses: set[str] = set()
    for relative_path in bundled_profile_relative_paths():
        profile_path = project_root / relative_path
        if not profile_path.is_file():
            raise ReleasePackagingError(
                f"missing bundled analysis profile: {relative_path}"
            )
        try:
            document = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ReleasePackagingError(
                f"unable to read bundled analysis profile {relative_path}: {exc}"
            ) from exc
        if not isinstance(document, dict):
            raise ReleasePackagingError(
                f"bundled analysis profile must be a JSON object: {relative_path}"
            )
        qualification_status = document.get("qualification_status")
        if not isinstance(qualification_status, str):
            raise ReleasePackagingError(
                f"bundled analysis profile {relative_path} is missing qualification_status"
            )
        statuses.add(qualification_status)

    if len(statuses) != 1:
        raise ReleasePackagingError(
            "bundled analysis profiles have mixed qualification_status values: "
            f"{sorted(statuses)!r}"
        )

    uniform_status = next(iter(statuses))
    if uniform_status not in {"draft", "qualified"}:
        raise ReleasePackagingError(
            "bundled analysis profiles must have uniform qualification_status "
            f"draft or qualified, got {uniform_status!r}"
        )
    return uniform_status


def _verify_approved_authority_manifest_for_qualified_build(project_root: Path) -> None:
    manifest_path = project_root / _AUTHORITY_MANIFEST_RELATIVE
    if not manifest_path.is_file():
        raise ReleasePackagingError(
            f"missing authority manifest: {_AUTHORITY_MANIFEST_RELATIVE}"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ReleasePackagingError(f"invalid JSON in authority manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ReleasePackagingError("authority manifest must be a JSON object")
    if manifest.get("status") != "approved":
        raise ReleasePackagingError(
            "qualified bundled analysis profiles require an approved authority manifest; "
            f"embedded manifest status is {manifest.get('status')!r}"
        )


def _verify_committed_bundled_profiles(project_root: Path) -> None:
    """Fail closed when bundled profile artifacts are missing or drifted."""
    root = project_root.resolve()
    qualification_status = _read_committed_bundled_profile_qualification_status(root)
    if qualification_status == "qualified":
        _verify_approved_authority_manifest_for_qualified_build(root)
    compiler = _load_compile_analysis_profiles_module(root)
    compile_error_type = getattr(compiler, "CompileAnalysisProfilesError", RuntimeError)
    try:
        compiler.compile_and_write_profiles(
            manifest_path=compiler.default_manifest_path(project_root=root),
            project_root=root,
            output_dir=compiler.default_output_dir(project_root=root),
            check_only=True,
            qualification_status=qualification_status,
        )
    except compile_error_type as exc:
        raise ReleasePackagingError(
            "bundled analysis profile artifacts failed deterministic check; "
            f"regenerate with scripts/compile_analysis_profiles.py "
            f"--qualification-status {qualification_status}: {exc}"
        ) from exc


def _validate_bundled_analysis_profiles(
    members: dict[str, bytes],
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    schema_payload = members.get(_ANALYSIS_PROFILE_SCHEMA_RELATIVE)
    validator: Draft202012Validator | None = None
    if schema_payload is None:
        errors.append(
            f"missing analysis profile schema: {_ANALYSIS_PROFILE_SCHEMA_RELATIVE}"
        )
    else:
        try:
            schema = json.loads(schema_payload.decode("utf-8"))
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)
        except (json.JSONDecodeError, SchemaError, UnicodeDecodeError) as exc:
            errors.append(f"analysis profile schema validation failed: {exc}")

    found_profiles = 0
    qualification_statuses: list[str] = []
    for relative_path in bundled_profile_relative_paths():
        payload = members.get(relative_path)
        if payload is None:
            errors.append(f"missing bundled analysis profile: {relative_path}")
            continue
        found_profiles += 1
        try:
            document = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            errors.append(f"invalid JSON in bundled analysis profile {relative_path}: {exc}")
            continue
        if not isinstance(document, dict):
            errors.append(f"bundled analysis profile must be a JSON object: {relative_path}")
            continue
        if validator is not None:
            validation_error = next(validator.iter_errors(document), None)
            if validation_error is not None:
                errors.append(
                    f"bundled analysis profile {relative_path}: "
                    f"{_format_schema_error(validation_error)}"
                )
        qualification_status = document.get("qualification_status")
        if not isinstance(qualification_status, str):
            errors.append(
                f"bundled analysis profile {relative_path} is missing qualification_status"
            )
            continue
        qualification_statuses.append(qualification_status)

    if not found_profiles:
        return errors, warnings

    unique_statuses = set(qualification_statuses)
    if len(unique_statuses) != 1:
        errors.append(
            "bundled analysis profiles have mixed qualification_status values: "
            f"{sorted(unique_statuses)!r}"
        )
        return errors, warnings

    uniform_status = next(iter(unique_statuses))
    if uniform_status not in {"draft", "qualified"}:
        errors.append(
            "bundled analysis profiles must have uniform qualification_status "
            f"draft or qualified, got {uniform_status!r}"
        )
        return errors, warnings

    if uniform_status == "draft":
        warnings.append(_BUNDLED_PROFILE_DRAFT_WARNING)
        return errors, warnings

    manifest_payload = members.get(_AUTHORITY_MANIFEST_RELATIVE)
    if manifest_payload is None:
        errors.append(f"missing authority manifest: {_AUTHORITY_MANIFEST_RELATIVE}")
        return errors, warnings
    try:
        manifest = json.loads(manifest_payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        errors.append(f"invalid JSON in authority manifest: {exc}")
        return errors, warnings
    if not isinstance(manifest, dict):
        errors.append("authority manifest must be a JSON object")
        return errors, warnings
    if manifest.get("status") != "approved":
        errors.append(
            "qualified bundled analysis profiles require an approved authority manifest; "
            f"embedded manifest status is {manifest.get('status')!r}"
        )

    return errors, warnings


def build_release_archive(options: ReleaseBuildOptions) -> ReleaseBuildReport:
    """Build a deterministic allowlisted release archive."""
    root = options.project_root.resolve()
    _verify_committed_bundled_profiles(root)
    package_version = _read_package_version(root)
    migration_head = _resolve_migration_head(root)
    source_files = collect_allowlisted_files(
        root,
        require_portal_dist=options.require_portal_dist,
        require_airgap=options.require_airgap,
    )

    members: dict[str, bytes] = {}
    file_digests: dict[str, str] = {}
    for path in source_files:
        relative = str(path.relative_to(root)).replace("\\", "/")
        payload = path.read_bytes()
        members[relative] = payload
        file_digests[relative] = _hash_bytes(payload)

    git_revision = options.git_revision
    if git_revision is None:
        git_revision = os.environ.get("RELEASE_GIT_REVISION")

    archive_name = f"ato-analyzer-{package_version}.tar.gz"
    archive_path = options.output_dir.resolve() / archive_name

    # Evidence files are computed from source payload only (not yet including themselves).
    sbom_without_archive = build_sbom(
        project_root=root,
        package_version=package_version,
        git_revision=git_revision,
        archive_sha256=None,
    )
    package_manifest_without_archive = {
        "schema_version": "1.0.0",
        "package_version": package_version,
        "source_date_epoch": options.source_date_epoch,
        "git_revision": git_revision,
        "builder": _BUILDER_ID,
        "allowlist_id": ALLOWLIST_ID,
        "file_count": len(source_files),
        "migration_head": migration_head,
        "portal_dist_required": options.require_portal_dist,
        "airgap_required": options.require_airgap,
        "signature_status": "unavailable",
        "archive_sha256": None,
    }

    members[_SBOM_RELATIVE] = json.dumps(
        sbom_without_archive, indent=2, sort_keys=True
    ).encode("utf-8")
    members[_MANIFEST_RELATIVE] = json.dumps(
        package_manifest_without_archive, indent=2, sort_keys=True
    ).encode("utf-8")
    members[_CHECKSUMS_RELATIVE] = _render_checksum_manifest(file_digests).encode("utf-8")

    _write_deterministic_gzip_tar(
        archive_path=archive_path,
        members=members,
        source_date_epoch=options.source_date_epoch,
    )

    archive_sha256 = _hash_file(archive_path)

    return ReleaseBuildReport(
        archive_path=archive_path,
        package_version=package_version,
        file_count=len(source_files),
        archive_sha256=archive_sha256,
        migration_head=migration_head,
    )


def _validate_archive_path(archive_path: Path) -> None:
    expanded = archive_path.expanduser()
    if expanded.is_symlink():
        raise ReleasePackagingError(f"archive must not be a symlink: {expanded}")
    resolved = expanded.resolve()
    if not resolved.is_file():
        raise ReleasePackagingError(f"archive does not exist: {resolved}")


def _read_bounded_tar_member(
    tar: tarfile.TarFile,
    member: tarfile.TarInfo,
    *,
    aggregate_uncompressed_bytes: int,
    max_member_bytes: int | None = None,
    max_aggregate_bytes: int | None = None,
) -> tuple[bytes | None, list[str], int]:
    """Read one tar member without extraction, enforcing declared and aggregate caps."""
    member_limit = (
        max_member_bytes
        if max_member_bytes is not None
        else MAX_TAR_MEMBER_UNCOMPRESSED_BYTES
    )
    aggregate_limit = (
        max_aggregate_bytes
        if max_aggregate_bytes is not None
        else MAX_TAR_AGGREGATE_UNCOMPRESSED_BYTES
    )
    errors: list[str] = []
    declared_size = member.size
    if declared_size < 0:
        errors.append(f"invalid negative archive member size: {member.name}")
        return None, errors, aggregate_uncompressed_bytes
    if declared_size > member_limit:
        errors.append(
            "archive member exceeds uncompressed size limit "
            f"({member_limit} bytes): {member.name} "
            f"({declared_size} bytes declared)"
        )
        return None, errors, aggregate_uncompressed_bytes
    if aggregate_uncompressed_bytes + declared_size > aggregate_limit:
        errors.append(
            "archive aggregate uncompressed size exceeds limit "
            f"({aggregate_limit} bytes) at member {member.name}"
        )
        return None, errors, aggregate_uncompressed_bytes

    extracted = tar.extractfile(member)
    if extracted is None:
        errors.append(f"unable to read archive member: {member.name}")
        return None, errors, aggregate_uncompressed_bytes

    payload = extracted.read(declared_size + 1)
    if len(payload) > declared_size:
        errors.append(
            f"archive member exceeds declared size during read: {member.name}"
        )
        return None, errors, aggregate_uncompressed_bytes
    if len(payload) != declared_size:
        errors.append(
            f"archive member size mismatch for {member.name}: "
            f"declared {declared_size} bytes, read {len(payload)} bytes"
        )
        return None, errors, aggregate_uncompressed_bytes

    return payload, errors, aggregate_uncompressed_bytes + len(payload)


def _iter_safe_tar_members(tar: tarfile.TarFile) -> tuple[list[tarfile.TarInfo], list[str]]:
    members: list[tarfile.TarInfo] = []
    errors: list[str] = []
    for member in tar.getmembers():
        if not is_safe_relative_path(member.name):
            errors.append(f"unsafe archive member path: {member.name}")
            continue
        if member.isdir():
            continue
        if member.issym() or member.islnk():
            errors.append(f"archive member must be a regular file: {member.name}")
            continue
        if member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE}:
            errors.append(f"unsupported archive member type: {member.name}")
            continue
        members.append(member)
    return members, errors


def _should_scan_text_for_secrets(relative_path: str) -> bool:
    if relative_path.startswith("src/"):
        return False
    if relative_path.startswith("scripts/"):
        return True
    if relative_path.startswith("deployment/"):
        return True
    if relative_path.startswith("docs/") and not relative_path.startswith("docs/contracts/"):
        return True
    return False


def _scan_text_for_secrets(relative_path: str, payload: bytes) -> list[str]:
    if not _should_scan_text_for_secrets(relative_path):
        return []
    if b"\x00" in payload[:1024]:
        return []
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return []
    findings: list[str] = []
    for pattern in FORBIDDEN_SECRET_PATTERNS:
        if pattern.search(text):
            findings.append(
                f"secret-like content matched in {relative_path}: {pattern.pattern}"
            )
    return findings


def _verify_detached_signature(
    *,
    archive_path: Path,
    signature_path: Path | None,
) -> tuple[str, list[str]]:
    if signature_path is None:
        return "unavailable", []
    if not signature_path.is_file():
        return "failed", [f"signature file does not exist: {signature_path}"]
    for command in (["gpg", "--verify", str(signature_path), str(archive_path)],):
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return "unavailable", [
                "gpg is not available; install GnuPG to verify detached signatures"
            ]
        if result.returncode == 0:
            return "verified", []
        stderr = (result.stderr or result.stdout or "").strip()
        return "failed", [f"detached signature verification failed: {stderr or 'gpg --verify returned non-zero'}"]
    return "unavailable", []


def _validate_onprem_config_example(members: dict[str, bytes]) -> list[str]:
    if _ONPREM_CONFIG_RELATIVE not in members:
        return [f"missing required config template: {_ONPREM_CONFIG_RELATIVE}"]
    if _RUNTIME_CONFIG_SCHEMA_RELATIVE not in members:
        return [f"missing runtime config schema: {_RUNTIME_CONFIG_SCHEMA_RELATIVE}"]
    try:
        schema = json.loads(members[_RUNTIME_CONFIG_SCHEMA_RELATIVE].decode("utf-8"))
        Draft202012Validator.check_schema(schema)
        document = json.loads(members[_ONPREM_CONFIG_RELATIVE].decode("utf-8"))
        validator = Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)
        error = next(validator.iter_errors(document), None)
        if error is not None:
            return [_format_schema_error(error)]
    except (json.JSONDecodeError, SchemaError, UnicodeDecodeError) as exc:
        return [f"runtime config example validation failed: {exc}"]
    return []


def _validate_package_manifest_schema(members: dict[str, bytes]) -> tuple[dict[str, Any] | None, list[str]]:
    if _MANIFEST_RELATIVE not in members:
        return None, [f"missing package manifest: {_MANIFEST_RELATIVE}"]
    if _PACKAGE_MANIFEST_SCHEMA_RELATIVE not in members:
        return None, [f"missing package manifest schema: {_PACKAGE_MANIFEST_SCHEMA_RELATIVE}"]
    try:
        schema = json.loads(members[_PACKAGE_MANIFEST_SCHEMA_RELATIVE].decode("utf-8"))
        Draft202012Validator.check_schema(schema)
        manifest = json.loads(members[_MANIFEST_RELATIVE].decode("utf-8"))
        validator = Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)
        error = next(validator.iter_errors(manifest), None)
        if error is not None:
            return None, [_format_schema_error(error)]
    except (json.JSONDecodeError, SchemaError, UnicodeDecodeError) as exc:
        return None, [f"package manifest validation failed: {exc}"]
    return manifest, []


def verify_release_archive(
    archive_path: Path,
    *,
    signature_path: Path | None = None,
    project_root: Path | None = None,
) -> ReleaseVerifyReport:
    """Verify a release archive offline without unsafe extraction."""
    _validate_archive_path(archive_path)
    errors: list[str] = []
    warnings: list[str] = []

    signature_status, signature_errors = _verify_detached_signature(
        archive_path=archive_path,
        signature_path=signature_path,
    )
    errors.extend(signature_errors)

    members: dict[str, bytes] = {}
    aggregate_uncompressed_bytes = 0
    with tarfile.open(archive_path, mode="r:gz") as tar:
        tar_members, member_errors = _iter_safe_tar_members(tar)
        errors.extend(member_errors)
        for member in tar_members:
            for segment in Path(member.name).parts:
                if segment in FORBIDDEN_PATH_SEGMENTS:
                    errors.append(f"forbidden path segment in archive: {member.name}")
                    break
            if is_excluded_relative_path(member.name):
                errors.append(f"excluded path present in archive: {member.name}")
            elif not member.name.startswith(_RELEASE_PREFIX) and not is_allowlisted_relative_path(
                member.name
            ):
                errors.append(f"path is outside release allowlist: {member.name}")

            payload, read_errors, aggregate_uncompressed_bytes = _read_bounded_tar_member(
                tar,
                member,
                aggregate_uncompressed_bytes=aggregate_uncompressed_bytes,
            )
            errors.extend(read_errors)
            if payload is None:
                continue
            members[member.name] = payload
            errors.extend(_scan_text_for_secrets(member.name, payload))

            if member.name.startswith(EXECUTABLE_SCRIPT_PREFIXES) and member.name.endswith(".sh"):
                if member.mode & stat.S_IXUSR == 0:
                    errors.append(f"shell script is not executable in archive: {member.name}")

    if _CHECKSUMS_RELATIVE not in members:
        errors.append(f"missing checksum manifest: {_CHECKSUMS_RELATIVE}")
    if _SBOM_RELATIVE not in members:
        errors.append(f"missing SBOM: {_SBOM_RELATIVE}")

    manifest, manifest_errors = _validate_package_manifest_schema(members)
    errors.extend(manifest_errors)
    errors.extend(_validate_onprem_config_example(members))
    profile_errors, profile_warnings = _validate_bundled_analysis_profiles(members)
    errors.extend(profile_errors)
    warnings.extend(profile_warnings)

    checksum_map: dict[str, str] = {}
    if _CHECKSUMS_RELATIVE in members:
        try:
            checksum_map = _parse_checksum_manifest(
                members[_CHECKSUMS_RELATIVE].decode("utf-8")
            )
        except ReleasePackagingError as exc:
            errors.append(str(exc))

    if manifest is not None and checksum_map:
        for relative_path, expected_digest in checksum_map.items():
            if relative_path.startswith(_RELEASE_PREFIX):
                continue
            actual = members.get(relative_path)
            if actual is None:
                errors.append(f"checksum manifest references missing file: {relative_path}")
                continue
            actual_digest = _hash_bytes(actual)
            if actual_digest != expected_digest:
                errors.append(
                    f"checksum mismatch for {relative_path}: expected {expected_digest}, got {actual_digest}"
                )

        extra_payload_paths = sorted(
            path
            for path in members
            if not path.startswith(_RELEASE_PREFIX) and path not in checksum_map
        )
        for relative_path in extra_payload_paths:
            errors.append(f"archive file missing from checksum manifest: {relative_path}")

    migration_head: str | None = None
    if manifest is not None:
        migration_head = manifest.get("migration_head")
        if isinstance(migration_head, str) and migration_head:
            root = project_root or _find_project_root(archive_path.parent)
            try:
                expected_head = _resolve_migration_head(root)
            except ReleasePackagingError:
                warnings.append(
                    "unable to compare migration head against local alembic script directory"
                )
            else:
                if migration_head != expected_head:
                    errors.append(
                        f"migration head mismatch: archive={migration_head} local={expected_head}"
                    )
        else:
            errors.append("package manifest migration_head is missing")

        declared_sha = manifest.get("archive_sha256")
        if isinstance(declared_sha, str):
            actual_archive_sha = _hash_file(archive_path)
            if declared_sha != actual_archive_sha:
                errors.append(
                    "package manifest archive_sha256 does not match archive bytes "
                    f"(expected {declared_sha}, got {actual_archive_sha})"
                )
        elif declared_sha is not None:
            errors.append("package manifest archive_sha256 must be null or a 64-char digest")

        if manifest.get("portal_dist_required") and "portal/dist/index.html" not in members:
            errors.append("portal/dist/index.html is required but missing from archive")
        if manifest.get("airgap_required") and "dist/airgap/manifest.json" not in members:
            errors.append("dist/airgap/manifest.json is required but missing from archive")

    payload_count = sum(1 for path in members if not path.startswith(_RELEASE_PREFIX))
    passed = not errors
    return ReleaseVerifyReport(
        passed=passed,
        errors=tuple(errors),
        warnings=tuple(warnings),
        file_count=payload_count,
        migration_head=migration_head,
        signature_status=signature_status,
    )


def reject_unsafe_staging_path(path: Path, *, staging_root: Path) -> None:
    """Reject traversal or symlink staging paths during tests and tooling."""
    if path.is_symlink():
        raise ReleasePackagingError(f"symlink staging path rejected: {path}")
    resolved = path.resolve()
    root = staging_root.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ReleasePackagingError(f"staging path escapes root: {path}") from exc


__all__ = [
    "MAX_TAR_AGGREGATE_UNCOMPRESSED_BYTES",
    "MAX_TAR_MEMBER_UNCOMPRESSED_BYTES",
    "ReleaseBuildReport",
    "ReleaseBuildOptions",
    "ReleasePackagingError",
    "ReleaseVerifyReport",
    "build_release_archive",
    "build_sbom",
    "reject_unsafe_staging_path",
    "verify_release_archive",
]
