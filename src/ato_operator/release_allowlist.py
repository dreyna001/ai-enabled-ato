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
    "deployment/systemd",
    "deployment/nginx",
    "data/qualification",
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
        if not path.is_file():
            return
        if path.is_symlink():
            raise ValueError(f"symlink not allowed in release source tree: {path}")
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
        if not directory.is_dir():
            raise FileNotFoundError(f"missing required release directory: {relative_directory}")
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                maybe_add(path)

    if require_portal_dist:
        portal_dist = root / CONDITIONAL_PORTAL_DIST
        if not portal_dist.is_dir():
            raise FileNotFoundError(
                "portal/dist is required; build portal assets before packaging "
                "(cd portal && npm ci && npm run build)"
            )
        for path in sorted(portal_dist.rglob("*")):
            if path.is_file():
                maybe_add(path)

    if require_airgap:
        airgap_root = root / CONDITIONAL_AIRGAP_ROOT
        if not airgap_root.is_dir():
            raise FileNotFoundError(
                "dist/airgap is required; run scripts/prestage_airgap_deps.sh on a connected host"
            )
        for path in sorted(airgap_root.rglob("*")):
            if path.is_file():
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
    "CONDITIONAL_AIRGAP_ROOT",
    "CONDITIONAL_PORTAL_DIST",
    "DEFAULT_SOURCE_DATE_EPOCH",
    "EXECUTABLE_SCRIPT_PREFIXES",
    "FORBIDDEN_PATH_SEGMENTS",
    "FORBIDDEN_SECRET_PATTERNS",
    "ReleaseBuildOptions",
    "collect_allowlisted_files",
    "is_allowlisted_relative_path",
    "is_excluded_relative_path",
    "is_safe_relative_path",
]
