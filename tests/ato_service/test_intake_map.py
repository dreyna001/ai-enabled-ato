"""Focused tests for bounded, upload-first intake MAP behavior."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import pytest

from ato_service.extraction.types import ExtractedSegment, ExtractionOutcome
from ato_service.intake import ArtifactSnapshot, IntakeRevisionSnapshot
from ato_service.intake_map import (
    MapResponseValidationError,
    _invoke_map_model,
    build_map_fact_bundle,
    build_map_model_call_request,
    compute_map_input_digest,
    intake_map_step_key,
    validate_and_parse_map_response,
)
from ato_service.model_gateway import (
    ModelCallRequest,
    PreAttestationModelCallRequest,
)
from ato_service.normalize_proposal.types import ArtifactFacts, SegmentFact
from ato_service.runtime_config import load_runtime_config_from_dict
from ato_service.text_llm import ChatMessage

REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
SYSTEM_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
ARTIFACT_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
SHA256 = "a" * 64


def _run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _config(tmp_path: Path, **overrides: Any):
    document = {
        "schema_version": "1.0.0",
        "runtime_profile": "dev_local",
        "STORAGE_DATA_PATH": "/data",
        **overrides,
    }
    return load_runtime_config_from_dict(document, base_dir=tmp_path)


def _external_config(tmp_path: Path):
    return _config(
        tmp_path,
        TEXT_MODEL_PROVIDER="openai_compatible",
        TEXT_MODEL_ENDPOINT_URL="https://api.openai.com/v1",
        TEXT_MODEL_NAME="test-model",
        TEXT_MODEL_CONTEXT_TOKENS=8192,
        TEXT_MODEL_MAX_OUTPUT_TOKENS=1024,
        TEXT_MODEL_TIMEOUT_SECONDS=30,
        TEXT_MODEL_MAX_RETRIES=0,
        TEXT_MODEL_ENDPOINT_PROFILE="external_openai",
        TEXT_MODEL_TEMPERATURE=0.0,
        TEXT_MODEL_ENDPOINT_POLICY_APPROVED=True,
        CUI_MODEL_BOUNDARY_APPROVED=False,
    )


def _locator(index: int) -> dict[str, Any]:
    return {
        "kind": "text_offsets",
        "start_byte": (index - 1) * 100,
        "end_byte": index * 100,
        "normalized_start": (index - 1) * 100,
        "normalized_end": index * 100,
    }


def _segment(index: int, text: str) -> SegmentFact:
    return SegmentFact(
        segment_index=index,
        text=text,
        locator=_locator(index),
        extraction_method="text",
    )


def _artifact_facts(*segments: SegmentFact) -> ArtifactFacts:
    return ArtifactFacts(
        artifact_id=ARTIFACT_ID,
        sha256=SHA256,
        filename="security-plan.txt",
        detected_format="text",
        segments=tuple(segments),
    )


def _artifact_snapshot() -> ArtifactSnapshot:
    return ArtifactSnapshot(
        artifact_id=ARTIFACT_ID,
        package_revision_id=REVISION_ID,
        display_filename="security-plan.txt",
        storage_key="ab/" + SHA256,
        sha256=SHA256,
        size_bytes=120,
        declared_media_type="text/plain",
        detected_media_type="text/plain",
        artifact_kind="evidence_document",
        malware_scan_status="clean",
        extraction_status="pending",
    )


def _snapshot(
    *,
    data_origin: str | None = None,
    sensitivity: str | None = None,
) -> IntakeRevisionSnapshot:
    return IntakeRevisionSnapshot(
        package_revision_id=REVISION_ID,
        revision_version=3,
        status="extracting",
        profile_id=None,
        impact_level=None,
        content_manifest_sha256="c" * 64,
        data_origin=data_origin,
        sensitivity=sensitivity,
        system_id=SYSTEM_ID,
        system_display_name="Upload First Test",
        artifacts=(_artifact_snapshot(),),
    )


def _extraction_outcome(text: str) -> ExtractionOutcome:
    return ExtractionOutcome(
        status="succeeded",
        detected_format="text",
        detected_media_type="text/plain",
        page_count=None,
        total_text_characters=len(text),
        vision_status="not_needed",
        segments=(
            ExtractedSegment(
                segment_index=1,
                text=text,
                locator=_locator(1),
                extraction_method="text",
            ),
        ),
    )


def _valid_response() -> str:
    return json.dumps(
        {
            "schema_version": "1.0.0",
            "facts": [
                {
                    "fact_key": "package.title",
                    "value": "Upload First System",
                    "value_kind": "direct_evidence",
                    "source_artifact_id": str(ARTIFACT_ID),
                    "segment_index": 1,
                    "chunk_ids": [f"{ARTIFACT_ID}:1"],
                    "confidence": "high",
                }
            ],
            "suggestions": {
                "profile_id": "fisma_agency_security",
                "impact_level": "moderate",
                "certification_class": None,
            },
        },
        sort_keys=True,
    )


@dataclass
class FakeTextClient:
    response: str
    calls: list[tuple[str | None, str]] = field(default_factory=list)
    provider: str = "fake"

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        system: str | None = None,
    ) -> str:
        self.calls.append((system, messages[-1].content))
        return self.response


def test_context_packing_records_omitted_chunk_ids(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        TEXT_MODEL_CONTEXT_TOKENS=4096,
        TEXT_MODEL_MAX_OUTPUT_TOKENS=512,
    )
    artifact = _artifact_facts(
        *(_segment(index, f"segment-{index}-" + "x" * 1200) for index in range(1, 9))
    )

    bundle = build_map_fact_bundle(artifact=artifact, config=config)

    assert bundle.included_segments
    assert bundle.omitted_chunk_ids
    assert bundle.context_complete is False
    assert bundle.omitted_chunk_ids == tuple(sorted(bundle.omitted_chunk_ids))


@pytest.mark.parametrize(
    "fact_key",
    ["data_origin", "sensitivity.level", "assessor.conclusion", "findings.item"],
)
def test_prohibited_fact_prefixes_fail_closed(fact_key: str) -> None:
    payload = json.loads(_valid_response())
    payload["facts"][0]["fact_key"] = fact_key

    with pytest.raises(MapResponseValidationError) as exc_info:
        validate_and_parse_map_response(
            raw_text=json.dumps(payload),
            artifact_id=ARTIFACT_ID,
            artifact_sha256=SHA256,
            included_segments=(_segment(1, "Upload First System"),),
        )

    assert exc_info.value.failure_kind == "prohibited_prefix"
    assert exc_info.value.repairable is False


def test_citation_binding_rejects_unpacked_chunk() -> None:
    payload = json.loads(_valid_response())
    payload["facts"][0]["chunk_ids"] = [f"{ARTIFACT_ID}:2"]

    with pytest.raises(MapResponseValidationError) as exc_info:
        validate_and_parse_map_response(
            raw_text=json.dumps(payload),
            artifact_id=ARTIFACT_ID,
            artifact_sha256=SHA256,
            included_segments=(_segment(1, "Upload First System"),),
        )

    assert exc_info.value.failure_kind == "source_binding"


def test_step_key_and_replay_digest_are_deterministic(tmp_path: Path) -> None:
    artifact = _artifact_facts(_segment(1, "Upload First System"))
    bundle = build_map_fact_bundle(artifact=artifact, config=_config(tmp_path))
    digest_args = {
        "package_revision_id": REVISION_ID,
        "revision_version": 3,
        "content_manifest_sha256": "c" * 64,
        "artifact": artifact,
        "fact_bundle_sha256": bundle.fact_bundle_sha256,
    }

    assert intake_map_step_key(ARTIFACT_ID) == f"imap_{ARTIFACT_ID.hex}"
    assert intake_map_step_key(ARTIFACT_ID) == intake_map_step_key(ARTIFACT_ID)
    assert compute_map_input_digest(**digest_args) == compute_map_input_digest(
        **digest_args
    )


def test_pre_attestation_dev_mock_calls_without_human_labels(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(data_origin=None, sensitivity=None)
    request = build_map_model_call_request(snapshot=snapshot, config=_config(tmp_path))
    assert isinstance(request, PreAttestationModelCallRequest)
    assert not hasattr(request, "data_origin")
    assert not hasattr(request, "sensitivity")

    artifact = _artifact_facts(_segment(1, "Upload First System"))
    bundle = build_map_fact_bundle(artifact=artifact, config=_config(tmp_path))
    client = FakeTextClient(_valid_response())

    raw, metadata, call_count = _run(
        _invoke_map_model(
            request=request,
            bundle=bundle,
            max_output_tokens=1024,
            text_client=client,
        )
    )
    parsed = validate_and_parse_map_response(
        raw_text=raw,
        artifact_id=ARTIFACT_ID,
        artifact_sha256=SHA256,
        included_segments=bundle.included_segments,
    )

    assert call_count == 1
    assert metadata.attempt == 1
    assert len(client.calls) == 1
    system_prompt, fact_prompt = client.calls[0]
    serialized_response = json.dumps(parsed, default=str)
    for forbidden in ("data_origin", "sensitivity"):
        assert forbidden not in (system_prompt or "")
        assert forbidden not in fact_prompt
        assert forbidden not in serialized_response
        assert forbidden not in stable_bundle_json(bundle)


def stable_bundle_json(bundle: Any) -> str:
    return json.dumps(bundle.prompt_payload, sort_keys=True)


def test_pre_attestation_real_endpoint_is_policy_blocked_before_call(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(data_origin=None, sensitivity=None)
    client = FakeTextClient(_valid_response())

    request = build_map_model_call_request(
        snapshot=snapshot,
        config=_external_config(tmp_path),
    )

    assert request is None
    assert client.calls == []


def test_existing_human_labels_use_normal_routing(tmp_path: Path) -> None:
    request = build_map_model_call_request(
        snapshot=_snapshot(
            data_origin="synthetic",
            sensitivity="internal_unclassified",
        ),
        config=_config(tmp_path),
    )

    assert isinstance(request, ModelCallRequest)

