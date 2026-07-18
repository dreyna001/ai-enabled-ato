"""Alembic migration head contract for operator and deployment scripts."""

from __future__ import annotations

import re
from pathlib import Path

EXPECTED_ALEMBIC_HEAD = "20260717_0013"

_REVISION_RE = re.compile(r'^revision\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
_DOWN_REVISION_RE = re.compile(r"^down_revision\s*=\s*(.+)$", re.MULTILINE)


def _parse_down_revision(raw_value: str) -> str | None:
    value = raw_value.strip()
    if value in {"None", "null"}:
        return None
    quoted = re.match(r'^["\']([^"\']+)["\']$', value)
    if quoted:
        return quoted.group(1)
    return None


def _load_migration_revisions(*, project_root: Path) -> dict[str, str | None]:
    versions_dir = project_root / "migrations" / "versions"
    if not versions_dir.is_dir():
        return {}

    revisions: dict[str, str | None] = {}
    for path in sorted(versions_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        revision_match = _REVISION_RE.search(text)
        down_revision_match = _DOWN_REVISION_RE.search(text)
        if revision_match is None or down_revision_match is None:
            continue
        revisions[revision_match.group(1)] = _parse_down_revision(
            down_revision_match.group(1)
        )
    return revisions


def resolve_migration_head_from_scripts(*, project_root: Path) -> str | None:
    """Return the repository migration head by walking script revision metadata."""
    revisions = _load_migration_revisions(project_root=project_root)
    if not revisions:
        return None

    referenced = {
        down_revision
        for down_revision in revisions.values()
        if down_revision is not None
    }
    heads = [revision for revision in revisions if revision not in referenced]
    if len(heads) != 1:
        return None
    return heads[0]


def resolve_alembic_head(*, project_root: Path) -> str | None:
    """Return the repository alembic head revision id."""
    return resolve_migration_head_from_scripts(project_root=project_root)


def migration_head_matches_contract(*, project_root: Path) -> bool:
    """Return True when the repository head matches the pinned contract revision."""
    head = resolve_alembic_head(project_root=project_root)
    return head == EXPECTED_ALEMBIC_HEAD


__all__ = [
    "EXPECTED_ALEMBIC_HEAD",
    "migration_head_matches_contract",
    "resolve_alembic_head",
    "resolve_migration_head_from_scripts",
]
