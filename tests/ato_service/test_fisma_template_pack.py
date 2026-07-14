"""Agency FISMA template pack loader and validator tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ato_service.fisma_template_pack import (
    FismaTemplatePackError,
    FismaTemplatePackReference,
    load_template_pack_reference,
    load_verified_template_pack,
)

ROOT = Path(__file__).resolve().parents[2]
PACK_ZIP = ROOT / "tests/fixtures/internal/internal-fisma-template-pack.zip"
PACK_DIGEST = "1350f8557eff5c44061a25599be762998b5a45629d9c2440ad7f6ebda4c1ec1c"


def test_load_template_pack_reference_returns_none_when_unconfigured() -> None:
    assert load_template_pack_reference({}) is None


def test_load_verified_template_pack_reads_internal_fixture() -> None:
    pack = load_verified_template_pack(
        FismaTemplatePackReference(path=PACK_ZIP, expected_sha256=PACK_DIGEST)
    )
    assert pack.pack_id == "internal-fixture-001"
    assert pack.approval_status == "approved"
    assert "templates/ssp-security-section.md" in pack.members


def test_load_verified_template_pack_rejects_digest_mismatch() -> None:
    with pytest.raises(FismaTemplatePackError, match="digest mismatch"):
        load_verified_template_pack(
            FismaTemplatePackReference(path=PACK_ZIP, expected_sha256="a" * 64)
        )


def test_contract_fixture_manifest_validates_against_schema() -> None:
    from jsonschema import Draft202012Validator

    schema = json.loads(
        (ROOT / "docs/contracts/fisma-template-pack.schema.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (
            ROOT / "docs/contracts/fixtures/fisma-template-pack.valid.internal-fixture.json"
        ).read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)
    assert not list(validator.iter_errors(manifest))
