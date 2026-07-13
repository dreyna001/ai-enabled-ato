"""Contract alignment tests for normalization persistence artifacts."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from jsonschema import Draft202012Validator

from ato_service.normalize_proposal.fact_bundle import build_fact_bundle
from ato_service.normalize_proposal.json_utils import stable_json_dumps
from ato_service.normalize_proposal.types import ArtifactFacts, SegmentFact

ROOT = Path(__file__).resolve().parents[2]
RESPONSE_SCHEMA_PATH = ROOT / "docs" / "contracts" / "normalize-proposal-response.schema.json"
FACT_BUNDLE_SCHEMA_PATH = ROOT / "docs" / "contracts" / "normalize-proposal-fact-bundle.schema.json"
MISSING_TARGET_FIXTURE = (
    ROOT
    / "docs"
    / "contracts"
    / "fixtures"
    / "normalize-proposal-response.invalid.missing-target-pointer.json"
)
VALID_FACT_BUNDLE_FIXTURE = (
    ROOT
    / "docs"
    / "contracts"
    / "fixtures"
    / "normalize-proposal-fact-bundle.valid.minimal.json"
)

ARTIFACT_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")


def _validator(schema_path: Path) -> Draft202012Validator:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    validator.check_schema(schema)
    return validator


def _text_locator() -> dict[str, object]:
    return {
        "kind": "text_offsets",
        "start_byte": 0,
        "end_byte": 120,
        "normalized_start": 0,
        "normalized_end": 120,
    }


def test_response_schema_requires_target_pointer() -> None:
    payload = json.loads(MISSING_TARGET_FIXTURE.read_text(encoding="utf-8"))
    errors = sorted(_validator(RESPONSE_SCHEMA_PATH).iter_errors(payload))
    assert errors
    assert any(error.validator == "required" for error in errors)


def test_core_prompt_payload_matches_fact_bundle_schema() -> None:
    bundle = build_fact_bundle(
        profile_id="fisma_agency_security",
        empty_targets=("/system/mission_summary",),
        artifacts=(
            ArtifactFacts(
                artifact_id=ARTIFACT_ID,
                sha256="a" * 64,
                filename="narrative.txt",
                detected_format="text",
                segments=(
                    SegmentFact(
                        segment_index=1,
                        text="Mission: Internal web application for agency case management.",
                        locator=_text_locator(),
                        extraction_method="text",
                    ),
                ),
            ),
        ),
        context_tokens=8192,
        max_output_tokens=512,
    )
    errors = sorted(_validator(FACT_BUNDLE_SCHEMA_PATH).iter_errors(bundle.prompt_payload))
    assert errors == []


def test_fact_bundle_fixture_matches_core_shape() -> None:
    fixture = json.loads(VALID_FACT_BUNDLE_FIXTURE.read_text(encoding="utf-8"))
    errors = sorted(_validator(FACT_BUNDLE_SCHEMA_PATH).iter_errors(fixture))
    assert errors == []
    assert stable_json_dumps(fixture)


def test_fact_bundle_fixture_rejects_model_authored_locator_fields() -> None:
    invalid_path = (
        ROOT
        / "docs"
        / "contracts"
        / "fixtures"
        / "normalize-proposal-fact-bundle.invalid.model-authored-locator.json"
    )
    payload = json.loads(invalid_path.read_text(encoding="utf-8"))
    errors = sorted(_validator(FACT_BUNDLE_SCHEMA_PATH).iter_errors(payload))
    assert errors
