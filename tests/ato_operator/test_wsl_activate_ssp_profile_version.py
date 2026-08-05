"""Contract tests for the local WSL SSP profile activation helper."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "wsl_activate_ssp_profile_version.sh"


def test_help_documents_profile_key_and_version_arguments() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "<profile-key> [profile-version]" in result.stdout
    assert "agency-fisma-nist-sp800-53-rev5 1.2.0" in result.stdout


def test_missing_profile_key_fails_before_accessing_the_install() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "<profile-key> [profile-version]" in result.stderr
    assert "/etc/ato-analyzer" not in result.stderr


def test_profile_lookup_uses_the_composite_unique_key() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'profile_key = os.environ["ACTIVATE_SSP_PROFILE_KEY"]' in script
    assert "SspProfileVersion.profile_key == profile_key," in script
    assert "SspProfileVersion.version == version," in script
    assert "where(SspProfileVersion.version == version)" not in script
