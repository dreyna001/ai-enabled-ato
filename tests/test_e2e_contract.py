"""Portal E2E deployment contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = ROOT / "docs" / "contracts"

E2E_RUNTIME_CONFIG = ROOT / "deployment" / "config" / "runtime-config.dev_local.e2e.json"
E2E_STACK_START = ROOT / "scripts" / "e2e-stack-start.sh"
E2E_STACK_STOP = ROOT / "scripts" / "e2e-stack-stop.sh"
E2E_STACK_COMMON = ROOT / "scripts" / "e2e-stack-common.sh"
PLAYWRIGHT_CONFIG = ROOT / "portal" / "playwright.config.ts"
PORTAL_E2E_README = ROOT / "portal" / "e2e" / "README.md"
SYNTHETIC_PACKAGES = [
    ROOT / "data" / "synthetic-packages" / "fisma-demo-portal" / "agency-security-plan-excerpt.json",
    ROOT / "data" / "synthetic-packages" / "fedramp-rev5-demo-portal" / "demo-package.json",
    ROOT / "data" / "synthetic-packages" / "fedramp-20x-demo-portal" / "demo-package.json",
]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validator_for(schema_path: Path) -> Draft202012Validator:
    schema = _load_json(schema_path)
    try:
        return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    except SchemaError:
        return Draft202012Validator(schema)


@pytest.mark.parametrize(
    "path",
    [
        E2E_RUNTIME_CONFIG,
        E2E_STACK_START,
        E2E_STACK_STOP,
        E2E_STACK_COMMON,
        PLAYWRIGHT_CONFIG,
        PORTAL_E2E_README,
        *SYNTHETIC_PACKAGES,
    ],
)
def test_e2e_assets_exist(path: Path) -> None:
    assert path.is_file(), f"missing E2E asset: {path}"


def test_e2e_runtime_config_validates_against_schema() -> None:
    document = _load_json(E2E_RUNTIME_CONFIG)
    validator = _validator_for(CONTRACTS_DIR / "runtime-config.schema.json")
    validator.validate(document)
    assert document["runtime_profile"] == "dev_local"
    assert document["IDENTITY_PROVIDER_MODE"] == "oidc"
    assert document["PROCESS_CAPABILITIES"]["package_search"] is True
    assert document["PROCESS_CAPABILITIES"]["text_model_calls"] is False


def test_e2e_stack_scripts_declare_loopback_stack() -> None:
    start = E2E_STACK_START.read_text(encoding="utf-8")
    stop = E2E_STACK_STOP.read_text(encoding="utf-8")
    assert "e2e-stack-common.sh" in start
    assert "dev-oidc" in start or "synthetic_intake_worker" in start
    assert "deterministic_analyzer_worker" in start
    assert "e2e-stack-stop" in stop or "E2E_STOP_FILE" in stop


def test_playwright_config_wires_managed_stack() -> None:
    text = PLAYWRIGHT_CONFIG.read_text(encoding="utf-8")
    assert "e2e-stack-start.sh" in text
    assert "ATO_E2E_MANAGED_STACK" in text
    assert "webServer" in text


def test_portal_package_declares_e2e_scripts() -> None:
    package = _load_json(ROOT / "portal" / "package.json")
    scripts = package["scripts"]
    assert "test:e2e:managed" in scripts
    assert "stack:e2e:start" in scripts
    assert "stack:e2e:stop" in scripts
