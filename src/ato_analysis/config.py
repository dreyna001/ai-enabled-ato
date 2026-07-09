"""Configuration loading and path resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _find_project_root() -> Path:
    start = Path(__file__).resolve().parent
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError("Could not locate project root (pyproject.toml not found)")


PROJECT_ROOT: Path = _find_project_root()
_CONFIG_FILE = PROJECT_ROOT / "config.local.env"


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if "=" not in stripped:
        return None
    key, _, value = stripped.partition("=")
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def _load_config_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        if key not in os.environ:
            os.environ[key] = value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def _env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def resolve_path(relative: str | Path) -> Path:
    """Resolve a path relative to PROJECT_ROOT."""
    path = Path(relative)
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


@dataclass(frozen=True, slots=True)
class Settings:
    runtime_profile: str
    incoming_dir: Path
    processed_dir: Path
    quarantine_dir: Path
    report_dir: Path
    audit_dir: Path
    openai_api_key: str
    openai_api_url: str
    openai_model: str
    openai_max_tokens: int
    openai_timeout: int
    openai_max_retries: int
    allow_sensitive_openai: bool
    max_input_file_bytes: int
    max_controls_per_package: int
    max_parallel_llm_calls: int
    preflight_block_threshold: float
    dry_run: bool


def load_settings() -> Settings:
    """Load settings from config.local.env with environment overrides."""
    _load_config_file(_CONFIG_FILE)

    dry_run = _env_bool("DRY_RUN", default=False)
    openai_api_key = _env_str("OPENAI_API_KEY")
    if not openai_api_key and not dry_run:
        raise RuntimeError(
            "OPENAI_API_KEY is required unless DRY_RUN=true "
            f"(set in {_CONFIG_FILE.name} or environment)"
        )

    return Settings(
        runtime_profile=_env_str("ATO_RUNTIME_PROFILE", "dev_local"),
        incoming_dir=resolve_path(_env_str("INCOMING_DIR", "data/incoming")),
        processed_dir=resolve_path(_env_str("PROCESSED_DIR", "data/processed")),
        quarantine_dir=resolve_path(_env_str("QUARANTINE_DIR", "data/quarantine")),
        report_dir=resolve_path(_env_str("REPORT_DIR", "data/reports")),
        audit_dir=resolve_path(_env_str("AUDIT_DIR", "data/audit")),
        openai_api_key=openai_api_key,
        openai_api_url=_env_str(
            "OPENAI_API_URL", "https://api.openai.com/v1/chat/completions"
        ),
        openai_model=_env_str("OPENAI_MODEL", "gpt-4.1-mini"),
        openai_max_tokens=_env_int("OPENAI_MAX_TOKENS", 4096),
        openai_timeout=_env_int("OPENAI_TIMEOUT", 120),
        openai_max_retries=_env_int("OPENAI_MAX_RETRIES", 2),
        allow_sensitive_openai=_env_bool("ALLOW_SENSITIVE_OPENAI", default=False),
        max_input_file_bytes=_env_int("MAX_INPUT_FILE_BYTES", 10_485_760),
        max_controls_per_package=_env_int("MAX_CONTROLS_PER_PACKAGE", 50),
        max_parallel_llm_calls=_env_int("MAX_PARALLEL_LLM_CALLS", 1),
        preflight_block_threshold=_env_float("PREFLIGHT_BLOCK_THRESHOLD", 0.6),
        dry_run=dry_run,
    )
