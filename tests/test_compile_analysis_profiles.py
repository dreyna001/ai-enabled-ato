"""Tests for deterministic analysis profile artifact compilation."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "compile_analysis_profiles.py"
REFERENCE_PROFILES_DIR = ROOT / "reference" / "profiles"
MANIFEST_PATH = ROOT / "docs" / "contracts" / "authority-manifest.json"

EXPECTED_GENERATED_AT = "2026-07-10T22:33:12Z"
EXPECTED_PROFILE_VERSION = "1.0.0"
EXPECTED_FILENAMES = (
    "fedramp-20x-program-class-c.json",
    "fedramp-rev5-transition-low.json",
    "fedramp-rev5-transition-moderate.json",
    "fedramp-rev5-transition-high.json",
)


def _load_compile_module():
    spec = importlib.util.spec_from_file_location(
        "compile_analysis_profiles",
        SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


compile_analysis_profiles = _load_compile_module()


def _compile_to_tmp(
    tmp_path: Path,
    *,
    qualification_status: str = "draft",
    manifest_path: Path | None = None,
):
    return compile_analysis_profiles.compile_and_write_profiles(
        manifest_path=manifest_path
        or compile_analysis_profiles.default_manifest_path(project_root=ROOT),
        project_root=ROOT,
        output_dir=tmp_path,
        qualification_status=qualification_status,
    )


def _copy_authority_tree(project_root: Path) -> None:
    for source in json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["sources"]:
        src = ROOT / source["local_path"]
        dest = project_root / source["local_path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def _write_manifest(
    project_root: Path,
    *,
    status: str,
    review_status: str,
) -> Path:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["status"] = status
    if status == "approved":
        manifest["approved_at"] = manifest["created_at"]
        manifest["approved_by"] = "test-reviewer"
    else:
        manifest["approved_at"] = None
        manifest["approved_by"] = None
    for source in manifest["sources"]:
        source["review_status"] = review_status
    manifest_path = project_root / "docs" / "contracts" / "authority-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def _prepare_temp_project(tmp_path: Path, *, status: str, review_status: str) -> Path:
    project_root = tmp_path / "project"
    _copy_authority_tree(project_root)
    contracts_dir = project_root / "docs" / "contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    for relative_name in (
        "analysis-profile.schema.json",
        "authority-manifest.schema.json",
    ):
        shutil.copy2(
            ROOT / "docs" / "contracts" / relative_name,
            contracts_dir / relative_name,
        )
    _write_manifest(project_root, status=status, review_status=review_status)
    return project_root


def test_compile_bundled_profile_artifacts_are_byte_stable(tmp_path: Path) -> None:
    first = _compile_to_tmp(tmp_path / "first")
    second = _compile_to_tmp(tmp_path / "second")

    assert [artifact.filename for artifact in first] == list(EXPECTED_FILENAMES)
    assert len(first) == len(EXPECTED_FILENAMES)

    first_by_name = {artifact.filename: artifact for artifact in first}
    second_by_name = {artifact.filename: artifact for artifact in second}
    for filename in EXPECTED_FILENAMES:
        assert first_by_name[filename].payload == second_by_name[filename].payload
        assert first_by_name[filename].sha256 == second_by_name[filename].sha256


def test_generated_profiles_use_manifest_metadata(tmp_path: Path) -> None:
    artifacts = _compile_to_tmp(tmp_path)

    for artifact in artifacts:
        document = json.loads(artifact.payload.decode("utf-8"))
        assert document["generated_at"] == EXPECTED_GENERATED_AT
        assert document["profile_version"] == EXPECTED_PROFILE_VERSION
        assert document["qualification_status"] == "draft"
        assert document["authority_manifest_id"] == "ato-authorities-2026-07-10-draft"


def test_generated_profiles_have_lf_and_trailing_newline(tmp_path: Path) -> None:
    artifacts = _compile_to_tmp(tmp_path)

    for artifact in artifacts:
        text = artifact.payload.decode("utf-8")
        assert "\r" not in text
        assert text.endswith("\n")
        assert not text.endswith("\n\n")


def test_check_mode_passes_for_committed_reference_profiles() -> None:
    artifacts = compile_analysis_profiles.compile_bundled_profile_artifacts(
        manifest_path=compile_analysis_profiles.default_manifest_path(
            project_root=ROOT
        ),
        project_root=ROOT,
    )
    compile_analysis_profiles.check_profile_artifacts(
        output_dir=REFERENCE_PROFILES_DIR,
        artifacts=artifacts,
    )


def test_check_mode_fails_when_committed_file_differs(tmp_path: Path) -> None:
    artifacts = compile_analysis_profiles.compile_bundled_profile_artifacts(
        manifest_path=compile_analysis_profiles.default_manifest_path(
            project_root=ROOT
        ),
        project_root=ROOT,
    )
    output_dir = tmp_path / "profiles"
    output_dir.mkdir()
    (output_dir / artifacts[0].filename).write_bytes(b"{}\n")

    with pytest.raises(
        compile_analysis_profiles.CompileAnalysisProfilesError,
        match="differs from generation",
    ):
        compile_analysis_profiles.check_profile_artifacts(
            output_dir=output_dir,
            artifacts=artifacts,
        )


def test_check_mode_fails_when_committed_file_missing(tmp_path: Path) -> None:
    artifacts = compile_analysis_profiles.compile_bundled_profile_artifacts(
        manifest_path=compile_analysis_profiles.default_manifest_path(
            project_root=ROOT
        ),
        project_root=ROOT,
    )

    with pytest.raises(
        compile_analysis_profiles.CompileAnalysisProfilesError,
        match="missing committed profile artifact",
    ):
        compile_analysis_profiles.check_profile_artifacts(
            output_dir=tmp_path,
            artifacts=artifacts,
        )


def test_resolve_output_path_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(
        compile_analysis_profiles.CompileAnalysisProfilesError,
        match="unsafe profile output filename",
    ):
        compile_analysis_profiles.resolve_output_path(
            output_dir=tmp_path,
            filename="../escape.json",
        )


def test_committed_reference_profiles_match_generation() -> None:
    artifacts = compile_analysis_profiles.compile_bundled_profile_artifacts(
        manifest_path=compile_analysis_profiles.default_manifest_path(
            project_root=ROOT
        ),
        project_root=ROOT,
    )
    artifacts_by_name = {artifact.filename: artifact for artifact in artifacts}

    committed_files = [REFERENCE_PROFILES_DIR / name for name in EXPECTED_FILENAMES]
    assert all(path.is_file() for path in committed_files)

    for path in committed_files:
        payload = path.read_bytes()
        artifact = artifacts_by_name[path.name]
        assert payload == artifact.payload
        assert hashlib.sha256(payload).hexdigest() == artifact.sha256


def test_cli_check_exits_zero_for_committed_profiles() -> None:
    assert (
        compile_analysis_profiles.main(
            ["--check", "--output-dir", str(REFERENCE_PROFILES_DIR)]
        )
        == 0
    )


def test_cli_write_generates_expected_files(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    assert (
        compile_analysis_profiles.main(["--output-dir", str(output_dir)]) == 0
    )

    generated = [output_dir / name for name in EXPECTED_FILENAMES]
    assert all(path.is_file() for path in generated)


def test_qualified_mode_fails_against_draft_manifest() -> None:
    with pytest.raises(
        compile_analysis_profiles.CompileAnalysisProfilesError,
        match="qualified bundled profiles require an approved authority manifest",
    ):
        compile_analysis_profiles.compile_bundled_profile_artifacts(
            manifest_path=compile_analysis_profiles.default_manifest_path(
                project_root=ROOT
            ),
            project_root=ROOT,
            qualification_status="qualified",
        )


def test_qualified_mode_only_changes_qualification_status(tmp_path: Path) -> None:
    project_root = _prepare_temp_project(
        tmp_path,
        status="draft",
        review_status="pending",
    )
    draft_manifest_path = project_root / "docs" / "contracts" / "authority-manifest.json"
    draft_artifacts = compile_analysis_profiles.compile_bundled_profile_artifacts(
        manifest_path=draft_manifest_path,
        project_root=project_root,
        qualification_status="draft",
    )
    approved_manifest_path = _write_manifest(
        project_root,
        status="approved",
        review_status="reviewed",
    )
    qualified_artifacts = compile_analysis_profiles.compile_bundled_profile_artifacts(
        manifest_path=approved_manifest_path,
        project_root=project_root,
        qualification_status="qualified",
    )

    draft_by_name = {artifact.filename: artifact for artifact in draft_artifacts}
    qualified_by_name = {
        artifact.filename: artifact for artifact in qualified_artifacts
    }
    for filename in EXPECTED_FILENAMES:
        draft_document = json.loads(draft_by_name[filename].payload.decode("utf-8"))
        qualified_document = json.loads(
            qualified_by_name[filename].payload.decode("utf-8")
        )
        assert draft_document["qualification_status"] == "draft"
        assert qualified_document["qualification_status"] == "qualified"
        draft_without_status = dict(draft_document)
        draft_without_status.pop("qualification_status")
        qualified_without_status = dict(qualified_document)
        qualified_without_status.pop("qualification_status")
        assert draft_without_status == qualified_without_status
        assert draft_by_name[filename].payload != qualified_by_name[filename].payload


def test_qualified_check_mode_matches_generation(tmp_path: Path) -> None:
    project_root = _prepare_temp_project(
        tmp_path,
        status="approved",
        review_status="reviewed",
    )
    manifest_path = project_root / "docs" / "contracts" / "authority-manifest.json"
    output_dir = project_root / "reference" / "profiles"
    output_dir.mkdir(parents=True)
    artifacts = compile_analysis_profiles.compile_and_write_profiles(
        manifest_path=manifest_path,
        project_root=project_root,
        output_dir=output_dir,
        qualification_status="qualified",
    )
    compile_analysis_profiles.check_profile_artifacts(
        output_dir=output_dir,
        artifacts=artifacts,
    )
