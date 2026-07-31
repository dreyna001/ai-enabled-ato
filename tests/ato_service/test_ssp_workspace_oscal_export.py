"""Tests for draft OSCAL SSP JSON export from approved workspace snapshots."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest

from ato_service.oscal_ssp_schema import (
    OscalSspSchemaError,
    _oscal_ssp_validator,
    _prepare_schema_for_python_jsonschema,
    validate_oscal_ssp_document,
)
from ato_service.ssp_workspace.oscal_export import (
    OscalSspExportError,
    _evidence_href,
    build_draft_oscal_ssp_json_export,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "docs" / "contracts" / "authority-manifest.json"


def _snapshot() -> dict[str, object]:
    return {
        "workspace_id": "11111111-1111-4111-8111-111111111111",
        "revision_id": "22222222-2222-4222-8222-222222222222",
        "content_sha256": "a" * 64,
        "approved_by": "isso@example.gov",
        "approved_at": "2026-07-27T12:00:00Z",
        "system": {
            "display_name": "Grant Intake System",
            "external_system_id": "GIMS-001",
        },
        "profile": {
            "profile_id": "agency-fisma-80053r5-moderate",
            "version": "5.2.0",
            "impact_level": "moderate",
        },
        "sections": [
            {
                "section_id": "boundary",
                "title": "Authorization Boundary",
                "order": 2,
                "state": "reviewed",
                "content": "The boundary includes the application and database.",
            },
            {
                "section_id": "purpose",
                "title": "System Purpose",
                "order": 1,
                "state": "reviewed",
                "content": "The system supports grant intake.",
            },
            {
                "section_id": "extra",
                "title": "Interconnections",
                "order": 3,
                "state": "draft",
                "content": "No interconnections documented yet.",
            },
        ],
        "controls": [
            {
                "control_id": "AC-2",
                "title": "Account Management",
                "state": "reviewed",
                "implementation_status": "implemented",
                "responsibility": "hybrid",
                "implementation_statement": "Agency identity manages accounts.",
                "evidence_links": ["artifact-2", "artifact-1"],
            },
            {
                "control_id": "cp-2",
                "title": "Contingency Plan",
                "state": "draft",
                "implementation_status": "unknown",
                "responsibility": "unknown",
                "implementation_statement": "",
                "evidence_links": [],
            },
            {
                "control_id": "SC-7",
                "title": "Boundary Protection",
                "state": "reviewed",
                "implementation_status": "not_implemented",
                "responsibility": "inherited",
                "implementation_statement": "Provider boundary controls apply.",
                "evidence_links": [],
            },
        ],
        "questions": [
            {
                "question_id": "q-2",
                "target": "AU-11",
                "question": "What is the retention period?",
                "owner_type": "agency",
                "status": "open",
            }
        ],
    }


def _export(**kwargs: object) -> bytes:
    return build_draft_oscal_ssp_json_export(
        _snapshot(),
        include_open_questions=bool(kwargs.get("include_open_questions", False)),
        project_root=ROOT,
    )


def test_representative_export_validates_against_official_schema() -> None:
    payload = json.loads(_export())
    validate_oscal_ssp_document(payload, project_root=ROOT)


def test_export_is_deterministic_for_bytes_uuids_and_order() -> None:
    first = _export()
    second = _export()
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()

    payload = json.loads(first)
    ssp = payload["system-security-plan"]
    requirements = ssp["control-implementation"]["implemented-requirements"]
    assert [item["control-id"] for item in requirements] == ["AC-2", "SC-7", "CP-2"]
    first_link = requirements[0]["links"][0]["href"]
    assert first_link.startswith("urn:ato-analyzer:evidence:")
    assert "artifact-1" not in first_link
    assert "{" not in first_link and " " not in first_link

    first_uuid = ssp["uuid"]
    mutated = copy.deepcopy(_snapshot())
    mutated["revision_id"] = "33333333-3333-4333-8333-333333333333"
    second_payload = json.loads(
        build_draft_oscal_ssp_json_export(
            mutated,
            include_open_questions=False,
            project_root=ROOT,
        )
    )
    assert second_payload["system-security-plan"]["uuid"] != first_uuid


def test_content_hash_change_affects_bound_identifiers() -> None:
    baseline = json.loads(_export())
    baseline_ssp = baseline["system-security-plan"]
    baseline_component = baseline_ssp["system-implementation"]["components"][0]["uuid"]
    baseline_requirement = baseline_ssp["control-implementation"]["implemented-requirements"][
        0
    ]["uuid"]

    mutated = copy.deepcopy(_snapshot())
    mutated["content_sha256"] = "b" * 64
    changed = json.loads(
        build_draft_oscal_ssp_json_export(
            mutated,
            include_open_questions=False,
            project_root=ROOT,
        )
    )
    changed_ssp = changed["system-security-plan"]
    assert changed_ssp["uuid"] == baseline_ssp["uuid"]
    assert (
        changed_ssp["system-implementation"]["components"][0]["uuid"]
        != baseline_component
    )
    assert (
        changed_ssp["control-implementation"]["implemented-requirements"][0]["uuid"]
        != baseline_requirement
    )


def test_inherited_and_unknown_mappings_remain_honest() -> None:
    payload = json.loads(_export())
    requirements = {
        item["control-id"]: item
        for item in payload["system-security-plan"]["control-implementation"][
            "implemented-requirements"
        ]
    }
    inherited = requirements["SC-7"]
    assert "by-components" not in inherited
    assert "fabricated" in inherited["remarks"]
    assert "leveraged authorization" in inherited["remarks"]
    inherited_status_props = [
        prop["name"]
        for prop in inherited["props"]
        if prop["name"].startswith("implementation-status")
    ]
    assert inherited_status_props == ["implementation-status-unmapped"]

    unknown = requirements["CP-2"]
    assert "by-components" not in unknown
    assert any(
        prop["name"] == "implementation-status-unmapped" and prop["value"] == "unknown"
        for prop in unknown["props"]
    )

    hybrid = requirements["AC-2"]
    assert hybrid["by-components"][0]["description"] == (
        "Agency identity manages accounts."
    )
    assert hybrid["by-components"][0]["implementation-status"] == {"state": "implemented"}


def test_missing_core_sections_use_unresolved_text() -> None:
    snapshot = copy.deepcopy(_snapshot())
    snapshot["sections"] = []
    payload = json.loads(
        build_draft_oscal_ssp_json_export(
            snapshot,
            include_open_questions=False,
            project_root=ROOT,
        )
    )
    characteristics = payload["system-security-plan"]["system-characteristics"]
    assert "unresolved" in characteristics["description"].casefold()
    assert "unresolved" in characteristics["authorization-boundary"]["description"].casefold()
    info = characteristics["system-information"]["information-types"][0]
    assert "unresolved" in info["description"].casefold()


def test_open_questions_are_optional_in_back_matter() -> None:
    without = json.loads(_export(include_open_questions=False))
    assert "back-matter" in without["system-security-plan"]
    assert not any(
        resource["title"].startswith("Open question:")
        for resource in without["system-security-plan"]["back-matter"]["resources"]
    )

    with_questions = json.loads(_export(include_open_questions=True))
    titles = [
        resource["title"]
        for resource in with_questions["system-security-plan"]["back-matter"]["resources"]
    ]
    assert any(title.startswith("Open question:") for title in titles)


def test_invalid_authority_digest_fails_closed(tmp_path: Path) -> None:
    _oscal_ssp_validator.cache_clear()
    project_root = tmp_path / "repo"
    shutil.copytree(ROOT / "reference", project_root / "reference")
    contracts = project_root / "docs" / "contracts"
    contracts.mkdir(parents=True)
    shutil.copy2(MANIFEST_PATH, contracts / "authority-manifest.json")
    shutil.copy2(
        ROOT / "docs" / "contracts" / "authority-manifest.schema.json",
        contracts / "authority-manifest.schema.json",
    )
    manifest = json.loads((contracts / "authority-manifest.json").read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        if source["authority_id"] == "nist-oscal-1.2.2":
            source["sha256"] = "0" * 64
    (contracts / "authority-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OscalSspExportError, match="sha256 does not match"):
        build_draft_oscal_ssp_json_export(
            _snapshot(),
            include_open_questions=False,
            project_root=project_root,
        )
    _oscal_ssp_validator.cache_clear()


def test_oscal_export_module_avoids_nondeterministic_sources() -> None:
    source_path = ROOT / "src" / "ato_service" / "ssp_workspace" / "oscal_export.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    forbidden = {"random", "time", "datetime", "uuid4"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_name = alias.name.split(".")[0]
                assert root_name not in forbidden
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root_name = module.split(".")[0]
            assert root_name not in forbidden
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "uuid4":
                pytest.fail("oscal_export must not call uuid4")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "uuid4":
                pytest.fail("oscal_export must not call uuid4")

    schema_path = ROOT / "src" / "ato_service" / "oscal_ssp_schema.py"
    schema_tree = ast.parse(schema_path.read_text(encoding="utf-8"))
    for node in ast.walk(schema_tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in forbidden


def test_validate_oscal_ssp_document_surfaces_schema_errors() -> None:
    with pytest.raises(OscalSspSchemaError, match="system-security-plan"):
        validate_oscal_ssp_document({}, project_root=ROOT)


def test_evidence_locators_with_special_characters_use_safe_hrefs() -> None:
    messy_reference = "{'artifact_id': 'scan 1', \"note\": \"boundary\"}"
    snapshot = copy.deepcopy(_snapshot())
    controls = list(snapshot["controls"])
    controls[0] = dict(controls[0])
    controls[0]["evidence_links"] = [messy_reference, messy_reference, "artifact-1"]
    snapshot["controls"] = controls

    payload = json.loads(
        build_draft_oscal_ssp_json_export(
            snapshot,
            include_open_questions=False,
            project_root=ROOT,
        )
    )
    validate_oscal_ssp_document(payload, project_root=ROOT)

    requirement = payload["system-security-plan"]["control-implementation"][
        "implemented-requirements"
    ][0]
    evidence_props = [
        prop["value"]
        for prop in requirement["props"]
        if prop["name"] == "evidence-reference"
    ]
    assert evidence_props == ["artifact-1", messy_reference]

    for link in requirement["links"]:
        href = link["href"]
        assert href.startswith("urn:ato-analyzer:evidence:")
        assert messy_reference not in href
        assert "{" not in href and '"' not in href and " " not in href

    expected_messy_href = _evidence_href(
        workspace_id=str(snapshot["workspace_id"]),
        content_sha256=str(snapshot["content_sha256"]),
        evidence_reference=messy_reference,
    )
    hrefs = [link["href"] for link in requirement["links"]]
    assert expected_messy_href in hrefs


def test_schema_preparation_rewrites_only_official_token_datatype_pattern() -> None:
    token_schema = {
        "definitions": {
            "TokenDatatype": {
                "type": "string",
                "pattern": r"^(\p{L}|_)(\p{L}|\p{N}|[.\-_])*$",
            }
        }
    }
    prepared = _prepare_schema_for_python_jsonschema(token_schema)
    assert (
        prepared["definitions"]["TokenDatatype"]["pattern"]
        == r"^([A-Za-z_])([A-Za-z0-9._-])*$"
    )

    foreign_schema = {
        "type": "string",
        "pattern": r"^\p{N}+$",
    }
    with pytest.raises(OscalSspSchemaError, match="unsupported Unicode property"):
        _prepare_schema_for_python_jsonschema(foreign_schema)


def test_pinned_oscal_ssp_schema_uses_only_known_token_unicode_pattern() -> None:
    import zipfile

    with zipfile.ZipFile(ROOT / "reference/authorities/nist/oscal-1.2.2.zip") as archive:
        schema = json.loads(archive.read("json/schema/oscal_ssp_schema.json"))

    patterns: set[str] = set()

    def collect(node: object) -> None:
        if isinstance(node, dict):
            pattern = node.get("pattern")
            if isinstance(pattern, str) and re.search(r"\\p\{", pattern):
                patterns.add(pattern)
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    collect(schema)
    assert patterns == {r"^(\p{L}|_)(\p{L}|\p{N}|[.\-_])*$"}
    _prepare_schema_for_python_jsonschema(schema)

