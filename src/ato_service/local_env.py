"""Load dev-only secret variables from a local env file."""

from __future__ import annotations

import os
from pathlib import Path

LOCAL_ENV_FILE_ENV_VAR = "ATO_LOCAL_ENV_FILE"
DEFAULT_LOCAL_ENV_FILENAME = "config.local.env"
TEXT_MODEL_API_KEY_ENV_VAR = "ATO_TEXT_MODEL_API_KEY"

_DEV_SECRET_KEYS = frozenset({TEXT_MODEL_API_KEY_ENV_VAR})


def default_local_env_path(*, project_root: Path | None = None) -> Path:
    """Return the default config.local.env path for the repository."""
    root = project_root or _find_project_root()
    return (root / DEFAULT_LOCAL_ENV_FILENAME).resolve()


def load_local_env_file(
    path: Path | str | None = None,
    *,
    project_root: Path | None = None,
) -> bool:
    """Load allowed dev secret keys from an env file without overriding existing env."""
    env_path = _resolve_local_env_path(path, project_root=project_root)
    if env_path is None or not env_path.is_file():
        return False

    for key, value in _parse_env_file(env_path).items():
        if key not in _DEV_SECRET_KEYS:
            continue
        if os.environ.get(key):
            continue
        os.environ[key] = value
    return True


def _resolve_local_env_path(
    path: Path | str | None,
    *,
    project_root: Path | None,
) -> Path | None:
    if path is not None and str(path).strip():
        return Path(path).expanduser().resolve()
    override = os.environ.get(LOCAL_ENV_FILE_ENV_VAR)
    if override and override.strip():
        return Path(override.strip()).expanduser().resolve()
    return default_local_env_path(project_root=project_root)


def _find_project_root(start: Path | None = None) -> Path:
    candidate = (start or Path(__file__)).resolve()
    for path in (candidate, *candidate.parents):
        if (path / "pyproject.toml").is_file():
            return path
    raise RuntimeError("Could not locate project root (pyproject.toml not found)")


def _parse_env_file(path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}: line {line_number} is not KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"{path}: line {line_number} has an empty key")
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        parsed[key] = value
    return parsed
