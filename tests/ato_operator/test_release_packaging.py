"""Focused release packaging tests for bundled analysis profile contracts."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
from pathlib import Path

import pytest

from ato_operator.release_allowlist import (
    ReleaseBuildOptions,
    bundled_profile_relative_paths,
    collect_allowlisted_files,
)
from ato_operator.release_packaging import (
    ReleasePackagingError,
    build_release_archive,
    verify_release_archive,
)

ROOT = Path(__file__).resolve().parents[2]


def _hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_minimal_release_tree(
    root: Path,
    *,
    include_profiles: bool = True,
    include_portal_dist: bool = False,
) -> None:
    for relative_directory in (
        "src/ato_service",
        "src/ato_operator",
        "migrations/versions",
        "docs/contracts/fixtures",
        "docs/release",
        "docs/requirements",
        "reference/authorities",
        "deployment/systemd",
        "deployment/nginx",
        "deployment/config",
        "data/qualification",
        "scripts",
    ):
        (root / relative_directory).mkdir(parents=True, exist_ok=True)

    shutil.copytree(
        ROOT / "src" / "ato_service", root / "src" / "ato_service", dirs_exist_ok=True
    )
    shutil.copytree(
        ROOT / "src" / "ato_operator", root / "src" / "ato_operator", dirs_exist_ok=True
    )
    shutil.copytree(ROOT / "migrations", root / "migrations", dirs_exist_ok=True)
    shutil.copytree(
        ROOT / "docs" / "contracts", root / "docs" / "contracts", dirs_exist_ok=True
    )
    shutil.copytree(
        ROOT / "docs" / "release", root / "docs" / "release", dirs_exist_ok=True
    )
    shutil.copytree(
        ROOT / "docs" / "requirements",
        root / "docs" / "requirements",
        dirs_exist_ok=True,
    )
    shutil.copytree(
        ROOT / "reference" / "authorities",
        root / "reference" / "authorities",
        dirs_exist_ok=True,
    )
    shutil.copytree(
        ROOT / "deployment" / "systemd",
        root / "deployment" / "systemd",
        dirs_exist_ok=True,
    )
    shutil.copytree(
        ROOT / "deployment" / "nginx", root / "deployment" / "nginx", dirs_exist_ok=True
    )
    shutil.copytree(
        ROOT / "data" / "qualification",
        root / "data" / "qualification",
        dirs_exist_ok=True,
    )

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
        shutil.copy2(ROOT / relative_file, destination)

    if include_profiles:
        shutil.copytree(
            ROOT / "reference" / "profiles",
            root / "reference" / "profiles",
            dirs_exist_ok=True,
        )

    if include_portal_dist:
        portal_dist = root / "portal" / "dist"
        portal_dist.mkdir(parents=True, exist_ok=True)
        (portal_dist / "index.html").write_text(
            "<!doctype html><title>portal</title>\n", encoding="utf-8"
        )


@pytest.fixture
def release_tree(tmp_path: Path) -> Path:
    tree = tmp_path / "repo"
    _write_minimal_release_tree(tree)
    return tree


def test_collect_allowlisted_files_includes_bundled_profiles(release_tree: Path) -> None:
    selected = collect_allowlisted_files(
        release_tree,
        require_portal_dist=False,
        require_airgap=False,
    )
    relative_paths = {
        str(path.relative_to(release_tree)).replace("\\", "/") for path in selected
    }
    for relative_path in bundled_profile_relative_paths():
        assert relative_path in relative_paths


def test_build_release_archive_fails_when_profiles_missing(tmp_path: Path) -> None:
    tree = tmp_path / "repo"
    _write_minimal_release_tree(tree, include_profiles=False)
    with pytest.raises(ReleasePackagingError, match="bundled analysis profile"):
        build_release_archive(
            ReleaseBuildOptions(
                project_root=tree,
                output_dir=tmp_path / "releases",
                require_portal_dist=False,
                require_airgap=False,
                source_date_epoch=1_700_000_000,
            )
        )


def test_build_release_archive_fails_when_profiles_drift(
    release_tree: Path, tmp_path: Path
) -> None:
    profile_path = (
        release_tree / "reference" / "profiles" / "fedramp-rev5-transition-low.json"
    )
    document = json.loads(profile_path.read_text(encoding="utf-8"))
    document["qualification_status"] = "qualified"
    profile_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(
        ReleasePackagingError,
        match="mixed qualification_status",
    ):
        build_release_archive(
            ReleaseBuildOptions(
                project_root=release_tree,
                output_dir=tmp_path / "releases",
                require_portal_dist=False,
                require_airgap=False,
                source_date_epoch=1_700_000_000,
            )
        )


def test_build_release_archive_fails_when_profiles_have_mixed_qualification_status(
    release_tree: Path, tmp_path: Path
) -> None:
    profiles_dir = release_tree / "reference" / "profiles"
    for filename in (
        "fedramp-rev5-transition-low.json",
        "fedramp-rev5-transition-moderate.json",
        "fedramp-rev5-transition-high.json",
        "fedramp-20x-program-class-c.json",
    ):
        document = json.loads((profiles_dir / filename).read_text(encoding="utf-8"))
        if filename == "fedramp-rev5-transition-low.json":
            document["qualification_status"] = "qualified"
        profile_path = profiles_dir / filename
        profile_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(
        ReleasePackagingError,
        match="mixed qualification_status",
    ):
        build_release_archive(
            ReleaseBuildOptions(
                project_root=release_tree,
                output_dir=tmp_path / "releases",
                require_portal_dist=False,
                require_airgap=False,
                source_date_epoch=1_700_000_000,
            )
        )


def test_build_release_archive_is_deterministic_with_profiles(
    release_tree: Path, tmp_path: Path
) -> None:
    output_a = tmp_path / "build-a"
    output_b = tmp_path / "build-b"
    report_a = build_release_archive(
        ReleaseBuildOptions(
            project_root=release_tree,
            output_dir=output_a,
            require_portal_dist=False,
            require_airgap=False,
            source_date_epoch=1_700_000_000,
            git_revision="test-revision",
        )
    )
    report_b = build_release_archive(
        ReleaseBuildOptions(
            project_root=release_tree,
            output_dir=output_b,
            require_portal_dist=False,
            require_airgap=False,
            source_date_epoch=1_700_000_000,
            git_revision="test-revision",
        )
    )
    assert report_a.archive_sha256 == report_b.archive_sha256
    assert report_a.archive_path.read_bytes() == report_b.archive_path.read_bytes()


def test_verify_release_archive_includes_profiles_and_draft_warning(
    release_tree: Path, tmp_path: Path
) -> None:
    report = build_release_archive(
        ReleaseBuildOptions(
            project_root=release_tree,
            output_dir=tmp_path / "releases",
            require_portal_dist=False,
            require_airgap=False,
            source_date_epoch=1_700_000_000,
        )
    )
    verify_report = verify_release_archive(
        report.archive_path, project_root=release_tree
    )
    assert verify_report.passed is True
    assert any("HS-001" in warning for warning in verify_report.warnings)
    with tarfile.open(report.archive_path, mode="r:gz") as tar:
        names = {member.name for member in tar.getmembers()}
    for relative_path in bundled_profile_relative_paths():
        assert relative_path in names


def test_verify_release_archive_rejects_symlink_input(tmp_path: Path) -> None:
    target = tmp_path / "release.tar.gz"
    target.write_bytes(b"not an archive")
    link = tmp_path / "release-link.tar.gz"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")

    with pytest.raises(ReleasePackagingError, match="must not be a symlink"):
        verify_release_archive(link, project_root=ROOT)


def test_verify_release_archive_detects_tampered_profile(
    release_tree: Path, tmp_path: Path
) -> None:
    report = build_release_archive(
        ReleaseBuildOptions(
            project_root=release_tree,
            output_dir=tmp_path / "releases",
            require_portal_dist=False,
            require_airgap=False,
            source_date_epoch=1_700_000_000,
        )
    )
    tampered_path = tmp_path / "tampered-profile.tar.gz"
    profile_member = "reference/profiles/fedramp-rev5-transition-low.json"
    with tarfile.open(report.archive_path, mode="r:gz") as source_tar:
        members = {
            member.name: source_tar.extractfile(member).read()
            for member in source_tar.getmembers()
            if member.isfile()
        }
    document = json.loads(members[profile_member].decode("utf-8"))
    document["qualification_status"] = "qualified"
    members[profile_member] = (json.dumps(document, indent=2) + "\n").encode("utf-8")
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
        "mixed qualification_status" in error for error in verify_report.errors
    )


def test_verify_release_archive_rejects_qualified_profiles_with_draft_manifest(
    release_tree: Path, tmp_path: Path
) -> None:
    report = build_release_archive(
        ReleaseBuildOptions(
            project_root=release_tree,
            output_dir=tmp_path / "releases",
            require_portal_dist=False,
            require_airgap=False,
            source_date_epoch=1_700_000_000,
        )
    )
    tampered_path = tmp_path / "qualified-with-draft-manifest.tar.gz"
    with tarfile.open(report.archive_path, mode="r:gz") as source_tar:
        members = {
            member.name: source_tar.extractfile(member).read()
            for member in source_tar.getmembers()
            if member.isfile()
        }
    for relative_path in bundled_profile_relative_paths():
        document = json.loads(members[relative_path].decode("utf-8"))
        document["qualification_status"] = "qualified"
        members[relative_path] = (json.dumps(document, indent=2) + "\n").encode("utf-8")
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
        "qualified bundled analysis profiles require an approved authority manifest"
        in error
        for error in verify_report.errors
    )
    assert not any("HS-001" in warning for warning in verify_report.warnings)


def _write_tar_member(
    tar: tarfile.TarFile,
    *,
    name: str,
    data: bytes,
    declared_size: int | None = None,
) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = declared_size if declared_size is not None else len(data)
    tar.addfile(info, io.BytesIO(data))


def test_verify_release_archive_rejects_oversized_declared_member(
    release_tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ato_operator.release_packaging as release_packaging

    monkeypatch.setattr(release_packaging, "MAX_TAR_MEMBER_UNCOMPRESSED_BYTES", 8)
    report = build_release_archive(
        ReleaseBuildOptions(
            project_root=release_tree,
            output_dir=tmp_path / "releases",
            require_portal_dist=False,
            require_airgap=False,
            source_date_epoch=1_700_000_000,
        )
    )
    oversized_path = tmp_path / "oversized-declared.tar.gz"
    with tarfile.open(report.archive_path, mode="r:gz") as source_tar:
        members = {
            member.name: source_tar.extractfile(member).read()
            for member in source_tar.getmembers()
            if member.isfile()
        }
    members["release/oversized.bin"] = b"0123456789"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in sorted(members.items()):
            _write_tar_member(tar, name=name, data=data)
    oversized_path.write_bytes(buffer.getvalue())
    verify_report = verify_release_archive(oversized_path, project_root=release_tree)
    assert verify_report.passed is False
    assert any(
        "archive member exceeds uncompressed size limit" in error
        for error in verify_report.errors
    )


def test_verify_release_archive_rejects_aggregate_cap_via_monkeypatch(
    release_tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ato_operator.release_packaging as release_packaging

    report = build_release_archive(
        ReleaseBuildOptions(
            project_root=release_tree,
            output_dir=tmp_path / "releases",
            require_portal_dist=False,
            require_airgap=False,
            source_date_epoch=1_700_000_000,
        )
    )
    monkeypatch.setattr(
        release_packaging,
        "MAX_TAR_AGGREGATE_UNCOMPRESSED_BYTES",
        1024,
    )
    verify_report = verify_release_archive(
        report.archive_path, project_root=release_tree
    )
    assert verify_report.passed is False
    assert any(
        "archive aggregate uncompressed size exceeds limit" in error
        for error in verify_report.errors
    )


def test_read_bounded_tar_member_rejects_short_member() -> None:
    import ato_operator.release_packaging as release_packaging

    member = tarfile.TarInfo(name="release/short.bin")
    member.size = 20

    class ShortReader:
        def read(self, size: int = -1) -> bytes:
            return b"truncated"

    class FakeTar:
        def extractfile(self, _member: tarfile.TarInfo) -> ShortReader:
            return ShortReader()

    payload, errors, aggregate = release_packaging._read_bounded_tar_member(
        FakeTar(),
        member,
        aggregate_uncompressed_bytes=0,
    )
    assert payload is None
    assert aggregate == 0
    assert any("archive member size mismatch" in error for error in errors)


def test_read_bounded_tar_member_rejects_long_member() -> None:
    import ato_operator.release_packaging as release_packaging

    member = tarfile.TarInfo(name="release/long.bin")
    member.size = 5

    class LongReader:
        def read(self, size: int = -1) -> bytes:
            if size < 0:
                size = 6
            return b"x" * size

    class FakeTar:
        def extractfile(self, _member: tarfile.TarInfo) -> LongReader:
            return LongReader()

    payload, errors, aggregate = release_packaging._read_bounded_tar_member(
        FakeTar(),
        member,
        aggregate_uncompressed_bytes=0,
    )
    assert payload is None
    assert aggregate == 0
    assert any("archive member exceeds declared size during read" in error for error in errors)


def test_build_release_archive_rejects_qualified_profiles_with_draft_manifest(
    release_tree: Path, tmp_path: Path
) -> None:
    profiles_dir = release_tree / "reference" / "profiles"
    for filename in (
        "fedramp-rev5-transition-low.json",
        "fedramp-rev5-transition-moderate.json",
        "fedramp-rev5-transition-high.json",
        "fedramp-20x-program-class-c.json",
    ):
        profile_path = profiles_dir / filename
        document = json.loads(profile_path.read_text(encoding="utf-8"))
        document["qualification_status"] = "qualified"
        profile_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(
        ReleasePackagingError,
        match="qualified bundled analysis profiles require an approved authority manifest",
    ):
        build_release_archive(
            ReleaseBuildOptions(
                project_root=release_tree,
                output_dir=tmp_path / "releases",
                require_portal_dist=False,
                require_airgap=False,
                source_date_epoch=1_700_000_000,
            )
        )
