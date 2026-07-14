"""Platform capability probes and pytest guards for cross-platform tests."""

from __future__ import annotations

import os
import shutil
import socket
from pathlib import Path

import pytest

POSIX_ONLY_REASON = "POSIX-only behavior; production deploys on Linux"
BASH_UNAVAILABLE_REASON = "bash not available"
SYMLINK_UNAVAILABLE_REASON = (
    "symbolic link creation requires elevated privilege on Windows"
)


def is_posix() -> bool:
    return os.name == "posix"


def bash_available() -> bool:
    return shutil.which("bash") is not None


def has_af_unix() -> bool:
    return hasattr(socket, "AF_UNIX")


def can_create_symlinks() -> bool:
    if os.name == "posix":
        return True
    probe_root = Path(os.environ.get("TEMP", os.environ.get("TMPDIR", "/tmp")))
    target = probe_root / "ato-symlink-probe-target"
    link = probe_root / "ato-symlink-probe-link"
    try:
        target.write_text("probe", encoding="utf-8")
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(target)
        return link.is_symlink()
    except (OSError, NotImplementedError):
        return False
    finally:
        for path in (link, target):
            try:
                if path.is_symlink() or path.is_file():
                    path.unlink()
            except OSError:
                pass


def bash_script_argv(script: Path, *, cwd: Path) -> list[str]:
    """Return a bash argv using a cwd-relative POSIX path safe on Windows shells."""
    if not bash_available():
        raise RuntimeError(BASH_UNAVAILABLE_REASON)
    return ["bash", script.resolve().relative_to(cwd.resolve()).as_posix()]


requires_posix = pytest.mark.skipif(not is_posix(), reason=POSIX_ONLY_REASON)
requires_bash = pytest.mark.skipif(not bash_available(), reason=BASH_UNAVAILABLE_REASON)
requires_af_unix = pytest.mark.skipif(not has_af_unix(), reason=POSIX_ONLY_REASON)
requires_symlink = pytest.mark.skipif(
    not can_create_symlinks(),
    reason=SYMLINK_UNAVAILABLE_REASON,
)
