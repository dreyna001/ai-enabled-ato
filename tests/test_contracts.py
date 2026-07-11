"""Deterministic P-1 checks for repository machine contracts."""

from __future__ import annotations

from functools import cache
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import unquote, urlsplit

from jsonschema import FormatChecker, validators


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = ROOT / "docs" / "contracts"
CONTRACT_FIXTURES_DIR = CONTRACTS_DIR / "fixtures"
TRACEABILITY_PATH = ROOT / "docs" / "requirements" / "traceability.yaml"
HARD_STOPS_PATH = ROOT / "docs" / "requirements" / "hard-stops.yaml"
TECHNICAL_SPEC_PATH = ROOT / "ATO_TECHNICAL_SPEC.md"
PRODUCT_PLAN_PATH = ROOT / "ATO_AI_ACCELERATOR_PLAN.md"
EPICS_PATH = ROOT / "ATO_PRODUCT_FUNCTIONALITY_AND_EPICS.md"
README_PATH = ROOT / "README.md"
CONFIGURATION_PATH = ROOT / "docs" / "CONFIGURATION.md"
OPERATIONS_PATH = ROOT / "docs" / "OPERATIONS_AND_RECOVERY.md"
CONTRACT_INDEX_PATH = ROOT / "docs" / "contracts" / "README.md"
P1_GATE_PATH = ROOT / "docs" / "P1_GATE_RECORD.md"
P0_GATE_PATH = ROOT / "docs" / "P0_GATE_RECORD.md"
RUNTIME_DEPLOYMENT_RULE_PATH = (
    ROOT / ".cursor" / "rules" / "ato-runtime-deployment-contract.mdc"
)
FORMAT_CHECKER = FormatChecker()

INTERNAL_SCHEMA_PATHS = tuple(sorted(CONTRACTS_DIR.glob("*.schema.json")))
JSON_SOURCE_DIRS = (
    CONTRACTS_DIR,
    ROOT / "data" / "fixtures",
    ROOT / "reference" / "authorities",
)
FIXTURE_SCHEMA_PATHS = {
    contract: CONTRACTS_DIR / f"{contract}.schema.json"
    for contract in (
        "domain",
        "analysis-profile",
        "content-manifest",
        "artifact-manifest",
        "export-manifest",
        "preflight",
        "runtime-config",
    )
}
FIXTURE_NAME = re.compile(
    rf"^({'|'.join(map(re.escape, FIXTURE_SCHEMA_PATHS))})"
    r"\.(valid|invalid)\.[a-z0-9][a-z0-9._-]*\.json$"
)

REQUIRED_API_METHODS = {
    ("post", "/systems"),
    ("get", "/systems"),
    ("get", "/systems/{system_id}"),
    ("post", "/systems/{system_id}/package-revisions"),
    ("get", "/systems/{system_id}/package-revisions"),
    ("post", "/package-revisions/{id}/files"),
    ("post", "/package-revisions/{id}/finalize"),
    ("post", "/package-revisions/{id}/confirm"),
    ("get", "/package-revisions/{id}"),
    ("get", "/package-revisions/{id}/proposals"),
    ("post", "/proposals/{id}/accept"),
    ("post", "/proposals/{id}/reject"),
    ("post", "/package-revisions/{id}/runs"),
    ("get", "/package-revisions/{id}/runs"),
    ("get", "/runs/{run_id}"),
    ("post", "/runs/{run_id}/cancel"),
    ("get", "/runs/{run_id}/matrix"),
    ("get", "/runs/{run_id}/artifacts"),
    ("post", "/runs/{run_id}/review-revisions"),
    ("post", "/review-revisions/{id}/submit"),
    ("patch", "/review-revisions/{id}/dispositions/{row_id}"),
    ("post", "/review-revisions/{id}/comments"),
    ("get", "/review-revisions/{id}/comments"),
    ("post", "/review-revisions/{id}/export-drafts"),
    ("post", "/export-drafts/{id}/submit"),
    ("post", "/approvals/{id}/approve"),
    ("post", "/approvals/{id}/reject"),
    ("get", "/exports/{id}/download"),
    ("get", "/package-revisions/{id}/search"),
    ("post", "/package-revisions/{id}/chat"),
    ("get", "/health/live"),
    ("get", "/health/ready"),
}

IDEMPOTENCY_KEY_OPERATIONS = {
    ("post", "/systems/{system_id}/package-revisions"),
    ("post", "/package-revisions/{id}/finalize"),
    ("post", "/package-revisions/{id}/runs"),
    ("post", "/review-revisions/{id}/submit"),
    ("post", "/review-revisions/{id}/export-drafts"),
    ("post", "/approvals/{id}/approve"),
    ("post", "/approvals/{id}/reject"),
    ("get", "/exports/{id}/download"),
}

IF_MATCH_OPERATIONS = {
    ("post", "/package-revisions/{id}/confirm"),
    ("post", "/proposals/{id}/accept"),
    ("post", "/proposals/{id}/reject"),
    ("post", "/review-revisions/{id}/submit"),
    ("patch", "/review-revisions/{id}/dispositions/{row_id}"),
    ("post", "/export-drafts/{id}/submit"),
}

REQUIRED_TRACEABILITY_FIELDS = {
    "requirement_id",
    "spec_section",
    "requirement_text",
    "epic",
    "implementation_files",
    "test_ids",
    "verification_type",
    "status",
}
REQUIRED_RELEASE_REQUIREMENT_IDS = {f"R-{number:03d}" for number in range(1, 24)}
REQUIRED_P0_REQUIREMENT_IDS = {f"P0-{number:03d}" for number in range(1, 16)}
REQUIRED_P1_REQUIREMENT_IDS = {f"P1-{number:03d}" for number in range(1, 12)}
REQUIRED_HARD_STOP_IDS = {f"HS-{number:03d}" for number in range(1, 11)}
REQUIRED_HARD_STOP_FIELDS = {
    "hard_stop_id",
    "condition",
    "blocked_work",
    "status",
    "owner",
    "evidence_paths",
    "notes",
}
ALLOWED_HARD_STOP_STATUSES = {
    "open",
    "resolved",
    "out_of_scope",
    "using_default",
}


@cache
def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_paths() -> tuple[Path, ...]:
    return tuple(
        sorted(
            {
                path
                for source_dir in JSON_SOURCE_DIRS
                if source_dir.is_dir()
                for path in source_dir.rglob("*.json")
            }
        )
    )


def _validator_for(schema_path: Path):
    schema = _load_json(schema_path)
    validator_class = validators.validator_for(schema, default=None)
    assert validator_class is not None, (
        f"{schema_path.relative_to(ROOT)} declares unsupported metaschema "
        f"{schema.get('$schema')!r}"
    )
    validator_class.check_schema(schema)
    return validator_class(schema, format_checker=FORMAT_CHECKER)


def _resolve_json_pointer(document: Any, fragment: str, ref: str) -> Any:
    if not fragment:
        return document
    pointer = unquote(fragment)
    assert pointer.startswith("/"), f"unsupported non-pointer JSON reference: {ref}"

    target = document
    for encoded_token in pointer[1:].split("/"):
        token = encoded_token.replace("~1", "/").replace("~0", "~")
        if isinstance(target, dict):
            assert token in target, f"missing JSON reference token {token!r}: {ref}"
            target = target[token]
        elif isinstance(target, list):
            assert token.isdigit(), f"invalid array reference token {token!r}: {ref}"
            index = int(token)
            assert index < len(target), f"array reference is out of range: {ref}"
            target = target[index]
        else:
            raise AssertionError(f"JSON reference traverses a scalar: {ref}")
    return target


def _resolve_local_ref(document_path: Path, ref: str) -> tuple[Path, str, Any]:
    parsed = urlsplit(ref)
    assert not parsed.scheme and not parsed.netloc, f"remote OpenAPI reference: {ref}"
    assert not parsed.query, f"OpenAPI reference must not contain a query: {ref}"

    relative_path = unquote(parsed.path)
    target_path = (
        document_path if not relative_path else document_path.parent / relative_path
    ).resolve()
    try:
        target_path.relative_to(ROOT)
    except ValueError as error:
        raise AssertionError(f"OpenAPI reference escapes repository: {ref}") from error

    assert target_path.is_file(), f"missing OpenAPI reference target: {ref}"
    document = _load_json(target_path)
    return target_path, parsed.fragment, _resolve_json_pointer(
        document, parsed.fragment, ref
    )


def _walk_openapi_refs(
    document_path: Path,
    node: Any,
    visited: set[tuple[Path, str]],
) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if ref is not None:
            assert isinstance(ref, str) and ref, "$ref must be a non-empty string"
            target_path, fragment, target = _resolve_local_ref(document_path, ref)
            target_key = (target_path, fragment)
            if target_key not in visited:
                visited.add(target_key)
                _walk_openapi_refs(target_path, target, visited)
        for key, child in node.items():
            if key != "$ref":
                _walk_openapi_refs(document_path, child, visited)
    elif isinstance(node, list):
        for child in node:
            _walk_openapi_refs(document_path, child, visited)


def _operation_parameters(openapi: dict[str, Any], method: str, path: str) -> set[str]:
    path_item = openapi["paths"][path]
    parameters = [
        *path_item.get("parameters", []),
        *path_item[method].get("parameters", []),
    ]
    names: set[str] = set()
    for parameter in parameters:
        if "$ref" in parameter:
            names.add(parameter["$ref"].rsplit("/", 1)[-1])
        else:
            names.add(parameter["name"])
    return names


def _closed_values(
    schema: dict[str, Any], document_path: Path | None = None
) -> set[Any]:
    if "$ref" in schema:
        assert document_path is not None, "document path is required to resolve $ref"
        target_path, _, target = _resolve_local_ref(document_path, schema["$ref"])
        assert isinstance(target, dict), f"closed enum reference is not a schema: {schema}"
        return _closed_values(target, target_path)
    values = schema.get("enum", schema.get("const"))
    assert isinstance(values, list), f"expected a closed enum, got {schema!r}"
    assert len(values) == len(set(values)), f"closed enum contains duplicates: {values!r}"
    return set(values)


def _traceability_records() -> list[dict[str, Any]]:
    assert TRACEABILITY_PATH.is_file(), (
        "missing JSON-compatible docs/requirements/traceability.yaml"
    )
    document = _load_json(TRACEABILITY_PATH)
    records = document.get("requirements") if isinstance(document, dict) else document
    assert isinstance(records, list), "traceability must be a list or contain requirements"
    assert records, "traceability requirements must not be empty"
    assert all(isinstance(record, dict) for record in records)
    return records


def _hard_stop_records() -> list[dict[str, Any]]:
    assert HARD_STOPS_PATH.is_file(), (
        "missing JSON-compatible docs/requirements/hard-stops.yaml"
    )
    document = _load_json(HARD_STOPS_PATH)
    records = document.get("entries") if isinstance(document, dict) else None
    assert isinstance(records, list) and records, (
        "hard-stop register must contain non-empty entries"
    )
    assert all(isinstance(record, dict) for record in records)
    return records


def _assert_repository_reference_exists(reference: str, requirement_id: str) -> None:
    path_text = reference.split("::", 1)[0].split("#", 1)[0]
    assert path_text, f"{requirement_id} contains an empty path reference"
    candidate = (ROOT / path_text).resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError as error:
        raise AssertionError(
            f"{requirement_id} path escapes repository: {reference}"
        ) from error
    assert candidate.exists(), f"{requirement_id} references missing path: {reference}"
    if "::" in reference:
        test_name = reference.rsplit("::", 1)[-1]
        assert candidate.suffix == ".py" and re.search(
            rf"^def {re.escape(test_name)}\(",
            candidate.read_text(encoding="utf-8"),
            re.MULTILINE,
        ), f"{requirement_id} references missing test: {reference}"


def test_repository_contract_and_fixture_json_parses() -> None:
    paths = _json_paths()
    assert paths, "no repository JSON contracts or fixtures found"
    for path in paths:
        _load_json(path)
    _traceability_records()
    _hard_stop_records()


def test_internal_schemas_validate_against_declared_metaschemas() -> None:
    assert INTERNAL_SCHEMA_PATHS, "no internal JSON Schemas found"
    for schema_path in INTERNAL_SCHEMA_PATHS:
        schema = _load_json(schema_path)
        assert isinstance(schema.get("$schema"), str), (
            f"{schema_path.relative_to(ROOT)} does not declare $schema"
        )
        _validator_for(schema_path)


def test_authority_manifest_validates_and_matches_local_bytes() -> None:
    manifest = _load_json(CONTRACTS_DIR / "authority-manifest.json")
    _validator_for(CONTRACTS_DIR / "authority-manifest.schema.json").validate(manifest)

    authority_ids = [source["authority_id"] for source in manifest["sources"]]
    assert len(authority_ids) == len(set(authority_ids)), "duplicate authority_id"

    for source in manifest["sources"]:
        local_path = source["local_path"]
        assert isinstance(local_path, str), (
            f"{source['authority_id']} must declare local_path"
        )
        authority_path = (ROOT / local_path).resolve()
        try:
            authority_path.relative_to(ROOT)
        except ValueError as error:
            raise AssertionError(
                f"{source['authority_id']} local_path escapes repository"
            ) from error
        assert authority_path.is_file(), f"missing authority file: {local_path}"
        content = authority_path.read_bytes()
        assert len(content) == source["size_bytes"], (
            f"{source['authority_id']} size_bytes does not match {local_path}"
        )
        assert hashlib.sha256(content).hexdigest() == source["sha256"], (
            f"{source['authority_id']} sha256 does not match {local_path}"
        )


def test_contract_fixtures_follow_contracts() -> None:
    assert CONTRACT_FIXTURES_DIR.is_dir(), "missing docs/contracts/fixtures"
    fixtures: dict[tuple[str, str], list[Path]] = {
        (contract, outcome): []
        for contract in FIXTURE_SCHEMA_PATHS
        for outcome in ("valid", "invalid")
    }
    unexpected: list[str] = []
    for fixture_path in sorted(CONTRACT_FIXTURES_DIR.glob("*.json")):
        match = FIXTURE_NAME.fullmatch(fixture_path.name)
        if match is None:
            unexpected.append(fixture_path.name)
            continue
        fixtures[(match.group(1), match.group(2))].append(fixture_path)
    assert not unexpected, f"contract fixtures violate naming convention: {unexpected}"
    assert all(fixtures.values()), (
        "each contract needs valid and invalid fixtures; missing "
        f"{[key for key, paths in fixtures.items() if not paths]}"
    )

    for (contract, outcome), fixture_paths in fixtures.items():
        validator = _validator_for(FIXTURE_SCHEMA_PATHS[contract])
        for fixture_path in fixture_paths:
            errors = list(validator.iter_errors(_load_json(fixture_path)))
            if outcome == "valid":
                assert not errors, (
                    f"{fixture_path.name} must validate: {errors[0].message}"
                )
            else:
                assert errors, f"{fixture_path.name} must be rejected by {contract}"


def test_openapi_references_are_local_and_resolve_recursively() -> None:
    openapi_path = CONTRACTS_DIR / "openapi.json"
    _walk_openapi_refs(openapi_path, _load_json(openapi_path), set())


def test_openapi_minimum_methods_and_health_root_override() -> None:
    openapi = _load_json(CONTRACTS_DIR / "openapi.json")
    assert openapi["info"]["version"] == "1.0.0"
    actual_methods = {
        (method, path)
        for path, path_item in openapi["paths"].items()
        for method in path_item
        if method in {"get", "post", "put", "patch", "delete"}
    }
    assert not (REQUIRED_API_METHODS - actual_methods), (
        f"missing minimum API methods: {sorted(REQUIRED_API_METHODS - actual_methods)}"
    )
    assert openapi["servers"] == [{"url": "/api/v1"}]

    for health_path in ("/health/live", "/health/ready"):
        path_item = openapi["paths"][health_path]
        servers = path_item["get"].get("servers", path_item.get("servers"))
        assert isinstance(servers, list) and [server.get("url") for server in servers] == ["/"], (
            f"{health_path} must override the /api/v1 server with the root server"
        )


def test_openapi_idempotency_concurrency_and_security_contracts() -> None:
    openapi = _load_json(CONTRACTS_DIR / "openapi.json")
    parameters = openapi["components"]["parameters"]
    assert parameters["IdempotencyKey"]["required"] is True
    assert parameters["IdempotencyKey"]["name"] == "Idempotency-Key"
    assert parameters["IfMatch"]["required"] is True
    assert parameters["IfMatch"]["name"] == "If-Match"

    for method, path in IDEMPOTENCY_KEY_OPERATIONS:
        assert "IdempotencyKey" in _operation_parameters(openapi, method, path), (
            f"{method.upper()} {path} must require Idempotency-Key"
        )
    for method, path in IF_MATCH_OPERATIONS:
        assert "IfMatch" in _operation_parameters(openapi, method, path), (
            f"{method.upper()} {path} must require If-Match"
        )
        assert "412" in openapi["paths"][path][method]["responses"], (
            f"{method.upper()} {path} must declare stale-write response 412"
        )

    assert openapi["security"] == [{"sessionCookie": []}]
    session_cookie = openapi["components"]["securitySchemes"]["sessionCookie"]
    assert session_cookie["type"] == "apiKey"
    assert session_cookie["in"] == "cookie"
    assert session_cookie["name"] == "__Host-ato_session"
    for path, path_item in openapi["paths"].items():
        for method in {"post", "put", "patch", "delete"} & path_item.keys():
            assert "CsrfToken" in _operation_parameters(openapi, method, path), (
                f"{method.upper()} {path} must require X-CSRF-Token"
            )
            assert path_item[method].get("security") != [], (
                f"{method.upper()} {path} must not disable authentication"
            )
    for health_path in ("/health/live", "/health/ready"):
        assert openapi["paths"][health_path]["get"]["security"] == []


def test_duplicated_closed_enums_are_synchronized() -> None:
    domain = _load_json(CONTRACTS_DIR / "domain.schema.json")
    profile = _load_json(CONTRACTS_DIR / "analysis-profile.schema.json")
    export_manifest = _load_json(CONTRACTS_DIR / "export-manifest.schema.json")
    preflight = _load_json(CONTRACTS_DIR / "preflight.schema.json")
    runtime_config = _load_json(CONTRACTS_DIR / "runtime-config.schema.json")
    openapi_path = CONTRACTS_DIR / "openapi.json"
    openapi = _load_json(openapi_path)
    domain_defs = domain["$defs"]
    profile_defs = profile["$defs"]
    create_revision = openapi["components"]["schemas"]["CreatePackageRevisionRequest"][
        "properties"
    ]

    assert (
        _closed_values(domain_defs["ProfileId"])
        == _closed_values(profile["properties"]["profile_id"])
        == _closed_values(export_manifest["properties"]["profile_id"])
        == _closed_values(
            preflight["$defs"]["profileFingerprint"]["properties"]["profile_id"]
        )
        == _closed_values(create_revision["profile_id"], openapi_path)
    )
    assert _closed_values(runtime_config["properties"]["runtime_profile"]) == {
        "dev_local",
        "onprem_production",
    }
    assert _closed_values(runtime_config["$defs"]["endpointProfile"]) == {
        "mock",
        "external_openai",
        "internal_openai_compatible",
    }
    assert _closed_values(runtime_config["properties"]["TEXT_MODEL_PROVIDER"]) == {
        "openai_compatible",
        "aws_bedrock",
    }
    for property_name in ("certification_class", "impact_level"):
        assert (
            _closed_values(domain_defs["PackageRevision"]["properties"][property_name])
            == _closed_values(profile["properties"][property_name])
            == _closed_values(create_revision[property_name], openapi_path)
        )
    for definition_name, property_name in (
        ("DataOrigin", "data_origin"),
        ("Sensitivity", "sensitivity"),
    ):
        assert _closed_values(domain_defs[definition_name]) == _closed_values(
            create_revision[property_name], openapi_path
        )

    upload_kind = openapi["paths"][
        "/package-revisions/{id}/files"
    ]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]["properties"][
        "artifact_kind"
    ]
    assert _closed_values(
        domain_defs["SourceArtifact"]["properties"]["artifact_kind"]
    ) == _closed_values(upload_kind, openapi_path)
    assert _closed_values(
        domain_defs["AnalysisRun"]["properties"]["run_type"]
    ) == _closed_values(
        openapi["components"]["schemas"]["StartRunRequest"]["properties"]["run_type"],
        openapi_path,
    )
    assert _closed_values(
        domain_defs["Disposition"]["properties"]["decision"]
    ) == _closed_values(
        openapi["components"]["schemas"]["DispositionRequest"]["properties"]["decision"],
        openapi_path,
    )

    assessment_item_types = _closed_values(
        domain_defs["MatrixRow"]["properties"]["assessment_item_type"]
    )
    assert assessment_item_types == _closed_values(
        profile_defs["assessmentItem"]["properties"]["assessment_item_type"]
    )

    statuses = _closed_values(
        domain_defs["MatrixRow"]["properties"]["model_proposed_status"]
    )
    assert statuses == _closed_values(
        domain_defs["MatrixRow"]["properties"]["system_status"]
    )
    assert statuses == _closed_values(
        profile_defs["statusPolicy"]["properties"]["allowed_statuses"]
    )
    matrix_status = next(
        parameter["schema"]
        for parameter in openapi["paths"]["/runs/{run_id}/matrix"]["get"]["parameters"]
        if parameter.get("name") == "status"
    )
    assert statuses == _closed_values(matrix_status)


def test_runtime_deployment_contract_is_persistent_across_active_plans() -> None:
    required_fragments = {
        TECHNICAL_SPEC_PATH: (
            "The runtime/deployment contract is cross-cutting after P0.",
            "Capability bundles or presets MUST NOT be introduced",
            "`tests/test_deployment_contract.py`",
        ),
        PRODUCT_PLAN_PATH: (
            "one schema-validated runtime JSON",
            "The historical Block 1 developer CLI is retired.",
            "deployment-contract tests",
        ),
        EPICS_PATH: (
            "Every epic preserves the cross-cutting runtime/deployment contract",
            "Use `config.env`, capability bundles",
            "static deployment-contract tests",
        ),
        README_PATH: (
            "Every future phase must preserve the cross-cutting "
            "runtime/deployment contract",
            "not proof of RHEL validation or production release",
        ),
        CONFIGURATION_PATH: (
            "Every new capability flag must be added with its schema",
            "Each future process receives only the credential mappings",
            "There is no `config.env`",
        ),
        OPERATIONS_PATH: (
            "This table is the target topology.",
            "The current scaffold ships only `ato-api.service`",
            "Completing upgrade, rollback, backup, restore",
        ),
        CONTRACT_INDEX_PATH: (
            "Runtime/deployment values and behavior form one contract",
            "python -m pytest tests/test_deployment_contract.py",
        ),
        TRACEABILITY_PATH: (
            '"requirement_id": "R-023"',
            '"epic": "Cross-cutting: EP-00-contracts through '
            'EP-08-onprem-release"',
        ),
        P1_GATE_PATH: (
            "Runtime configuration contract",
            "API-only deployment scaffold",
            "tests/test_deployment_contract.py",
        ),
        P0_GATE_PATH: (
            "Post-gate runtime/deployment baseline synchronization",
            "does not claim a worker, portal",
            "tests/test_deployment_contract.py",
        ),
        RUNTIME_DEPLOYMENT_RULE_PATH: (
            "alwaysApply: true",
            "Do not introduce `config.env`",
            "Do not add worker, portal, timer",
        ),
    }

    for path, fragments in required_fragments.items():
        text = path.read_text(encoding="utf-8")
        missing = [fragment for fragment in fragments if fragment not in text]
        assert not missing, (
            f"{path.relative_to(ROOT)} is missing runtime/deployment rules: {missing}"
        )

    product_plan = PRODUCT_PLAN_PATH.read_text(encoding="utf-8")
    assert "Harden the existing Block 1 analyzer" not in product_plan
    assert "The current CLI is reused and hardened" not in product_plan

    records_by_id = {
        record["requirement_id"]: record for record in _traceability_records()
    }
    for requirement_id in ("R-022", "R-023"):
        record = records_by_id[requirement_id]
        assert record["implementation_files"]
        assert record["test_ids"]
        for reference in record["implementation_files"] + record["test_ids"]:
            _assert_repository_reference_exists(reference, requirement_id)

    hard_stops_by_id = {
        record["hard_stop_id"]: record for record in _hard_stop_records()
    }
    runtime_evidence = {"docs/CONFIGURATION.md", "deployment/README.md"}
    for hard_stop_id in ("HS-003", "HS-004", "HS-005", "HS-008"):
        assert runtime_evidence <= set(hard_stops_by_id[hard_stop_id]["evidence_paths"])


def test_traceability_fields_ids_and_implemented_paths() -> None:
    records = _traceability_records()
    requirement_ids: list[str] = []
    for record in records:
        missing_fields = REQUIRED_TRACEABILITY_FIELDS - record.keys()
        assert not missing_fields, (
            f"traceability record is missing fields {sorted(missing_fields)}: {record}"
        )
        requirement_id = record["requirement_id"]
        assert isinstance(requirement_id, str) and requirement_id
        requirement_ids.append(requirement_id)
        for field in (
            "spec_section",
            "requirement_text",
            "epic",
            "verification_type",
            "status",
        ):
            assert isinstance(record[field], str) and record[field], (
                f"{requirement_id} must provide {field}"
            )
        for field in ("implementation_files", "test_ids"):
            assert isinstance(record[field], list), (
                f"{requirement_id} {field} must be a list"
            )
            assert all(isinstance(value, str) and value for value in record[field])

        if record["status"] == "implemented":
            assert record["implementation_files"], (
                f"{requirement_id} is implemented without implementation evidence"
            )
            for field in ("implementation_files", "test_ids"):
                for reference in record[field]:
                    _assert_repository_reference_exists(reference, requirement_id)

    assert len(requirement_ids) == len(set(requirement_ids)), (
        "traceability contains duplicate requirement_id values"
    )
    ids = set(requirement_ids)
    assert not (REQUIRED_RELEASE_REQUIREMENT_IDS - ids), (
        "traceability is missing release requirements: "
        f"{sorted(REQUIRED_RELEASE_REQUIREMENT_IDS - ids)}"
    )
    assert not (REQUIRED_P0_REQUIREMENT_IDS - ids), (
        f"traceability is missing P0 requirements: {sorted(REQUIRED_P0_REQUIREMENT_IDS - ids)}"
    )
    assert not (REQUIRED_P1_REQUIREMENT_IDS - ids), (
        f"traceability is missing P1 requirements: {sorted(REQUIRED_P1_REQUIREMENT_IDS - ids)}"
    )
    records_by_id = {record["requirement_id"]: record for record in records}
    incomplete_p1 = sorted(
        requirement_id
        for requirement_id in REQUIRED_P1_REQUIREMENT_IDS
        if records_by_id[requirement_id]["status"] != "implemented"
    )
    assert not incomplete_p1, (
        f"P-1 gate is recorded as complete but requirements remain incomplete: {incomplete_p1}"
    )


def test_hard_stop_register_is_complete_and_evidence_backed() -> None:
    records = _hard_stop_records()
    hard_stop_ids: list[str] = []
    for record in records:
        missing_fields = REQUIRED_HARD_STOP_FIELDS - record.keys()
        assert not missing_fields, (
            f"hard-stop record is missing fields {sorted(missing_fields)}: {record}"
        )
        hard_stop_id = record["hard_stop_id"]
        assert isinstance(hard_stop_id, str) and hard_stop_id
        hard_stop_ids.append(hard_stop_id)
        for field in ("condition", "blocked_work", "owner", "notes"):
            assert isinstance(record[field], str) and record[field], (
                f"{hard_stop_id} must provide {field}"
            )
        assert record["status"] in ALLOWED_HARD_STOP_STATUSES, (
            f"{hard_stop_id} has unsupported status {record['status']!r}"
        )
        evidence_paths = record["evidence_paths"]
        assert isinstance(evidence_paths, list) and evidence_paths, (
            f"{hard_stop_id} must provide evidence_paths"
        )
        for reference in evidence_paths:
            assert isinstance(reference, str) and reference
            _assert_repository_reference_exists(reference, hard_stop_id)

    assert len(hard_stop_ids) == len(set(hard_stop_ids)), (
        "hard-stop register contains duplicate IDs"
    )
    assert set(hard_stop_ids) == REQUIRED_HARD_STOP_IDS, (
        "hard-stop register IDs do not match HS-001 through HS-010"
    )
    statuses = {record["hard_stop_id"]: record["status"] for record in records}
    assert statuses["HS-007"] == "out_of_scope"
    assert statuses["HS-010"] == "using_default"

    technical_spec = (ROOT / "ATO_TECHNICAL_SPEC.md").read_text(encoding="utf-8")
    for hard_stop_id in REQUIRED_HARD_STOP_IDS:
        assert hard_stop_id in technical_spec, (
            f"{hard_stop_id} is absent from the normative specification"
        )

    authority_manifest = _load_json(CONTRACTS_DIR / "authority-manifest.json")
    if authority_manifest["status"] == "reviewed":
        assert all(
            source["review_status"] == "reviewed"
            for source in authority_manifest["sources"]
        )
        assert statuses["HS-001"] == "resolved"
    else:
        assert statuses["HS-001"] == "open"


def test_package_revision_content_manifest_sha256_nullability() -> None:
    domain_schema_path = CONTRACTS_DIR / "domain.schema.json"
    validator = _validator_for(domain_schema_path)
    package_revision = _load_json(
        CONTRACT_FIXTURES_DIR / "domain.valid.uploading-null-manifest.json"
    )
    validator.validate(package_revision)

    ready_with_null = dict(package_revision)
    ready_with_null["status"] = "ready"
    errors = list(validator.iter_errors(ready_with_null))
    assert errors, "ready revisions with null content_manifest_sha256 must be rejected"

    ready_fixture = _load_json(
        CONTRACT_FIXTURES_DIR / "domain.invalid.ready-null-manifest.json"
    )
    assert list(validator.iter_errors(ready_fixture)), (
        "domain.invalid.ready-null-manifest.json must be rejected"
    )


def _minimal_onprem_runtime_config() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "runtime_profile": "onprem_production",
        "TEXT_MODEL_ENDPOINT_URL": "https://models.example.internal/v1",
        "TEXT_MODEL_NAME": "fixture-text-model",
        "TEXT_MODEL_CONTEXT_TOKENS": 8192,
        "TEXT_MODEL_MAX_OUTPUT_TOKENS": 1024,
        "TEXT_MODEL_TIMEOUT_SECONDS": 30,
        "TEXT_MODEL_MAX_RETRIES": 2,
        "TEXT_MODEL_ENDPOINT_PROFILE": "internal_openai_compatible",
        "VISION_MODEL_ENABLED": False,
        "VISION_MODEL_ENDPOINT_URL": "https://models.example.internal/v1",
        "VISION_MODEL_NAME": "fixture-vision-model",
        "VISION_MODEL_CONTEXT_TOKENS": 4096,
        "VISION_MODEL_ENDPOINT_PROFILE": "internal_openai_compatible",
        "MODEL_ENDPOINT_ALLOWLIST": [
            {"host": "models.example.internal", "port": 443}
        ],
        "MAX_MODEL_CALLS_PER_RUN": 120,
        "MAX_MODEL_INPUT_TOKENS_PER_RUN": 100000,
        "MAX_MODEL_OUTPUT_TOKENS_PER_RUN": 20000,
        "MAX_PACKAGE_BYTES": 2147483648,
        "MAX_SINGLE_FILE_BYTES": 104857600,
        "MAX_FILES_PER_REVISION": 500,
        "MAX_ASSESSMENT_ITEMS": 500,
        "MAX_EVIDENCE_ITEMS": 2000,
        "MAX_PDF_PAGES_PER_FILE": 200,
        "MAX_EXTRACTED_TEXT_CHARACTERS_PER_FILE": 2000000,
        "MAX_CONCURRENT_ANALYSIS_RUNS": 2,
        "MAX_ACTIVE_ANALYZER_WORKERS": 2,
        "JOB_HEARTBEAT_SECONDS": 30,
        "JOB_LEASE_SECONDS": 300,
        "CHAT_MAX_RETRIEVED_CHUNKS": 8,
        "CHAT_RATE_LIMIT_PER_USER": {
            "max_requests": 30,
            "window_seconds": 60,
        },
        "CHAT_INPUT_LIMIT": {"value": 4000, "unit": "characters"},
        "CHAT_TURN_LIMIT": 20,
        "CHAT_DAILY_TOKEN_LIMIT_PER_USER": 100000,
        "STORAGE_DATA_PATH": "/var/ato-packages",
        "STORAGE_WARNING_PERCENT": 80,
        "STORAGE_REJECTION_PERCENT": 90,
        "DATABASE_ENGINE": "postgresql",
        "DATABASE_MAJOR_VERSION": 16,
        "DATABASE_LISTEN_MODE": "loopback",
        "DATABASE_DSN_CREDENTIAL_REFERENCE": {
            "source": "systemd_credential",
            "identifier": "database-dsn",
        },
        "IDENTITY_PROVIDER_MODE": "oidc",
        "OIDC_ISSUER_URL": "https://idp.example.internal/",
        "OIDC_AUDIENCE": "ato-analyzer",
        "OIDC_CLIENT_CREDENTIAL_REFERENCE": {
            "source": "systemd_credential",
            "identifier": "oidc-client-secret",
        },
        "SESSION_IDLE_TIMEOUT_MINUTES": 30,
        "SESSION_ABSOLUTE_TIMEOUT_HOURS": 8,
        "LOCAL_PASSWORD_AUTH_ENABLED": False,
        "MALWARE_SCANNER_ENABLED": True,
        "MALWARE_SCANNER_ID": "fixture-scanner",
        "MALWARE_SCANNER_FAILURE_POLICY": "fail_closed",
        "RETENTION_YEARS": 7,
        "APPROVAL_EXPIRY_DAYS": 7,
        "AUDIT_CHAIN_ALGORITHM": "HMAC-SHA-256",
        "AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE": {
            "source": "systemd_credential",
            "identifier": "audit-hmac-key",
        },
        "AUDIT_ROOT_BACKUP_FREQUENCY": "daily",
        "BACKUP_RTO_HOURS": 4,
        "BACKUP_RPO_HOURS": 1,
        "BACKUP_POSTGRES_WAL_ARCHIVING_ENABLED": True,
        "BACKUP_FILESYSTEM_SNAPSHOT_INTERVAL_MINUTES": 60,
        "BACKUP_FULL_FREQUENCY": "daily",
        "BACKUP_OFF_HOST_ENABLED": True,
        "BACKUP_ENCRYPTION_ENABLED": True,
        "BACKUP_KEY_OWNERSHIP": "customer",
        "BACKUP_ENCRYPTION_KEY_CREDENTIAL_REFERENCE": {
            "source": "systemd_credential",
            "identifier": "backup-encryption-key",
        },
        "BACKUP_ONLINE_RETENTION_DAYS": 90,
        "BACKUP_RESTORE_DRILL_FREQUENCY": "quarterly",
    }


def test_runtime_config_onprem_requires_database_dsn_credential_reference() -> None:
    runtime_schema_path = CONTRACTS_DIR / "runtime-config.schema.json"
    runtime_schema = _load_json(runtime_schema_path)
    validator = _validator_for(runtime_schema_path)

    onprem_required = next(
        branch["then"]["required"]
        for branch in runtime_schema["allOf"]
        if branch.get("if", {})
        .get("properties", {})
        .get("runtime_profile", {})
        .get("const")
        == "onprem_production"
    )
    assert "DATABASE_DSN_CREDENTIAL_REFERENCE" in onprem_required

    complete_config = _minimal_onprem_runtime_config()
    validator.validate(complete_config)

    missing_dsn = dict(complete_config)
    missing_dsn.pop("DATABASE_DSN_CREDENTIAL_REFERENCE")
    assert list(validator.iter_errors(missing_dsn)), (
        "onprem_production without DATABASE_DSN_CREDENTIAL_REFERENCE must be rejected"
    )


def test_frozen_contract_amendment_cross_doc_consistency() -> None:
    domain_schema = _load_json(CONTRACTS_DIR / "domain.schema.json")
    runtime_schema = _load_json(CONTRACTS_DIR / "runtime-config.schema.json")
    technical_spec = (ROOT / "ATO_TECHNICAL_SPEC.md").read_text(encoding="utf-8")
    lifecycle = (CONTRACTS_DIR / "LIFECYCLE_AND_ERRORS.md").read_text(encoding="utf-8")
    operations = (ROOT / "docs" / "OPERATIONS_AND_RECOVERY.md").read_text(
        encoding="utf-8"
    )

    package_revision = domain_schema["$defs"]["PackageRevision"]
    assert "content_manifest_sha256" in package_revision["required"]
    assert (
        package_revision["properties"]["content_manifest_sha256"]["$ref"]
        == "#/$defs/Sha256OrNull"
    )
    assert "Sha256OrNull" in domain_schema["$defs"]

    assert "content_manifest_sha256: sha256 | null" in technical_spec
    assert "uploading -> scanning" in lifecycle
    assert "atomically sets" in lifecycle
    assert "DATABASE_DSN_CREDENTIAL_REFERENCE" in technical_spec
    assert "DATABASE_DSN_CREDENTIAL_REFERENCE" in operations
    assert "DATABASE_DSN_CREDENTIAL_REFERENCE" in runtime_schema["properties"]
    assert (
        runtime_schema["properties"]["DATABASE_DSN_CREDENTIAL_REFERENCE"]["$ref"]
        == "#/$defs/credentialReference"
    )


def _lifecycle_error_taxonomy_section() -> str:
    lifecycle = (CONTRACTS_DIR / "LIFECYCLE_AND_ERRORS.md").read_text(encoding="utf-8")
    section_start = lifecycle.index("### 4.1")
    section_end = lifecycle.index("## 5.")
    return lifecycle[section_start:section_end]


def _documented_error_codes_from_lifecycle_section(section: str) -> set[str]:
    return set(
        re.findall(
            r"^\| `([a-z][a-z0-9_]{2,127})` \|",
            section,
            re.MULTILINE,
        )
    )


def _operations_readiness_probe_section() -> str:
    operations = (ROOT / "docs" / "OPERATIONS_AND_RECOVERY.md").read_text(
        encoding="utf-8"
    )
    section_start = operations.index("Readiness probe keys are a closed set:")
    section_end = operations.index(
        "- When the pinned authority manifest is present",
        section_start,
    )
    return operations[section_start:section_end]


def test_readiness_check_names_match_operations_contract() -> None:
    from ato_service.health import READINESS_CHECK_NAMES

    section = _operations_readiness_probe_section()
    documented = set(
        re.findall(
            r"^\| `([a-z_]+)` \|",
            section,
            re.MULTILINE,
        )
    )
    implemented = set(READINESS_CHECK_NAMES)
    assert documented == implemented, (
        "operations readiness probe keys drifted from implementation: "
        f"documented={sorted(documented)} implemented={sorted(implemented)}"
    )


def test_implemented_problem_codes_are_documented_in_error_taxonomy() -> None:
    from ato_service.problems import KNOWN_ERROR_CODES

    documented = _documented_error_codes_from_lifecycle_section(
        _lifecycle_error_taxonomy_section()
    )
    undocumented = KNOWN_ERROR_CODES - documented
    assert not undocumented, (
        "implemented Problem error_code values must appear in "
        f"LIFECYCLE_AND_ERRORS.md Section 4: {sorted(undocumented)}"
    )


def _lifecycle_markdown() -> str:
    return (CONTRACTS_DIR / "LIFECYCLE_AND_ERRORS.md").read_text(encoding="utf-8")


def _job_status_enum_from_lifecycle() -> set[str]:
    lifecycle = _lifecycle_markdown()
    section_start = lifecycle.index("Closed job `status` enum")
    section_end = lifecycle.index("#### 2.7.1", section_start)
    section = lifecycle[section_start:section_end]
    allowed = {
        "available",
        "leased",
        "completed",
        "failed",
        "reconciliation_required",
    }
    found = {match for match in re.findall(r"`([a-z_]+)`", section) if match in allowed}
    assert found == allowed, (
        f"lifecycle job status table drifted: found={sorted(found)} expected={sorted(allowed)}"
    )
    return found


def test_job_status_enum_matches_lifecycle_contract() -> None:
    domain = _load_json(CONTRACTS_DIR / "domain.schema.json")
    schema_statuses = _closed_values(domain["$defs"]["Job"]["properties"]["status"])
    lifecycle_statuses = _job_status_enum_from_lifecycle()
    assert schema_statuses == lifecycle_statuses
    assert schema_statuses == {
        "available",
        "leased",
        "completed",
        "failed",
        "reconciliation_required",
    }

    validator = _validator_for(CONTRACTS_DIR / "domain.schema.json")
    for fixture_name in (
        "domain.valid.job-available.json",
        "domain.valid.job-leased.json",
        "domain.valid.job-attempt-active.json",
    ):
        validator.validate(_load_json(CONTRACT_FIXTURES_DIR / fixture_name))

    invalid_job = _load_json(
        CONTRACT_FIXTURES_DIR / "domain.invalid.job-leased-missing-lease.json"
    )
    assert list(validator.iter_errors(invalid_job)), (
        "leased jobs without lease_owner and timestamps must be rejected"
    )


def test_job_and_job_attempt_schema_required_fields() -> None:
    domain = _load_json(CONTRACTS_DIR / "domain.schema.json")
    job = domain["$defs"]["Job"]
    attempt = domain["$defs"]["JobAttempt"]

    assert job["properties"]["object_type"]["const"] == "job"
    assert attempt["properties"]["object_type"]["const"] == "job_attempt"
    assert "step_key" in job["required"]
    assert "step_idempotent" in job["required"]
    assert "attempt_number" in attempt["required"]
    assert _closed_values(attempt["properties"]["status"]) == {
        "active",
        "succeeded",
        "failed",
    }
    assert "maximum" not in json.dumps(job["properties"]["attempt_count"])
    assert "maximum" not in json.dumps(attempt["properties"]["attempt_number"])

    technical_spec = TECHNICAL_SPEC_PATH.read_text(encoding="utf-8")
    lifecycle = _lifecycle_markdown()
    for fragment in (
        "step_key",
        "step_idempotent",
        "available | leased | completed | failed | reconciliation_required",
        "attempt_count` is the durable count of `JobAttempt` rows",
        "TEXT_MODEL_MAX_RETRIES + 1",
        "one Job per (run_id, step_key)",
        "one JobAttempt per (job_id, attempt_number)",
        "error_code=job_lease_lost",
    ):
        assert fragment in technical_spec, f"missing technical-spec job contract: {fragment}"

    assert "no `available -> failed` transition" in lifecycle
    assert "error_code=job_lease_lost" in lifecycle
    assert "one `Job` row exists per `(run_id, step_key)`" in lifecycle
    assert "neither creates a `JobAttempt` nor increments `attempt_count`" in lifecycle


def test_pending_approval_expiry_uses_approval_expiry_days_default() -> None:
    lifecycle = _lifecycle_markdown()
    assert "submitted_at + APPROVAL_EXPIRY_DAYS" in lifecycle
    assert "decided_at + APPROVAL_EXPIRY_DAYS" in lifecycle
    assert "HS-010" in lifecycle
    assert "EP-06" in lifecycle

    technical_spec = TECHNICAL_SPEC_PATH.read_text(encoding="utf-8")
    assert "pending_approval -> expired` at `submitted_at + APPROVAL_EXPIRY_DAYS" in technical_spec

    runtime_schema = _load_json(CONTRACTS_DIR / "runtime-config.schema.json")
    assert "APPROVAL_EXPIRY_DAYS" in runtime_schema["properties"]


def test_disposition_decision_graph_is_contract_only() -> None:
    lifecycle = _lifecycle_markdown()
    section_start = lifecycle.index("#### 2.4.1 Disposition decision graph")
    section_end = lifecycle.index("### 2.5 ExportDraft", section_start)
    section = lifecycle[section_start:section_end]

    assert "implementation in EP-06" in section
    assert "`pending`" in section
    assert "`weakness_confirmed`" in section
    assert "Route handlers" in section

    domain = _load_json(CONTRACTS_DIR / "domain.schema.json")
    disposition_values = _closed_values(
        domain["$defs"]["Disposition"]["properties"]["decision"]
    )
    assert disposition_values == {
        "pending",
        "accepted",
        "edited",
        "rejected",
        "evidence_requested",
        "weakness_confirmed",
    }
