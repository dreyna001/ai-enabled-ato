"""Alembic migration head contract for operator and deployment scripts."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

EXPECTED_ALEMBIC_HEAD = "20260717_0012"


def resolve_alembic_head(*, project_root: Path) -> str | None:
    """Return the repository alembic head revision id."""
    alembic_ini = project_root / "alembic.ini"
    if not alembic_ini.is_file():
        return None
    script = ScriptDirectory.from_config(Config(str(alembic_ini)))
    return script.get_current_head()


def migration_head_matches_contract(*, project_root: Path) -> bool:
    """Return True when the repository head matches the pinned contract revision."""
    head = resolve_alembic_head(project_root=project_root)
    return head == EXPECTED_ALEMBIC_HEAD


__all__ = [
    "EXPECTED_ALEMBIC_HEAD",
    "migration_head_matches_contract",
    "resolve_alembic_head",
]
