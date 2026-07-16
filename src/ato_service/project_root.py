"""Resolve the repository or installation root for contract assets."""

from __future__ import annotations

from pathlib import Path


class ProjectRootError(RuntimeError):
    """Raised when the application install root cannot be located."""


def find_project_root(start: Path | None = None) -> Path:
    """Return the nearest ancestor directory containing pyproject.toml."""
    candidate = (start or Path(__file__)).resolve()
    for path in (candidate, *candidate.parents):
        if (path / "pyproject.toml").is_file():
            return path
    raise ProjectRootError("Could not locate project root (pyproject.toml not found)")


def contract_path(relative_path: str | Path, *, start: Path | None = None) -> Path:
    """Return an absolute path below docs/contracts for the install root."""
    return find_project_root(start) / "docs" / "contracts" / Path(relative_path)
