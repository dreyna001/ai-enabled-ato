"""Allowlist coverage for bundled analysis profile release artifacts."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ato_operator.release_allowlist import (
    ALLOWLIST_FILES,
    BUNDLED_PROFILE_DIRECTORY,
    BUNDLED_PROFILE_FILENAMES,
    bundled_profile_relative_paths,
    collect_allowlisted_files,
    is_allowlisted_relative_path,
    is_excluded_relative_path,
)

ROOT = Path(__file__).resolve().parents[2]


def _try_symlink(source: Path, target: Path) -> bool:
    try:
        os.symlink(source, target)
        return True
    except OSError:
        return False


def _write_minimal_allowlist_tree(root: Path) -> None:
    for relative_directory in (
        "src/ato_service",
        "migrations/versions",
        "docs/contracts",
        "docs/release",
        "docs/requirements",
        "reference/authorities",
        "reference/profiles",
        "deployment/systemd",
        "deployment/nginx",
        "data/qualification",
    ):
        directory = root / relative_directory
        directory.mkdir(parents=True, exist_ok=True)
        (directory / ".keep").write_text("", encoding="utf-8")

    for relative_file in (
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
    ):
        destination = root / relative_file
        destination.parent.mkdir(parents=True, exist_ok=True)
        if (ROOT / relative_file).is_file():
            destination.write_bytes((ROOT / relative_file).read_bytes())
        else:
            destination.write_text("", encoding="utf-8")

    profiles_dir = root / "reference" / "profiles"
    for filename in BUNDLED_PROFILE_FILENAMES:
        source = ROOT / "reference" / "profiles" / filename
        if source.is_file():
            (profiles_dir / filename).write_bytes(source.read_bytes())


@pytest.fixture
def allowlist_tree(tmp_path: Path) -> Path:
    tree = tmp_path / "repo"
    _write_minimal_allowlist_tree(tree)
    return tree


def test_bundled_profile_paths_are_allowlisted() -> None:
    for relative_path in bundled_profile_relative_paths():
        assert is_allowlisted_relative_path(relative_path) is True
        assert is_excluded_relative_path(relative_path) is False


def test_profile_compiler_scripts_are_allowlisted() -> None:
    for relative_path in (
        "scripts/compile_analysis_profiles.py",
        "scripts/compile_fisma_analysis_profile.py",
    ):
        assert relative_path in ALLOWLIST_FILES
        assert is_allowlisted_relative_path(relative_path) is True
        assert is_excluded_relative_path(relative_path) is False


def test_committed_bundled_profiles_exist() -> None:
    profile_dir = ROOT / BUNDLED_PROFILE_DIRECTORY
    assert profile_dir.is_dir()
    present = {path.name for path in profile_dir.glob("*.json")}
    assert set(BUNDLED_PROFILE_FILENAMES) <= present


def test_collect_rejects_symlinked_allowlist_root(allowlist_tree: Path) -> None:
    src_real = allowlist_tree / "src_real"
    src = allowlist_tree / "src"
    src.rename(src_real)
    if not _try_symlink(src_real, src):
        pytest.skip("symlink creation requires elevated privileges on this platform")
    with pytest.raises(ValueError, match="symlink not allowed"):
        collect_allowlisted_files(
            allowlist_tree,
            require_portal_dist=False,
            require_airgap=False,
        )


def test_collect_rejects_symlink_in_allowlist_subtree(
    allowlist_tree: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    secret_file = outside / "secret.txt"
    secret_file.write_text("outside-tree", encoding="utf-8")
    link = allowlist_tree / "src" / "ato_service" / "secret.txt"
    if not _try_symlink(secret_file, link):
        pytest.skip("symlink creation requires elevated privileges on this platform")
    with pytest.raises(ValueError, match="symlink not allowed"):
        collect_allowlisted_files(
            allowlist_tree,
            require_portal_dist=False,
            require_airgap=False,
        )


def test_collect_rejects_symlinked_portal_dist(allowlist_tree: Path) -> None:
    portal_real = allowlist_tree / "portal_real"
    portal_real.mkdir()
    (portal_real / "index.html").write_text("<html></html>", encoding="utf-8")
    portal_dist = allowlist_tree / "portal" / "dist"
    if not _try_symlink(portal_real, portal_dist):
        pytest.skip("symlink creation requires elevated privileges on this platform")
    with pytest.raises(ValueError, match="symlink not allowed"):
        collect_allowlisted_files(
            allowlist_tree,
            require_portal_dist=True,
            require_airgap=False,
        )


def test_collect_rejects_symlinked_airgap_root(allowlist_tree: Path) -> None:
    airgap_real = allowlist_tree / "airgap_real"
    airgap_real.mkdir()
    (airgap_real / "manifest.json").write_text("{}", encoding="utf-8")
    airgap_root = allowlist_tree / "dist" / "airgap"
    airgap_root.parent.mkdir(parents=True, exist_ok=True)
    if not _try_symlink(airgap_real, airgap_root):
        pytest.skip("symlink creation requires elevated privileges on this platform")
    with pytest.raises(ValueError, match="symlink not allowed"):
        collect_allowlisted_files(
            allowlist_tree,
            require_portal_dist=False,
            require_airgap=True,
        )
