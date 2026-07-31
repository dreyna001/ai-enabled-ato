"""Tests for immutable, offline SSP profile bundles."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZIP_STORED, ZipFile

import pytest

from ato_service.ssp_workspace.profile_bundles import (
    ImplementationStatementAuthorityRef,
    ProfileBundleError,
    ProfileControl,
    ProfileSource,
    default_control_response_policy,
    default_implementation_statement_policy,
    diff_profiles,
    load_profile_bundle,
    resolve_profile,
)

ROOT = Path(__file__).resolve().parents[2]
SEED_BUNDLE = ROOT / "reference" / "ssp_profiles" / "synthetic-fisma-rev5-1.0.0"
REAL_BUNDLE = (
    ROOT / "reference" / "ssp_profiles" / "agency-fisma-nist-sp800-53-rev5-1.2.0"
)
PRIOR_REAL_BUNDLE = (
    ROOT / "reference" / "ssp_profiles" / "agency-fisma-nist-sp800-53-rev5-1.1.0"
)
LEGACY_REAL_BUNDLE = (
    ROOT / "reference" / "ssp_profiles" / "agency-fisma-nist-sp800-53-rev5-1.0.0"
)
PINNED_OSCAL_ARCHIVE = (
    ROOT / "reference" / "authorities" / "nist" / "oscal-content-1.5.0.zip"
)
PROFILE_BUILDER = ROOT / "scripts" / "build_ssp_profile_bundle.py"


def test_load_directory_resolves_low_moderate_and_high_profiles() -> None:
    bundle = load_profile_bundle(SEED_BUNDLE)

    assert bundle.manifest.profile_id == "synthetic-fisma-rev5"
    assert [source.version for source in bundle.manifest.sources] == [
        "Revision 5",
        "Revision 5",
    ]
    assert tuple(
        control.control_id for control in resolve_profile(bundle, "low").controls
    ) == ("AC-1",)
    assert tuple(
        control.control_id for control in resolve_profile(bundle, "moderate").controls
    ) == ("AC-1", "AC-2")
    assert tuple(
        control.control_id for control in resolve_profile(bundle, "high").controls
    ) == ("AC-1", "AC-2", "IA-2")
    assert tuple(item.item_id for item in bundle.ssp_required_items) == (
        "system.authorization_boundary",
        "system.data_types",
        "system.hosting_model",
        "system.name",
        "system.purpose",
    )


def test_loaded_manifest_and_content_are_immutable() -> None:
    bundle = load_profile_bundle(SEED_BUNDLE)

    with pytest.raises(FrozenInstanceError):
        bundle.manifest.profile_version = "2.0.0"  # type: ignore[misc]
    with pytest.raises(TypeError):
        bundle.low_control_ids[0] = "IA-2"  # type: ignore[index]


def test_seed_bundle_uses_default_control_response_policy() -> None:
    bundle = load_profile_bundle(SEED_BUNDLE)

    assert bundle.control_response == default_control_response_policy()
    assert bundle.standard_coverage == ()
    assert all(item.required for item in bundle.ssp_required_items)
    assert all(item.standard_refs == () for item in bundle.ssp_required_items)


def test_real_rev5_bundle_resolves_expected_nist_baseline_counts() -> None:
    bundle = load_profile_bundle(REAL_BUNDLE)

    assert bundle.manifest.profile_id == "agency-fisma-nist-sp800-53-rev5"
    assert bundle.manifest.profile_version == "1.2.0"
    assert bundle.manifest.nist_control_catalog_release == "5.2.0"
    assert len(bundle.catalog_controls) == 1196
    assert len(bundle.ssp_required_items) == 34
    source_ids = tuple(source.source_id for source in bundle.manifest.sources)
    assert source_ids == (
        "nist-oscal-content",
        "nist-sp-800-18-r2",
        "nist-sp-800-53",
        "nist-sp-800-53b",
    )
    sp800_18 = next(
        source
        for source in bundle.manifest.sources
        if source.source_id == "nist-sp-800-18-r2"
    )
    assert sp800_18.version == "2.0.0"
    assert "800-18r2" in sp800_18.reference
    assert len(bundle.standard_coverage) == 22
    assert bundle.control_response.implementation_statuses[-1] == "unknown"
    assert bundle.control_response.responsibilities[-1] == "unknown"
    diast = next(
        item
        for item in bundle.ssp_required_items
        if item.item_id == "system.digital_identity_acceptance"
    )
    assert diast.required is False
    assert diast.evidence_required_for_agent is True

    expected_counts = {
        "low": 149,
        "moderate": 287,
        "high": 370,
    }
    for impact_level, expected_count in expected_counts.items():
        resolved = resolve_profile(bundle, impact_level)  # type: ignore[arg-type]
        assert len(resolved.controls) == expected_count
        assert all(control.requirement_text for control in resolved.controls)
        assert not any(
            control.control_id.startswith("PT-") for control in resolved.controls
        )


def test_legacy_100_bundle_remains_loadable_with_defaults() -> None:
    bundle = load_profile_bundle(LEGACY_REAL_BUNDLE)

    assert bundle.manifest.profile_version == "1.0.0"
    assert len(bundle.ssp_required_items) == 18
    assert bundle.control_response == default_control_response_policy()
    assert bundle.standard_coverage == ()


def test_prior_110_bundle_remains_loadable_without_statement_policy() -> None:
    bundle = load_profile_bundle(PRIOR_REAL_BUNDLE)

    assert bundle.manifest.profile_version == "1.1.0"
    assert (
        bundle.implementation_statement_policy
        == default_implementation_statement_policy()
    )


def test_real_bundle_declares_implementation_statement_policy() -> None:
    bundle = load_profile_bundle(REAL_BUNDLE)

    policy = bundle.implementation_statement_policy
    assert policy.policy_version == "1.0.0"
    assert policy.deterministic.reject_oscal_parameter_insert_syntax is True
    assert (
        policy.deterministic.require_question_for_unresolved_parameterized_controls
        is True
    )
    assert policy.deterministic.require_evidence_for_agent_non_unknown_claims is True
    assert (
        policy.deterministic.require_statement_gap_or_question_before_approval is True
    )
    assert policy.deterministic.semantic_quality_findings_are_advisory is True
    assert policy.agent_instructions.statement_content
    assert policy.agent_instructions.organization_defined_parameters
    assert policy.agent_instructions.inherited_and_hybrid_responsibility
    assert policy.agent_instructions.semantic_review
    assert policy.authority_refs == (
        ImplementationStatementAuthorityRef(
            source_id="nist-sp-800-18-r2",
            requirement_ids=(
                "table1.control-implementation-details",
                "table1.control-implementation-status",
            ),
        ),
    )


def test_load_rejects_unknown_policy_authority_requirement_ids(tmp_path: Path) -> None:
    bundle_path = _copy_real_bundle(tmp_path)
    requirements_path = bundle_path / "ssp-requirements.json"
    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
    requirements["implementation_statement_policy"]["authority_refs"] = [
        {
            "source_id": "nist-sp-800-18-r2",
            "requirement_ids": ["table1.missing-requirement"],
        }
    ]
    _write_json_and_update_checksum(
        bundle_path,
        role="ssp_requirements",
        path=requirements_path,
        document=requirements,
    )

    with pytest.raises(ProfileBundleError, match="unknown requirement_ids"):
        load_profile_bundle(bundle_path)


def test_load_rejects_model_controlled_semantic_approval_gate(tmp_path: Path) -> None:
    bundle_path = _copy_real_bundle(tmp_path)
    requirements_path = bundle_path / "ssp-requirements.json"
    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
    requirements["implementation_statement_policy"]["deterministic"][
        "semantic_quality_findings_are_advisory"
    ] = False
    _write_json_and_update_checksum(
        bundle_path,
        role="ssp_requirements",
        path=requirements_path,
        document=requirements,
    )

    with pytest.raises(
        ProfileBundleError, match="SSP requirements failed schema validation"
    ):
        load_profile_bundle(bundle_path)


def test_diff_profiles_reports_implementation_statement_policy_change() -> None:
    old = resolve_profile(load_profile_bundle(PRIOR_REAL_BUNDLE), "moderate")
    new = resolve_profile(load_profile_bundle(REAL_BUNDLE), "moderate")

    diff = diff_profiles(old, new)

    assert diff.implementation_statement_policy_changed is True
    assert diff.old_implementation_statement_policy_version == "1.0.0"
    assert diff.new_implementation_statement_policy_version == "1.0.0"


def test_real_bundle_standard_coverage_matches_table1_outline() -> None:
    bundle = load_profile_bundle(REAL_BUNDLE)
    coverage_ids = {entry.requirement_id for entry in bundle.standard_coverage}
    table1_ids = {
        "table1.authorization-boundary-description",
        "table1.control-assessment-status",
        "table1.control-implementation-details",
        "table1.control-implementation-status",
        "table1.digital-identity-acceptance-statement",
        "table1.environment-of-operation-diagrams",
        "table1.information-exchanges-summary",
        "table1.laws-regulations-and-policies-affecting-system-requirements",
        "table1.remediation-actions",
        "table1.role-identification-and-responsible-personnel",
        "table1.system-categorization",
        "table1.system-component-inventory",
        "table1.system-information-types",
        "table1.system-name-and-identifier",
        "table1.system-operational-status",
        "table1.system-overview",
        "table1.system-plan-approval",
        "table1.system-plan-change-records",
        "table1.system-plan-review-records",
        "table1.system-type",
        "table1.system-authorization-decision",
    }
    assert table1_ids.issubset(coverage_ids)
    assert coverage_ids - table1_ids == {"outline.acronyms-and-glossary"}
    controls_entries = [
        entry for entry in bundle.standard_coverage if entry.coverage_kind == "controls"
    ]
    assert len(controls_entries) == 2
    assert all(entry.item_ids == () for entry in controls_entries)


def test_load_rejects_unknown_standard_refs(tmp_path: Path) -> None:
    bundle_path = _copy_seed_bundle(tmp_path)
    requirements_path = bundle_path / "ssp-requirements.json"
    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
    requirements["standard_coverage"] = [
        {
            "source_id": "nist-sp-800-53",
            "requirement_id": "table1.system-name",
            "title": "System Name",
            "coverage_kind": "ssp_item",
            "item_ids": ["system.name"],
            "required": True,
        }
    ]
    requirements["items"][0]["standard_refs"] = ["table1.missing"]
    _write_json_and_update_checksum(
        bundle_path,
        role="ssp_requirements",
        path=requirements_path,
        document=requirements,
    )

    with pytest.raises(ProfileBundleError, match="unknown standard_refs"):
        load_profile_bundle(bundle_path)


def test_load_rejects_controls_coverage_with_item_ids(tmp_path: Path) -> None:
    bundle_path = _copy_real_bundle(tmp_path)
    requirements_path = bundle_path / "ssp-requirements.json"
    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
    requirements["standard_coverage"] = [
        {
            "source_id": "nist-sp-800-18-r2",
            "requirement_id": "table1.control-implementation",
            "title": "Security Control Implementation",
            "coverage_kind": "controls",
            "item_ids": ["system.name"],
            "required": True,
        }
    ]
    _write_json_and_update_checksum(
        bundle_path,
        role="ssp_requirements",
        path=requirements_path,
        document=requirements,
    )

    with pytest.raises(
        ProfileBundleError, match="failed schema validation.*standard_coverage"
    ):
        load_profile_bundle(bundle_path)


def test_real_bundle_builder_is_deterministic(tmp_path: Path) -> None:
    generated_bundle = tmp_path / "generated-profile"

    subprocess.run(
        [
            sys.executable,
            str(PROFILE_BUILDER),
            "--archive",
            str(PINNED_OSCAL_ARCHIVE),
            "--output",
            str(generated_bundle),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    expected_files = (
        "catalog.json",
        "baselines.json",
        "ssp-requirements.json",
        "manifest.json",
    )
    assert tuple(sorted(path.name for path in generated_bundle.iterdir())) == tuple(
        sorted(expected_files)
    )
    for filename in expected_files:
        assert (generated_bundle / filename).read_bytes() == (
            REAL_BUNDLE / filename
        ).read_bytes()
    regenerated = load_profile_bundle(generated_bundle)
    assert len(resolve_profile(regenerated, "moderate").controls) == 287


def test_real_bundle_builder_refuses_to_mutate_existing_version(
    tmp_path: Path,
) -> None:
    existing_bundle = tmp_path / "existing-profile"
    shutil.copytree(REAL_BUNDLE, existing_bundle)
    manifest_path = existing_bundle / "manifest.json"
    original_bytes = manifest_path.read_bytes()
    manifest_path.write_bytes(original_bytes + b"\n")

    result = subprocess.run(
        [
            sys.executable,
            str(PROFILE_BUILDER),
            "--archive",
            str(PINNED_OSCAL_ARCHIVE),
            "--output",
            str(existing_bundle),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "immutable profile bundle version" in result.stderr
    assert manifest_path.read_bytes() == original_bytes + b"\n"


def test_load_zip_archive_without_extracting(tmp_path: Path) -> None:
    archive_path = tmp_path / "profile.zip"
    with ZipFile(archive_path, "w", compression=ZIP_STORED) as archive:
        for source_path in sorted(SEED_BUNDLE.iterdir()):
            archive.write(source_path, arcname=source_path.name)

    bundle = load_profile_bundle(archive_path)

    assert bundle.manifest.profile_version == "1.0.0"
    assert len(resolve_profile(bundle, "high").controls) == 3
    assert not (tmp_path / "manifest.json").exists()


def test_load_rejects_checksum_mismatch(tmp_path: Path) -> None:
    bundle_path = _copy_seed_bundle(tmp_path)
    catalog_path = bundle_path / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["catalog"]["metadata"]["title"] = "Tampered title"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    with pytest.raises(ProfileBundleError, match="checksum mismatch.*catalog.json"):
        load_profile_bundle(bundle_path)


def test_load_rejects_missing_source_version(tmp_path: Path) -> None:
    bundle_path = _copy_seed_bundle(tmp_path)
    manifest_path = bundle_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["sources"][0]["version"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        ProfileBundleError,
        match="manifest failed schema validation.*version",
    ):
        load_profile_bundle(bundle_path)


def test_load_rejects_baseline_control_missing_from_catalog(tmp_path: Path) -> None:
    bundle_path = _copy_seed_bundle(tmp_path)
    baselines_path = bundle_path / "baselines.json"
    baselines = json.loads(baselines_path.read_text(encoding="utf-8"))
    baselines["high"].append("SC-1")
    _write_json_and_update_checksum(
        bundle_path,
        role="baselines",
        path=baselines_path,
        document=baselines,
    )

    with pytest.raises(
        ProfileBundleError,
        match="high baseline references controls missing from catalog: SC-1",
    ):
        load_profile_bundle(bundle_path)


def test_load_rejects_unsafe_archive_member_even_when_unreferenced(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with ZipFile(archive_path, "w", compression=ZIP_STORED) as archive:
        for source_path in sorted(SEED_BUNDLE.iterdir()):
            archive.write(source_path, arcname=source_path.name)
        archive.writestr("../outside.json", "{}")

    with pytest.raises(ProfileBundleError, match="member path is unsafe"):
        load_profile_bundle(archive_path)


def test_resolve_rejects_unknown_impact_level() -> None:
    bundle = load_profile_bundle(SEED_BUNDLE)

    with pytest.raises(ProfileBundleError, match="unsupported impact level"):
        resolve_profile(bundle, "critical")  # type: ignore[arg-type]


def test_diff_profiles_is_deterministic_and_reports_semantic_changes() -> None:
    old = resolve_profile(load_profile_bundle(SEED_BUNDLE), "moderate")
    new = replace(
        old,
        profile_version="1.1.0",
        controls=(
            ProfileControl(
                control_id="IA-2",
                title="Identification and Authentication",
                requirement_text="Identify and authenticate users.",
                catalog_pointer="/catalog/groups/1/controls/0",
            ),
            replace(old.controls[1], title="Account Management Updated"),
        ),
        ssp_required_items=(
            replace(old.ssp_required_items[4], min_length=30),
            old.ssp_required_items[0],
        ),
        sources=(
            ProfileSource(
                source_id="agency-overlay",
                title="Synthetic agency overlay",
                version="1",
                reference="Local synthetic source.",
            ),
            replace(old.sources[0], version="Revision 5 update 1"),
        ),
    )

    first = diff_profiles(old, new)
    second = diff_profiles(old, new)

    assert first == second
    assert first.added_control_ids == ("IA-2",)
    assert first.removed_control_ids == ("AC-1",)
    assert first.changed_control_ids == ("AC-2",)
    assert first.removed_ssp_item_ids == (
        "system.data_types",
        "system.hosting_model",
        "system.name",
    )
    assert first.changed_ssp_item_ids == ("system.purpose",)
    assert tuple(
        (change.source_id, change.old_version, change.new_version)
        for change in first.source_version_changes
    ) == (
        ("agency-overlay", None, "1"),
        ("nist-sp-800-53", "Revision 5", "Revision 5 update 1"),
        ("nist-sp-800-53b", "Revision 5", None),
    )


def test_diff_rejects_different_profile_identity_or_impact() -> None:
    profile = resolve_profile(load_profile_bundle(SEED_BUNDLE), "low")

    with pytest.raises(ProfileBundleError, match="matching profile_id"):
        diff_profiles(profile, replace(profile, profile_id="different-profile"))
    with pytest.raises(ProfileBundleError, match="matching impact levels"):
        diff_profiles(profile, replace(profile, impact_level="high"))


def _copy_seed_bundle(tmp_path: Path) -> Path:
    destination = tmp_path / "bundle"
    shutil.copytree(SEED_BUNDLE, destination)
    return destination


def _copy_real_bundle(tmp_path: Path) -> Path:
    destination = tmp_path / "real-bundle"
    shutil.copytree(REAL_BUNDLE, destination)
    return destination


def _write_json_and_update_checksum(
    bundle_path: Path,
    *,
    role: str,
    path: Path,
    document: dict[str, object],
) -> None:
    import hashlib

    path.write_text(
        json.dumps(document, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest_path = bundle_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        if entry["role"] == role:
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            break
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
