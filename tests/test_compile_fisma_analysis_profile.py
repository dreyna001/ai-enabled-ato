"""Tests for the customer FISMA analysis profile compile operator CLI."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from ato_service.fisma_control_inventory import load_fisma_control_inventory

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "compile_fisma_analysis_profile.py"
VALID_INVENTORY_PATH = (
    ROOT
    / "docs"
    / "contracts"
    / "fixtures"
    / "fisma-control-inventory.valid.example.json"
)
MANIFEST_PATH = ROOT / "docs" / "contracts" / "authority-manifest.json"
MANIFEST_CREATED_AT = "2026-07-10T22:33:12Z"
APPROVED_AT = "2026-07-14T16:00:00Z"
APPROVED_BY = "agency-security-officer"
MANIFEST_APPROVED_AT = "2026-07-14T17:00:00Z"
MANIFEST_APPROVED_BY = "qualified-sme"


def _load_compile_module():
    spec = importlib.util.spec_from_file_location(
        "compile_fisma_analysis_profile",
        SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


compile_fisma_analysis_profile = _load_compile_module()


def _write_approved_inventory(tmp_path: Path) -> Path:
    document = json.loads(VALID_INVENTORY_PATH.read_text(encoding="utf-8"))
    document["status"] = "approved"
    document["approved_at"] = APPROVED_AT
    document["approved_by"] = APPROVED_BY
    inventory_path = tmp_path / "inventory-approved.json"
    inventory_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return inventory_path


def _write_approved_manifest_copy(tmp_path: Path) -> Path:
    document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    document["status"] = "approved"
    document["approved_at"] = MANIFEST_APPROVED_AT
    document["approved_by"] = MANIFEST_APPROVED_BY
    for source in document["sources"]:
        source["review_status"] = "reviewed"
    manifest_path = tmp_path / "authority-manifest-approved.json"
    manifest_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def _compile_to_path(
    tmp_path: Path,
    *,
    inventory_path: Path | None = None,
    output_name: str = "customer-fisma-profile.json",
    require_approved_inventory: bool = False,
    qualification_status: str = "draft",
    manifest_path: Path | None = None,
) -> compile_fisma_analysis_profile.CompiledFismaProfileArtifact:
    resolved_inventory = inventory_path or VALID_INVENTORY_PATH
    resolved_manifest = manifest_path or compile_fisma_analysis_profile.default_manifest_path(
        project_root=ROOT
    )
    output_path = tmp_path / output_name
    return compile_fisma_analysis_profile.compile_and_write_fisma_profile(
        inventory_path=resolved_inventory,
        output_path=output_path,
        project_root=ROOT,
        manifest_path=resolved_manifest,
        require_approved_inventory=require_approved_inventory,
        qualification_status=qualification_status,
    )


def test_compile_to_tmp_output_is_byte_repeatable(tmp_path: Path) -> None:
    first = _compile_to_path(tmp_path / "first")
    second = _compile_to_path(tmp_path / "second")

    assert first.payload == second.payload
    assert first.output_sha256 == second.output_sha256
    assert first.canonical_profile_digest == second.canonical_profile_digest


def test_draft_inventory_uses_manifest_created_at_for_generated_at(
    tmp_path: Path,
) -> None:
    artifact = _compile_to_path(tmp_path)
    assert artifact.profile["generated_at"] == MANIFEST_CREATED_AT
    assert artifact.profile["qualification_status"] == "draft"
    assert artifact.profile["profile_version"] == "1.0.0"


def test_approved_inventory_uses_approved_at_for_generated_at(tmp_path: Path) -> None:
    inventory_path = _write_approved_inventory(tmp_path)
    artifact = _compile_to_path(
        tmp_path,
        inventory_path=inventory_path,
        require_approved_inventory=True,
    )
    assert artifact.profile["generated_at"] == APPROVED_AT
    assert artifact.inventory.status == "approved"


def test_check_mode_passes_for_matching_output(tmp_path: Path) -> None:
    output_path = tmp_path / "profile.json"
    artifact = _compile_to_path(tmp_path, output_name=output_path.name)

    checked = compile_fisma_analysis_profile.compile_and_write_fisma_profile(
        inventory_path=VALID_INVENTORY_PATH,
        output_path=output_path,
        project_root=ROOT,
        manifest_path=compile_fisma_analysis_profile.default_manifest_path(
            project_root=ROOT
        ),
        check_only=True,
    )
    assert checked.payload == artifact.payload


def test_check_mode_fails_when_output_differs(tmp_path: Path) -> None:
    output_path = tmp_path / "profile.json"
    _compile_to_path(tmp_path, output_name=output_path.name)
    output_path.write_bytes(b"{}\n")

    with pytest.raises(
        compile_fisma_analysis_profile.CompileFismaAnalysisProfileError,
        match="differs from generation",
    ):
        compile_fisma_analysis_profile.compile_and_write_fisma_profile(
            inventory_path=VALID_INVENTORY_PATH,
            output_path=output_path,
            project_root=ROOT,
            manifest_path=compile_fisma_analysis_profile.default_manifest_path(
                project_root=ROOT
            ),
            check_only=True,
        )


def test_check_mode_fails_when_output_missing(tmp_path: Path) -> None:
    output_path = tmp_path / "missing-profile.json"

    with pytest.raises(
        compile_fisma_analysis_profile.CompileFismaAnalysisProfileError,
        match="missing profile artifact",
    ):
        compile_fisma_analysis_profile.compile_and_write_fisma_profile(
            inventory_path=VALID_INVENTORY_PATH,
            output_path=output_path,
            project_root=ROOT,
            manifest_path=compile_fisma_analysis_profile.default_manifest_path(
                project_root=ROOT
            ),
            check_only=True,
        )


def test_runtime_config_snippet_uses_absolute_path_and_output_digest(
    tmp_path: Path,
) -> None:
    artifact = _compile_to_path(tmp_path)
    snippet = compile_fisma_analysis_profile.runtime_config_snippet(
        output_path=artifact.output_path,
        expected_sha256=artifact.output_sha256,
    )

    reference = snippet["FISMA_ANALYSIS_PROFILE_FILE_REFERENCE"]
    assert reference["path"] == str(artifact.output_path.resolve())
    assert reference["expected_sha256"] == artifact.output_sha256
    assert len(reference["expected_sha256"]) == 64


def test_cli_reports_runtime_config_snippet_and_digests(tmp_path: Path) -> None:
    output_path = tmp_path / "profile.json"
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = compile_fisma_analysis_profile.main(
            [
                "--inventory",
                str(VALID_INVENTORY_PATH),
                "--output",
                str(output_path),
            ]
        )

    assert exit_code == 0
    output = buffer.getvalue()
    assert "inventory_status: draft" in output
    assert "profile_id: fisma_agency_security" in output
    assert "impact_level: moderate" in output
    assert "output_byte_sha256:" in output
    assert "canonical_profile_digest:" in output
    assert '"FISMA_ANALYSIS_PROFILE_FILE_REFERENCE"' in output
    assert str(output_path.resolve()) in output


def test_require_approved_inventory_rejects_draft_inventory(tmp_path: Path) -> None:
    output_path = tmp_path / "profile.json"

    with pytest.raises(
        compile_fisma_analysis_profile.CompileFismaAnalysisProfileError,
        match="not approved",
    ):
        compile_fisma_analysis_profile.compile_and_write_fisma_profile(
            inventory_path=VALID_INVENTORY_PATH,
            output_path=output_path,
            project_root=ROOT,
            manifest_path=compile_fisma_analysis_profile.default_manifest_path(
                project_root=ROOT
            ),
            require_approved_inventory=True,
        )


def test_cli_require_approved_inventory_rejects_draft_inventory(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "profile.json"
    exit_code = compile_fisma_analysis_profile.main(
        [
            "--inventory",
            str(VALID_INVENTORY_PATH),
            "--output",
            str(output_path),
            "--require-approved-inventory",
        ]
    )
    assert exit_code == 1
    assert not output_path.exists()


def test_cli_require_approved_inventory_accepts_approved_inventory(
    tmp_path: Path,
) -> None:
    inventory_path = _write_approved_inventory(tmp_path)
    output_path = tmp_path / "profile.json"
    exit_code = compile_fisma_analysis_profile.main(
        [
            "--inventory",
            str(inventory_path),
            "--output",
            str(output_path),
            "--require-approved-inventory",
        ]
    )
    assert exit_code == 0
    assert output_path.is_file()


def test_qualified_fails_on_current_draft_authority_manifest(tmp_path: Path) -> None:
    inventory_path = _write_approved_inventory(tmp_path)
    output_path = tmp_path / "profile.json"

    with pytest.raises(
        compile_fisma_analysis_profile.CompileFismaAnalysisProfileError,
        match="authority manifest status 'draft' is not approved",
    ):
        compile_fisma_analysis_profile.compile_and_write_fisma_profile(
            inventory_path=inventory_path,
            output_path=output_path,
            project_root=ROOT,
            manifest_path=compile_fisma_analysis_profile.default_manifest_path(
                project_root=ROOT
            ),
            qualification_status="qualified",
        )


def test_cli_qualified_fails_on_current_repository_manifest(tmp_path: Path) -> None:
    inventory_path = _write_approved_inventory(tmp_path)
    output_path = tmp_path / "profile.json"

    exit_code = compile_fisma_analysis_profile.main(
        [
            "--inventory",
            str(inventory_path),
            "--output",
            str(output_path),
            "--qualification-status",
            "qualified",
        ]
    )

    assert exit_code == 1
    assert not output_path.exists()


def test_assert_qualified_compilation_prerequisites_accepts_approved_inputs(
    tmp_path: Path,
) -> None:
    inventory_path = _write_approved_inventory(tmp_path)
    manifest_path = _write_approved_manifest_copy(tmp_path)
    inventory = load_fisma_control_inventory(
        inventory_path,
        project_root=ROOT,
    )

    manifest = compile_fisma_analysis_profile.assert_qualified_compilation_prerequisites(
        inventory=inventory,
        manifest_path=manifest_path,
        project_root=ROOT,
    )

    assert inventory.status == "approved"
    assert manifest["status"] == "approved"


def test_assert_qualified_compilation_prerequisites_rejects_draft_manifest(
    tmp_path: Path,
) -> None:
    inventory_path = _write_approved_inventory(tmp_path)
    inventory = load_fisma_control_inventory(
        inventory_path,
        project_root=ROOT,
    )

    with pytest.raises(
        compile_fisma_analysis_profile.CompileFismaAnalysisProfileError,
        match="authority manifest status 'draft' is not approved",
    ):
        compile_fisma_analysis_profile.assert_qualified_compilation_prerequisites(
            inventory=inventory,
            manifest_path=compile_fisma_analysis_profile.default_manifest_path(
                project_root=ROOT
            ),
            project_root=ROOT,
        )


def test_apply_qualification_status_changes_only_status_field() -> None:
    profile = {"profile_id": "fisma_agency_security", "qualification_status": "draft"}

    qualified = compile_fisma_analysis_profile.apply_qualification_status(
        profile,
        qualification_status="qualified",
    )

    assert qualified["qualification_status"] == "qualified"
    assert qualified["profile_id"] == "fisma_agency_security"


def test_qualified_compilation_with_approved_manifest_and_inventory(
    tmp_path: Path,
) -> None:
    inventory_path = _write_approved_inventory(tmp_path)
    manifest_path = _write_approved_manifest_copy(tmp_path)

    qualified = _compile_to_path(
        tmp_path,
        inventory_path=inventory_path,
        manifest_path=manifest_path,
        qualification_status="qualified",
        output_name="qualified-profile.json",
    )
    draft = _compile_to_path(
        tmp_path,
        inventory_path=inventory_path,
        manifest_path=manifest_path,
        require_approved_inventory=True,
        output_name="draft-profile.json",
    )

    assert qualified.profile["qualification_status"] == "qualified"
    assert qualified.profile["generated_at"] == APPROVED_AT
    assert draft.profile["qualification_status"] == "draft"
    assert draft.profile["generated_at"] == APPROVED_AT

    qualified_without_status = {
        key: value
        for key, value in qualified.profile.items()
        if key != "qualification_status"
    }
    draft_without_status = {
        key: value
        for key, value in draft.profile.items()
        if key != "qualification_status"
    }
    assert qualified_without_status == draft_without_status
    assert qualified.payload != draft.payload


def test_qualified_check_mode_passes_for_matching_output(tmp_path: Path) -> None:
    inventory_path = _write_approved_inventory(tmp_path)
    manifest_path = _write_approved_manifest_copy(tmp_path)
    output_path = tmp_path / "qualified-profile.json"

    artifact = _compile_to_path(
        tmp_path,
        inventory_path=inventory_path,
        manifest_path=manifest_path,
        qualification_status="qualified",
        output_name=output_path.name,
    )

    checked = compile_fisma_analysis_profile.compile_and_write_fisma_profile(
        inventory_path=inventory_path,
        output_path=output_path,
        project_root=ROOT,
        manifest_path=manifest_path,
        check_only=True,
        qualification_status="qualified",
    )

    assert checked.payload == artifact.payload


def test_resolve_explicit_output_path_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(
        compile_fisma_analysis_profile.CompileFismaAnalysisProfileError,
        match="parent traversal",
    ):
        compile_fisma_analysis_profile.resolve_explicit_output_path(
            tmp_path / ".." / "escape.json"
        )


def test_resolve_explicit_output_path_rejects_directory(tmp_path: Path) -> None:
    directory = tmp_path / "profile-dir"
    directory.mkdir()

    with pytest.raises(
        compile_fisma_analysis_profile.CompileFismaAnalysisProfileError,
        match="must not be a directory",
    ):
        compile_fisma_analysis_profile.resolve_explicit_output_path(directory)


def test_output_may_be_outside_project_root(tmp_path: Path) -> None:
    outside_root = tmp_path / "outside-project"
    outside_root.mkdir()
    output_path = outside_root / "customer-profile.json"

    artifact = compile_fisma_analysis_profile.compile_and_write_fisma_profile(
        inventory_path=VALID_INVENTORY_PATH,
        output_path=output_path,
        project_root=ROOT,
        manifest_path=compile_fisma_analysis_profile.default_manifest_path(
            project_root=ROOT
        ),
    )

    assert output_path.is_file()
    assert output_path.read_bytes() == artifact.payload
