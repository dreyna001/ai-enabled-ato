"""Focused tests for deterministic release packaging and offline verification."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import stat
import tarfile
from pathlib import Path

import pytest

from ato_operator.release_allowlist import (
    collect_allowlisted_files,
    is_safe_relative_path,
)
from ato_operator.release_packaging import (
    ReleaseBuildOptions,
    ReleasePackagingError,
    build_release_archive,
    reject_unsafe_staging_path,
    verify_release_archive,
)

ROOT = Path(__file__).resolve().parents[1]


def _hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_minimal_release_tree(
    root: Path,
    *,
    include_portal_dist: bool = True,
    include_airgap: bool = False,
    secret_in_tree: str | None = None,
) -> None:
    for relative_directory in (
        "src/ato_service",
        "src/ato_operator",
        "migrations/versions",
        "docs/contracts/fixtures",
        "docs/release",
        "docs/requirements",
        "reference/authorities/fedramp",
        "deployment/systemd",
        "deployment/nginx",
        "deployment/config",
        "data/qualification",
        "scripts",
    ):
        (root / relative_directory).mkdir(parents=True, exist_ok=True)

    shutil.copytree(ROOT / "src" / "ato_service", root / "src" / "ato_service", dirs_exist_ok=True)
    shutil.copytree(ROOT / "src" / "ato_operator", root / "src" / "ato_operator", dirs_exist_ok=True)
    shutil.copytree(ROOT / "migrations", root / "migrations", dirs_exist_ok=True)
    shutil.copytree(ROOT / "docs" / "contracts", root / "docs" / "contracts", dirs_exist_ok=True)
    shutil.copytree(ROOT / "docs" / "release", root / "docs" / "release", dirs_exist_ok=True)
    shutil.copytree(ROOT / "docs" / "requirements", root / "docs" / "requirements", dirs_exist_ok=True)
    shutil.copytree(
        ROOT / "reference" / "authorities" / "fedramp",
        root / "reference" / "authorities" / "fedramp",
        dirs_exist_ok=True,
    )
    shutil.copytree(ROOT / "deployment" / "systemd", root / "deployment" / "systemd", dirs_exist_ok=True)
    shutil.copytree(ROOT / "deployment" / "nginx", root / "deployment" / "nginx", dirs_exist_ok=True)
    shutil.copytree(ROOT / "data" / "qualification", root / "data" / "qualification", dirs_exist_ok=True)

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
    ):
        destination = root / relative_file
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative_file, destination)

    if include_portal_dist:
        portal_dist = root / "portal" / "dist"
        portal_dist.mkdir(parents=True, exist_ok=True)
        (portal_dist / "index.html").write_text("<!doctype html><title>portal</title>\n", encoding="utf-8")
        (portal_dist / "assets").mkdir(exist_ok=True)
        (portal_dist / "assets" / "app.js").write_text("console.log('portal');\n", encoding="utf-8")

    if include_airgap:
        wheel_dir = root / "dist" / "airgap" / "wheels"
        wheel_dir.mkdir(parents=True, exist_ok=True)
        wheel_path = wheel_dir / "example_pkg-1.0.0-py3-none-any.whl"
        wheel_path.write_bytes(b"PK\x03\x04fake-wheel")
        manifest = {
            "schema_version": "1.1.0",
            "created_at": "2026-07-14T00:00:00Z",
            "python": "3.12.0",
            "wheel_dir": "wheels",
            "portal_bundle_built": include_portal_dist,
            "portal_package_lock_sha256": _hash_bytes(
                (ROOT / "portal" / "package-lock.json").read_bytes()
            ),
            "portal_dist_files": [],
            "wheels": [
                {
                    "filename": wheel_path.name,
                    "sha256": _hash_bytes(wheel_path.read_bytes()),
                    "size_bytes": wheel_path.stat().st_size,
                }
            ],
            "runtime_config_contract": "JSON non-secret settings with credential references only",
            "credential_layout": [
                "/etc/ato-analyzer/credentials/database-dsn",
                "/etc/ato-analyzer/credentials/audit-hmac-key",
            ],
            "portal_prebuild_command": "cd portal && npm ci && npm run build",
            "install_command": "sudo bash scripts/install.sh",
            "notes": "test fixture",
        }
        (root / "dist" / "airgap" / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )

    if secret_in_tree is not None:
        secret_path = root / secret_in_tree
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        secret_path.write_text("postgresql://user:secret@127.0.0.1/db\n", encoding="utf-8")


@pytest.fixture
def release_tree(tmp_path: Path) -> Path:
    tree = tmp_path / "repo"
    _write_minimal_release_tree(tree)
    return tree


def test_collect_allowlisted_files_requires_portal_dist(tmp_path: Path) -> None:
    tree = tmp_path / "repo"
    _write_minimal_release_tree(tree, include_portal_dist=False)
    with pytest.raises(FileNotFoundError, match="portal/dist is required"):
        collect_allowlisted_files(tree, require_portal_dist=True, require_airgap=False)


def test_collect_allowlisted_files_requires_airgap_manifest(tmp_path: Path) -> None:
    tree = tmp_path / "repo"
    _write_minimal_release_tree(tree, include_airgap=False)
    with pytest.raises(FileNotFoundError, match="dist/airgap is required"):
        collect_allowlisted_files(tree, require_portal_dist=True, require_airgap=True)


def test_build_release_archive_is_deterministic(release_tree: Path, tmp_path: Path) -> None:
    output_a = tmp_path / "build-a"
    output_b = tmp_path / "build-b"
    report_a = build_release_archive(
        ReleaseBuildOptions(
            project_root=release_tree,
            output_dir=output_a,
            require_portal_dist=True,
            require_airgap=False,
            source_date_epoch=1_700_000_000,
            git_revision="test-revision",
        )
    )
    report_b = build_release_archive(
        ReleaseBuildOptions(
            project_root=release_tree,
            output_dir=output_b,
            require_portal_dist=True,
            require_airgap=False,
            source_date_epoch=1_700_000_000,
            git_revision="test-revision",
        )
    )
    assert report_a.archive_sha256 == report_b.archive_sha256
    assert report_a.archive_path.read_bytes() == report_b.archive_path.read_bytes()


def test_verify_release_archive_passes_for_fresh_build(release_tree: Path, tmp_path: Path) -> None:
    report = build_release_archive(
        ReleaseBuildOptions(
            project_root=release_tree,
            output_dir=tmp_path / "releases",
            require_portal_dist=True,
            require_airgap=False,
            source_date_epoch=1_700_000_000,
        )
    )
    verify_report = verify_release_archive(report.archive_path, project_root=release_tree)
    assert verify_report.passed is True
    assert verify_report.signature_status == "unavailable"
    assert verify_report.file_count > 0


def test_verify_release_archive_detects_checksum_tampering(release_tree: Path, tmp_path: Path) -> None:
    report = build_release_archive(
        ReleaseBuildOptions(
            project_root=release_tree,
            output_dir=tmp_path / "releases",
            require_portal_dist=True,
            require_airgap=False,
            source_date_epoch=1_700_000_000,
        )
    )
    tampered_path = tmp_path / "tampered.tar.gz"
    with tarfile.open(report.archive_path, mode="r:gz") as source_tar:
        members = {
            member.name: source_tar.extractfile(member).read()
            for member in source_tar.getmembers()
            if member.isfile()
        }
    members["README.md"] = b"tampered readme\n"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in sorted(members.items()):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    tampered_path.write_bytes(buffer.getvalue())
    verify_report = verify_release_archive(tampered_path, project_root=release_tree)
    assert verify_report.passed is False
    assert any("checksum mismatch for README.md" in error for error in verify_report.errors)


def test_verify_release_archive_rejects_traversal_member(release_tree: Path, tmp_path: Path) -> None:
    report = build_release_archive(
        ReleaseBuildOptions(
            project_root=release_tree,
            output_dir=tmp_path / "releases",
            require_portal_dist=True,
            require_airgap=False,
            source_date_epoch=1_700_000_000,
        )
    )
    unsafe_archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(report.archive_path, mode="r:gz") as source_tar:
        members = {member.name: source_tar.extractfile(member).read() for member in source_tar.getmembers() if member.isfile()}
    members["../escape.txt"] = b"traversal"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in sorted(members.items()):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    unsafe_archive.write_bytes(buffer.getvalue())
    verify_report = verify_release_archive(unsafe_archive, project_root=release_tree)
    assert verify_report.passed is False
    assert any("unsafe archive member path" in error for error in verify_report.errors)


def test_verify_release_archive_rejects_symlink_member(release_tree: Path, tmp_path: Path) -> None:
    report = build_release_archive(
        ReleaseBuildOptions(
            project_root=release_tree,
            output_dir=tmp_path / "releases",
            require_portal_dist=True,
            require_airgap=False,
            source_date_epoch=1_700_000_000,
        )
    )
    unsafe_archive = tmp_path / "symlink.tar.gz"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="etc/ato-analyzer/credentials/database-dsn")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    unsafe_archive.write_bytes(buffer.getvalue())
    verify_report = verify_release_archive(unsafe_archive, project_root=release_tree)
    assert verify_report.passed is False
    assert any("must be a regular file" in error for error in verify_report.errors)


def test_build_release_archive_includes_airgap_when_required(tmp_path: Path) -> None:
    tree = tmp_path / "repo"
    _write_minimal_release_tree(tree, include_airgap=True)
    report = build_release_archive(
        ReleaseBuildOptions(
            project_root=tree,
            output_dir=tmp_path / "releases",
            require_portal_dist=True,
            require_airgap=True,
            source_date_epoch=1_700_000_000,
        )
    )
    with tarfile.open(report.archive_path, mode="r:gz") as tar:
        names = {member.name for member in tar.getmembers()}
    assert "dist/airgap/manifest.json" in names
    assert any(name.startswith("dist/airgap/wheels/") for name in names)


def test_is_safe_relative_path_rejects_traversal() -> None:
    assert is_safe_relative_path("src/ato_service/main.py") is True
    assert is_safe_relative_path("../escape") is False
    assert is_safe_relative_path("/absolute") is False


def test_reject_unsafe_staging_path_blocks_symlink(tmp_path: Path) -> None:
    staging_root = tmp_path / "stage"
    staging_root.mkdir()
    target = staging_root / "real.txt"
    target.write_text("ok\n", encoding="utf-8")
    link = staging_root / "link.txt"
    link.symlink_to(target)
    with pytest.raises(ReleasePackagingError, match="symlink"):
        reject_unsafe_staging_path(link, staging_root=staging_root)


def test_verify_release_archive_allows_skipped_portal_dist(release_tree: Path, tmp_path: Path) -> None:
    report = build_release_archive(
        ReleaseBuildOptions(
            project_root=release_tree,
            output_dir=tmp_path / "releases",
            require_portal_dist=False,
            require_airgap=False,
            source_date_epoch=1_700_000_000,
        )
    )
    verify_report = verify_release_archive(report.archive_path, project_root=release_tree)
    assert verify_report.passed is True
    with tarfile.open(report.archive_path, mode="r:gz") as tar:
        names = {member.name for member in tar.getmembers()}
    assert "portal/dist/index.html" not in names


def test_shell_scripts_are_executable_in_archive(release_tree: Path, tmp_path: Path) -> None:
    report = build_release_archive(
        ReleaseBuildOptions(
            project_root=release_tree,
            output_dir=tmp_path / "releases",
            require_portal_dist=True,
            require_airgap=False,
            source_date_epoch=1_700_000_000,
        )
    )
    with tarfile.open(report.archive_path, mode="r:gz") as tar:
        install_member = tar.getmember("scripts/install.sh")
    assert install_member.mode & stat.S_IXUSR


def test_verify_release_archive_rejects_secret_like_script_content(
    release_tree: Path,
    tmp_path: Path,
) -> None:
    report = build_release_archive(
        ReleaseBuildOptions(
            project_root=release_tree,
            output_dir=tmp_path / "releases",
            require_portal_dist=True,
            require_airgap=False,
            source_date_epoch=1_700_000_000,
        )
    )
    tampered_path = tmp_path / "secret-script.tar.gz"
    with tarfile.open(report.archive_path, mode="r:gz") as source_tar:
        members = {
            member.name: source_tar.extractfile(member).read()
            for member in source_tar.getmembers()
            if member.isfile()
        }
    members["scripts/install.sh"] = b"#!/usr/bin/env bash\npassword=notallowed\n"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in sorted(members.items()):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    tampered_path.write_bytes(buffer.getvalue())
    verify_report = verify_release_archive(tampered_path, project_root=release_tree)
    assert verify_report.passed is False
    assert any(
        "secret-like content matched in scripts/install.sh" in error
        for error in verify_report.errors
    )


def test_verify_release_archive_rejects_excluded_credential_path(
    release_tree: Path,
    tmp_path: Path,
) -> None:
    report = build_release_archive(
        ReleaseBuildOptions(
            project_root=release_tree,
            output_dir=tmp_path / "releases",
            require_portal_dist=True,
            require_airgap=False,
            source_date_epoch=1_700_000_000,
        )
    )
    unsafe_archive = tmp_path / "credential-leak.tar.gz"
    with tarfile.open(report.archive_path, mode="r:gz") as source_tar:
        members = {
            member.name: source_tar.extractfile(member).read()
            for member in source_tar.getmembers()
            if member.isfile()
        }
    members["etc/ato-analyzer/credentials/database-dsn"] = b"postgresql://secret\n"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in sorted(members.items()):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    unsafe_archive.write_bytes(buffer.getvalue())
    verify_report = verify_release_archive(unsafe_archive, project_root=release_tree)
    assert verify_report.passed is False
    assert any("forbidden path segment" in error or "excluded path" in error for error in verify_report.errors)


def test_prestage_verify_only_fails_on_missing_wheel(tmp_path: Path) -> None:
    tree = tmp_path / "airgap-repo"
    _write_minimal_release_tree(tree, include_airgap=True)
    wheel_dir = tree / "dist" / "airgap" / "wheels"
    for wheel in wheel_dir.glob("*.whl"):
        wheel.unlink()
    result = pytest.importorskip("subprocess").run(
        ["bash", str(tree / "scripts" / "prestage_airgap_deps.sh"), "--verify-only"],
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "pinned wheel" in (result.stderr + result.stdout).lower()
