"""Regression tests for cross-platform test guards."""

from __future__ import annotations

import os

from tests.support import platform as platform_support


def test_platform_helpers_report_linux_capabilities() -> None:
    assert platform_support.is_posix() is True
    assert platform_support.bash_available() is True
    assert platform_support.has_af_unix() is True
    assert platform_support.can_create_symlinks() is True


def test_bash_script_argv_uses_posix_relative_path(tmp_path) -> None:
    script = tmp_path / "scripts" / "example.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    argv = platform_support.bash_script_argv(script, cwd=tmp_path)
    assert argv == ["bash", "scripts/example.sh"]


def test_requires_posix_marker_reason_is_documented() -> None:
    assert "Linux" in platform_support.POSIX_ONLY_REASON
    if os.name == "posix":
        assert platform_support.is_posix() is True
