"""Integration-style tests for runtime limit propagation into storage and model helpers."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from ato_service.blobs import BlobStore, BlobTooLargeError
from ato_service.content_manifests import (
    ContentManifestValidationError,
    ManifestSourceEntry,
    write_content_manifest,
)
from ato_service.model_gateway import (
    ModelCallLimitExceededError,
    ModelCallRequest,
    ModelCapability,
    invoke_model_call,
)
from ato_service.model_routing import DataOrigin, EndpointProfile, Sensitivity
from ato_service.runtime_config import load_runtime_config_from_dict

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "docs" / "contracts" / "content-manifest.schema.json"

PACKAGE_REVISION_ID = "11111111-1111-4111-8111-111111111111"
ARTIFACT_ID_A = "22222222-2222-4222-8222-222222222222"
ARTIFACT_ID_B = "33333333-3333-4333-8333-333333333333"


def _run(awaitable):
    return asyncio.run(awaitable)


def _custom_dev_config(tmp_path: Path):
    return load_runtime_config_from_dict(
        {
            "schema_version": "1.0.0",
            "runtime_profile": "dev_local",
            "MAX_MODEL_CALLS_PER_RUN": 2,
            "MAX_PACKAGE_BYTES": 50,
            "MAX_SINGLE_FILE_BYTES": 10,
            "MAX_FILES_PER_REVISION": 1,
        },
        base_dir=tmp_path,
    )


def test_loaded_runtime_limits_enforce_blob_single_file_boundary(
    tmp_path: Path,
) -> None:
    config = _custom_dev_config(tmp_path)
    store = BlobStore(config.storage_data_path)
    limits = config.limits

    stored = store.store_stream(
        io.BytesIO(b"x" * limits.max_single_file_bytes),
        max_bytes=limits.max_single_file_bytes,
    )
    assert stored.size_bytes == limits.max_single_file_bytes

    with pytest.raises(BlobTooLargeError, match="maximum"):
        store.store_stream(
            io.BytesIO(b"x" * (limits.max_single_file_bytes + 1)),
            max_bytes=limits.max_single_file_bytes,
        )


def test_loaded_runtime_limits_enforce_content_manifest_boundaries(
    tmp_path: Path,
) -> None:
    config = _custom_dev_config(tmp_path)
    store = BlobStore(config.storage_data_path)
    limits = config.limits

    blob = store.store_stream(
        io.BytesIO(b"x" * limits.max_single_file_bytes),
        max_bytes=limits.max_single_file_bytes,
    )
    entry = ManifestSourceEntry(
        artifact_id=ARTIFACT_ID_A,
        storage_key=blob.storage_key,
        sha256=blob.sha256,
        size_bytes=blob.size_bytes,
    )

    write_content_manifest(
        PACKAGE_REVISION_ID,
        [entry],
        storage_root=config.storage_data_path,
        schema_path=SCHEMA_PATH,
        max_artifacts=limits.max_files_per_revision,
        max_artifact_bytes=limits.max_single_file_bytes,
        max_package_bytes=limits.max_package_bytes,
    )

    second_blob = store.store_stream(
        io.BytesIO(b"y"),
        max_bytes=limits.max_single_file_bytes,
    )
    second_entry = ManifestSourceEntry(
        artifact_id=ARTIFACT_ID_B,
        storage_key=second_blob.storage_key,
        sha256=second_blob.sha256,
        size_bytes=second_blob.size_bytes,
    )

    with pytest.raises(ContentManifestValidationError, match="must not exceed 1 artifacts"):
        write_content_manifest(
            PACKAGE_REVISION_ID,
            [entry, second_entry],
            storage_root=config.storage_data_path,
            schema_path=SCHEMA_PATH,
            max_artifacts=limits.max_files_per_revision,
            max_artifact_bytes=limits.max_single_file_bytes,
            max_package_bytes=limits.max_package_bytes,
        )


def test_loaded_runtime_limits_enforce_model_call_budget_without_callback(
    tmp_path: Path,
) -> None:
    config = _custom_dev_config(tmp_path)
    limits = config.limits
    callback = AsyncMock(return_value="must-not-run")

    request = ModelCallRequest(
        capability=ModelCapability.NORMALIZE_PROPOSAL,
        data_origin=DataOrigin.SYNTHETIC,
        sensitivity=Sensitivity.PUBLIC,
        endpoint_profile=EndpointProfile.MOCK,
        endpoint_policy_approved=False,
        cui_boundary_approved=False,
        vision_model_enabled=config.vision_model_enabled,
        current_llm_call_count=limits.max_model_calls_per_run,
        max_llm_calls=limits.max_model_calls_per_run,
    )

    with pytest.raises(ModelCallLimitExceededError) as exc_info:
        _run(invoke_model_call(request, callback))

    exc = exc_info.value
    assert exc.error_code == "model_call_limit_exceeded"
    assert exc.llm_call_count == limits.max_model_calls_per_run
    callback.assert_not_awaited()


def test_loaded_runtime_limits_allow_calls_up_to_configured_budget(
    tmp_path: Path,
) -> None:
    config = _custom_dev_config(tmp_path)
    limits = config.limits
    current_count = 0

    for _ in range(limits.max_model_calls_per_run):
        callback = AsyncMock(return_value="ok")
        request = ModelCallRequest(
            capability=ModelCapability.NORMALIZE_PROPOSAL,
            data_origin=DataOrigin.SYNTHETIC,
            sensitivity=Sensitivity.PUBLIC,
            endpoint_profile=EndpointProfile.MOCK,
            endpoint_policy_approved=False,
            cui_boundary_approved=False,
            vision_model_enabled=config.vision_model_enabled,
            current_llm_call_count=current_count,
            max_llm_calls=limits.max_model_calls_per_run,
        )
        result = _run(invoke_model_call(request, callback))
        callback.assert_awaited_once()
        current_count = result.llm_call_count

    assert current_count == limits.max_model_calls_per_run
