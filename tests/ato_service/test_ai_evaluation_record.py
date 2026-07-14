"""Tests for immutable AI qualification evaluation record contract."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from ato_service.ai_evaluation.persistence import (
    EvaluationRecordConflictError,
    EvaluationRecordPersistenceError,
    load_evaluation_record,
    write_evaluation_record,
)
from ato_service.ai_evaluation.record import validate_evaluation_record
from ato_service.ai_evaluation.runner import (
    EvaluationCaseInput,
    EvaluationRunRequest,
    build_explicit_gateway_request,
    run_bounded_evaluation_sync,
)
from ato_service.ai_evaluation.types import DigestVerificationTarget
from ato_service.model_gateway import ModelCapability
from ato_service.storage_reconciliation import StoragePathError

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "docs" / "contracts" / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_valid_failed_fixture_passes_schema_and_semantic_validation() -> None:
    document = _load("ai-evaluation-record.valid.failed-gates.json")
    report = validate_evaluation_record(document, project_root=ROOT)
    assert report.valid is True
    assert report.outcome == "failed"


def test_valid_invalid_fixture_passes_schema_and_semantic_validation() -> None:
    document = _load("ai-evaluation-record.valid.invalid-holdout.json")
    report = validate_evaluation_record(document, project_root=ROOT)
    assert report.valid is True
    assert report.outcome == "invalid"


def test_invalid_fixture_rejected_by_schema() -> None:
    document = _load("ai-evaluation-record.invalid.missing-required-fields.json")
    report = validate_evaluation_record(document, project_root=ROOT)
    assert report.valid is False
    assert report.schema_errors


def test_passed_outcome_rejected_while_hs006_unresolved() -> None:
    document = _load("ai-evaluation-record.valid.failed-gates.json")
    document = copy.deepcopy(document)
    document["outcome"] = "passed"
    document["blockers"] = []
    report = validate_evaluation_record(document, project_root=ROOT)
    assert report.valid is False
    assert any("outcome=passed" in item for item in report.semantic_errors)


def test_forbidden_secret_field_rejected() -> None:
    document = _load("ai-evaluation-record.valid.failed-gates.json")
    document = copy.deepcopy(document)
    document["blockers"][0]["message"] = "Bearer abcdefghijklmnopqrstuvwxyz1234567890"
    report = validate_evaluation_record(document, project_root=ROOT)
    assert report.valid is False
    assert any("secret material" in item for item in report.semantic_errors)


def test_digest_verification_detects_mismatch(tmp_path: Path) -> None:
    document = _load("ai-evaluation-record.valid.failed-gates.json")
    source = tmp_path / "guide.md"
    source.write_text("guide bytes", encoding="utf-8")
    targets = (
        DigestVerificationTarget(
            field_path="guide_sha256",
            expected_sha256="0" * 64,
            source_path=str(source),
        ),
    )
    report = validate_evaluation_record(document, digest_targets=targets, project_root=ROOT)
    assert report.valid is False
    assert report.digest_errors


def test_write_evaluation_record_is_write_once(tmp_path: Path) -> None:
    document = _load("ai-evaluation-record.valid.failed-gates.json")
    stored = write_evaluation_record(document, records_root=tmp_path, project_root=ROOT)
    assert stored.evaluation_id == document["evaluation_id"]
    assert Path(stored.storage_path).is_file()

    reloaded = load_evaluation_record(document["evaluation_id"], records_root=tmp_path)
    assert reloaded.sha256 == stored.sha256

    same = write_evaluation_record(document, records_root=tmp_path, project_root=ROOT)
    assert same.sha256 == stored.sha256

    mutated = copy.deepcopy(document)
    mutated["results"]["metric_values"]["supported_precision"] = 0.99
    with pytest.raises(EvaluationRecordConflictError):
        write_evaluation_record(mutated, records_root=tmp_path, project_root=ROOT)


def test_unsafe_records_root_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _load("ai-evaluation-record.valid.failed-gates.json")

    def _raise_unsafe(*_args, **_kwargs):
        raise StoragePathError("storage file path escapes root")

    monkeypatch.setattr(
        "ato_service.ai_evaluation.persistence.prepare_storage_file_path",
        _raise_unsafe,
    )
    with pytest.raises(EvaluationRecordPersistenceError):
        write_evaluation_record(document, records_root=tmp_path, project_root=ROOT)


def test_partial_write_leaves_no_final_record_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _load("ai-evaluation-record.valid.invalid-holdout.json")
    monkeypatch.setattr(os, "replace", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("replace failed")))
    with pytest.raises(EvaluationRecordPersistenceError):
        write_evaluation_record(document, records_root=tmp_path, project_root=ROOT)

    final_path = tmp_path / "evaluations" / f"{document['evaluation_id']}.json"
    assert not final_path.exists()


def test_runner_requires_explicit_blockers_and_rejects_passed() -> None:
    gateway_request = build_explicit_gateway_request(max_llm_calls=1)
    case = EvaluationCaseInput(
        case_id="matrix-001",
        prompt_sha256="1" * 64,
        fact_bundle_sha256="2" * 64,
        response_sha256="3" * 64,
    )
    request = EvaluationRunRequest(
        evaluation_id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        holdout_manifest_sha256="a" * 64,
        corpus_digest_sha256="b" * 64,
        model_step="sufficiency_matrix",
        profile_id="fedramp_20x_class_c",
        profile_version="1.0.0",
        cases=(case,),
        declared_outcome="failed",
        declared_blockers=("gate.supported_precision",),
        gateway_request=gateway_request,
    )

    def _callback_factory(_case: EvaluationCaseInput):
        async def _inner() -> str:
            return "{}"

        return _inner

    result = run_bounded_evaluation_sync(request, callback_factory=_callback_factory)
    assert result.outcome == "failed"
    assert result.llm_call_count == 1
    assert result.blockers == ("gate.supported_precision",)
    assert result.per_case_attempt_metadata[0]["case_id"] == "matrix-001"

    with pytest.raises(ValueError, match="declared_blockers"):
        run_bounded_evaluation_sync(
            EvaluationRunRequest(
                evaluation_id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                holdout_manifest_sha256="a" * 64,
                corpus_digest_sha256="b" * 64,
                model_step="sufficiency_matrix",
                profile_id="fedramp_20x_class_c",
                profile_version="1.0.0",
                cases=(case,),
                declared_outcome="failed",
                declared_blockers=(),
                gateway_request=gateway_request,
            ),
            callback_factory=_callback_factory,
        )


def test_runner_uses_gateway_policy_boundary() -> None:
    gateway_request = build_explicit_gateway_request(
        capability=ModelCapability.SUFFICIENCY_MATRIX,
        current_llm_call_count=1,
        max_llm_calls=1,
    )
    case = EvaluationCaseInput(
        case_id="matrix-002",
        prompt_sha256="4" * 64,
        fact_bundle_sha256="5" * 64,
        response_sha256="6" * 64,
    )
    request = EvaluationRunRequest(
        evaluation_id="ffffffff-ffff-4fff-8fff-ffffffffffff",
        holdout_manifest_sha256="a" * 64,
        corpus_digest_sha256="b" * 64,
        model_step="sufficiency_matrix",
        profile_id="fedramp_20x_class_c",
        profile_version="1.0.0",
        cases=(case,),
        declared_outcome="invalid",
        declared_blockers=("model_call_limit_exceeded",),
        gateway_request=gateway_request,
    )

    def _callback_factory(_case: EvaluationCaseInput):
        async def _inner() -> str:
            return "{}"

        return _inner

    result = run_bounded_evaluation_sync(request, callback_factory=_callback_factory)
    assert result.llm_call_count == 1
    assert "model_call_limit_exceeded" in result.failure_codes
