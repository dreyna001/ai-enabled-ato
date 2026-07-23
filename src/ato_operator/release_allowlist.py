"""Explicit allowlist and exclusion rules for customer release archives."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
import re

ALLOWLIST_ID = "ato-release-allowlist-v1"

# Directories included recursively when present under project root.
ALLOWLIST_DIRECTORIES: tuple[str, ...] = (
    "src",
    "migrations",
    "docs/contracts",
    "docs/release",
    "docs/requirements",
    "reference/authorities",
    "reference/profiles",
    "deployment/systemd",
    "deployment/nginx",
    "data/qualification",
)

# Deterministic bundled draft analysis profiles shipped in every customer release.
BUNDLED_PROFILE_DIRECTORY = "reference/profiles"
BUNDLED_PROFILE_FILENAMES: tuple[str, ...] = (
    "fedramp-20x-program-class-c.json",
    "fedramp-rev5-transition-low.json",
    "fedramp-rev5-transition-moderate.json",
    "fedramp-rev5-transition-high.json",
)

# Individual files included when present.
ALLOWLIST_FILES: tuple[str, ...] = (
    "pyproject.toml",
    "README.md",
    "alembic.ini",
    "portal/package-lock.json",
    "deployment/config/runtime-config.onprem.example.json",
    "deployment/README.md",
    "docs/CONFIGURATION.md",
    "docs/OPERATIONS_AND_RECOVERY.md",
    "docs/CUSTOMER_ONBOARDING.md",
    "docs/AIRGAP_PRESTAGE.md",
    "docs/RELEASE_PACKAGING.md",
    "docs/AI_EVALUATION_GUIDE.md",
    "docs/THREAT_MODEL.md",
    "scripts/install.sh",
    "scripts/upgrade.sh",
    "scripts/rollback.sh",
    "scripts/drain_workers.sh",
    "scripts/smoke_service_chain.sh",
    "scripts/verify_backup_contract.sh",
    "scripts/prestage_airgap_deps.sh",
    "scripts/build_release.sh",
    "scripts/verify_release.sh",
    "scripts/compile_analysis_profiles.py",
    "scripts/compile_fisma_analysis_profile.py",
)

CONDITIONAL_PORTAL_DIST = "portal/dist"
CONDITIONAL_AIRGAP_ROOT = "dist/airgap"

# Paths or glob patterns always excluded even when a parent directory is allowlisted.
EXCLUDED_GLOBS: tuple[str, ...] = (
    "**/.git/**",
    "**/.git",
    "**/__pycache__/**",
    "**/*.pyc",
    "**/.pytest_cache/**",
    "**/.ruff_cache/**",
    "**/.venv/**",
    "**/node_modules/**",
    "**/.env",
    "**/config.local.env",
    "**/credentials/**",
    "**/database-dsn",
    "**/audit-hmac-key",
    "**/oidc-client-secret",
    "deployment/config/runtime-config.dev_*",
    "deployment/config/runtime-config.wsl_*",
    "deployment/config/runtime-config.dev_local.e2e.json",
    "data/incoming/**",
    "data/processed/**",
    "data/quarantine/**",
    "data/reports/**",
    "data/audit/**",
    "tests/**",
    "portal/node_modules/**",
    "portal/src/**",
    "portal/test-results/**",
    "portal/playwright-report/**",
    ".e2e-stack/**",
)

# Path segments that fail closed when present anywhere in an archive member path.
FORBIDDEN_PATH_SEGMENTS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        ".pytest_cache",
        ".ruff_cache",
        "credentials",
    }
)

# Secret-like content patterns scanned in text payloads during verification.
FORBIDDEN_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"postgresql://", re.IGNORECASE),
    re.compile(r"postgres://", re.IGNORECASE),
    re.compile(r"password\s*=", re.IGNORECASE),
    re.compile(r"secret\s*=", re.IGNORECASE),
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
)

EXECUTABLE_SCRIPT_PREFIXES: tuple[str, ...] = ("scripts/",)

DEFAULT_SOURCE_DATE_EPOCH = 1_700_000_000


@dataclass(frozen=True, slots=True)
class ReleaseBuildOptions:
    project_root: Path
    output_dir: Path
    require_portal_dist: bool = True
    require_airgap: bool = False
    source_date_epoch: int = DEFAULT_SOURCE_DATE_EPOCH
    git_revision: str | None = None


def _normalize_relative(path: str) -> str:
    return path.replace("\\", "/")


def is_safe_relative_path(relative_path: str) -> bool:
    if not relative_path or relative_path.startswith("/"):
        return False
    parts = Path(relative_path).parts
    return ".." not in parts


def path_matches_glob(relative_path: str, pattern: str) -> bool:
    normalized = _normalize_relative(relative_path)
    if fnmatch(normalized, pattern):
        return True
    if fnmatch(normalized, pattern.rstrip("/**")):
        return True
    return False


def is_excluded_relative_path(relative_path: str) -> bool:
    normalized = _normalize_relative(relative_path)
    for pattern in EXCLUDED_GLOBS:
        if path_matches_glob(normalized, pattern):
            return True
    for segment in Path(normalized).parts:
        if segment in FORBIDDEN_PATH_SEGMENTS:
            return True
    return False


def is_allowlisted_relative_path(relative_path: str) -> bool:
    normalized = _normalize_relative(relative_path)
    if is_excluded_relative_path(normalized):
        return False
    if normalized in ALLOWLIST_FILES:
        return True
    for directory in ALLOWLIST_DIRECTORIES:
        prefix = f"{directory}/"
        if normalized == directory or normalized.startswith(prefix):
            return True
    portal_prefix = f"{CONDITIONAL_PORTAL_DIST}/"
    if normalized == CONDITIONAL_PORTAL_DIST or normalized.startswith(portal_prefix):
        return True
    airgap_prefix = f"{CONDITIONAL_AIRGAP_ROOT}/"
    if normalized == CONDITIONAL_AIRGAP_ROOT or normalized.startswith(airgap_prefix):
        return True
    return False


def bundled_profile_relative_paths() -> tuple[str, ...]:
    return tuple(
        f"{BUNDLED_PROFILE_DIRECTORY}/{filename}"
        for filename in BUNDLED_PROFILE_FILENAMES
    )


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"symlink not allowed in release source tree: {path}")


def _require_real_directory(path: Path, *, missing_message: str) -> None:
    _reject_symlink(path)
    if not path.is_dir():
        raise FileNotFoundError(missing_message)


def _iter_regular_files_under(directory: Path) -> list[Path]:
    """Return sorted regular files under directory without following symlinks."""
    _reject_symlink(directory)
    collected: list[Path] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        _reject_symlink(entry)
        if entry.is_dir():
            collected.extend(_iter_regular_files_under(entry))
        elif entry.is_file():
            collected.append(entry)
    return collected


def collect_allowlisted_files(
    project_root: Path,
    *,
    require_portal_dist: bool,
    require_airgap: bool,
) -> list[Path]:
    """Return sorted regular files that belong in a release archive."""
    root = project_root.resolve()
    selected: list[Path] = []

    def maybe_add(path: Path) -> None:
        _reject_symlink(path)
        if not path.is_file():
            return
        relative = _normalize_relative(str(path.relative_to(root)))
        if is_excluded_relative_path(relative):
            return
        if not is_allowlisted_relative_path(relative):
            raise ValueError(f"path is not allowlisted for release packaging: {relative}")
        selected.append(path)

    for relative_file in ALLOWLIST_FILES:
        maybe_add(root / relative_file)

    for relative_directory in ALLOWLIST_DIRECTORIES:
        directory = root / relative_directory
        _require_real_directory(
            directory,
            missing_message=f"missing required release directory: {relative_directory}",
        )
        for path in _iter_regular_files_under(directory):
            maybe_add(path)

    if require_portal_dist:
        portal_dist = root / CONDITIONAL_PORTAL_DIST
        _require_real_directory(
            portal_dist,
            missing_message=(
                "portal/dist is required; build portal assets before packaging "
                "(cd portal && npm ci && npm run build)"
            ),
        )
        for path in _iter_regular_files_under(portal_dist):
            maybe_add(path)

    if require_airgap:
        airgap_root = root / CONDITIONAL_AIRGAP_ROOT
        _require_real_directory(
            airgap_root,
            missing_message=(
                "dist/airgap is required; run scripts/prestage_airgap_deps.sh on a connected host"
            ),
        )
        for path in _iter_regular_files_under(airgap_root):
            maybe_add(path)

    # De-duplicate while preserving deterministic order.
    seen: set[str] = set()
    unique: list[Path] = []
    for path in sorted(selected, key=lambda item: _normalize_relative(str(item.relative_to(root)))):
        relative = _normalize_relative(str(path.relative_to(root)))
        if relative in seen:
            continue
        seen.add(relative)
        unique.append(path)
    return unique


__all__ = [
    "ALLOWLIST_DIRECTORIES",
    "ALLOWLIST_FILES",
    "ALLOWLIST_ID",
    "BUNDLED_PROFILE_DIRECTORY",
    "BUNDLED_PROFILE_FILENAMES",
    "CONDITIONAL_AIRGAP_ROOT",
    "CONDITIONAL_PORTAL_DIST",
    "DEFAULT_SOURCE_DATE_EPOCH",
    "EXECUTABLE_SCRIPT_PREFIXES",
    "FORBIDDEN_PATH_SEGMENTS",
    "FORBIDDEN_SECRET_PATTERNS",
    "ReleaseBuildOptions",
    "bundled_profile_relative_paths",
    "collect_allowlisted_files",
    "is_allowlisted_relative_path",
    "is_excluded_relative_path",
    "is_safe_relative_path",
]
