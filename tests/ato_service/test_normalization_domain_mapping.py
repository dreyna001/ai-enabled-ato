"""Focused tests for PackageNormalizationStep domain mapping."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from ato_service.domain_mapping import map_package_normalization_step_to_domain

ROOT = Path(__file__).resolve().parents[2]
RESERVED_FIXTURE_PATH = (
    ROOT
    / "docs"
    / "contracts"
    / "fixtures"
    / "domain.valid.package-normalization-step-reserved.json"
)


class _ReservedStep:
    step_id = UUID("a1b2c3d4-e5f6-4789-a012-3456789abcde")
    package_revision_id = UUID("f47ac10b-58cc-4372-a567-0e02b2c3d479")
    step_key = "normalize_proposal"
    status = "reserved"
    input_digest = "1" * 64
    fact_bundle_sha256 = None
    schema_id = "https://ato.local/schemas/normalize-proposal-response.schema.json"
    prompt_version = "1.0.0"
    prompt_sha256 = None
    prompt_storage_key = None
    fact_bundle_storage_key = None
    response_storage_key = None
    endpoint_profile = None
    endpoint_host = None
    model_requested = None
    model_reported = None
    temperature = None
    input_limit = None
    output_limit = None
    timeout_seconds = None
    attempt = None
    provider_request_id = None
    input_tokens = None
    output_tokens = None
    latency_ms = None
    response_sha256 = None
    validation_outcome = None
    llm_call_count = 0
    repair_attempted = False
    error_code = None
    error_retryable = None
    created_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    started_at = None
    completed_at = None


def test_map_reserved_step_matches_domain_fixture() -> None:
    expected = json.loads(RESERVED_FIXTURE_PATH.read_text(encoding="utf-8"))
    expected["input_digest"] = "1" * 64

    mapped = map_package_normalization_step_to_domain(_ReservedStep())

    assert mapped == expected


def test_map_completed_step_converts_numeric_fields() -> None:
    step = _ReservedStep()
    step.status = "completed"
    step.fact_bundle_sha256 = "3" * 64
    step.prompt_sha256 = "4" * 64
    step.prompt_storage_key = (
        "revisions/f47ac10b-58cc-4372-a567-0e02b2c3d479/normalization/"
        "a1b2c3d4-e5f6-4789-a012-3456789abcde/prompt.json"
    )
    step.fact_bundle_storage_key = (
        "revisions/f47ac10b-58cc-4372-a567-0e02b2c3d479/normalization/"
        "a1b2c3d4-e5f6-4789-a012-3456789abcde/fact-bundle.json"
    )
    step.response_storage_key = (
        "revisions/f47ac10b-58cc-4372-a567-0e02b2c3d479/normalization/"
        "a1b2c3d4-e5f6-4789-a012-3456789abcde/response.json"
    )
    step.endpoint_profile = "internal_openai_compatible"
    step.endpoint_host = "llm.internal.example"
    step.model_requested = "gpt-test"
    step.model_reported = "gpt-test"
    step.temperature = Decimal("0.0000")
    step.input_limit = 8192
    step.output_limit = 2048
    step.timeout_seconds = Decimal("30.000")
    step.attempt = 1
    step.provider_request_id = "req-123"
    step.input_tokens = 1200
    step.output_tokens = 300
    step.latency_ms = 450
    step.response_sha256 = "5" * 64
    step.validation_outcome = "accepted"
    step.llm_call_count = 1
    step.started_at = datetime(2026, 7, 14, 12, 0, 1, tzinfo=timezone.utc)
    step.completed_at = datetime(2026, 7, 14, 12, 0, 5, tzinfo=timezone.utc)

    mapped = map_package_normalization_step_to_domain(step)

    assert mapped["temperature"] == 0.0
    assert mapped["timeout_seconds"] == 30.0
    assert mapped["llm_call_count"] == 1
