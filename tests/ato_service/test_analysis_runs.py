"""Unit tests for analysis run API service gates and idempotency."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ato_service.analysis_runs import (
    AnalysisRunPolicyError,
    AnalysisRunValidationError,
    ConcurrentRunLimitExceededError,
    StartRunInput,
    start_run,
)
from ato_service.audit import MIN_AUDIT_HMAC_KEY_BYTES
from ato_service.package_revisions import PackageRevisionNotFoundError
from ato_service.runtime_config import load_runtime_config_from_dict

ROOT = Path(__file__).resolve().parents[2]

REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


def _run(awaitable: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(awaitable)


def _principal() -> MagicMock:
    principal = MagicMock()
    principal.actor_id = "actor-1"
    principal.groups = ("owners",)
    return principal


def _runtime_config(tmp_path: Path) -> object:
    return load_runtime_config_from_dict(
        {
            "schema_version": "1.0.0",
            "runtime_profile": "dev_local",
            "STORAGE_DATA_PATH": str(tmp_path / "storage"),
        },
        base_dir=tmp_path,
    )


def test_start_run_rejects_full_run_type(tmp_path: Path) -> None:
    session = AsyncMock()
    package_revision = MagicMock()
    package_revision.package_revision_id = REVISION_ID
    package_revision.data_origin = "synthetic"
    package_revision.status = "ready"
    system = MagicMock()
    system.owner_group = "owners"
    system.viewer_groups = ["viewers"]
    result = MagicMock()
    result.one_or_none.return_value = (package_revision, system)
    session.execute = AsyncMock(return_value=result)

    with (
        patch(
            "ato_service.analysis_runs.load_idempotency_replay",
            new=AsyncMock(return_value=None),
        ),
        pytest.raises(AnalysisRunPolicyError) as exc_info,
    ):
        _run(
            start_run(
                session,
                principal=_principal(),
                package_revision_id=REVISION_ID,
                request=StartRunInput(
                    run_type="full",
                    parent_run_id=None,
                    assessment_item_ids=(),
                ),
                config=_runtime_config(tmp_path),
                authority_manifest_id="authority.v2",
                project_root=ROOT,
                idempotency_key="idempotency-key-01234567",
                hmac_key=b"x" * MIN_AUDIT_HMAC_KEY_BYTES,
                now=NOW,
            )
        )

    assert exc_info.value.error_code == "prohibited_model_action"


def test_start_run_requires_ready_revision(tmp_path: Path) -> None:
    session = AsyncMock()
    package_revision = MagicMock()
    package_revision.status = "awaiting_confirmation"
    package_revision.data_origin = "synthetic"
    system = MagicMock()
    system.owner_group = "owners"
    system.viewer_groups = ["viewers"]
    result = MagicMock()
    result.one_or_none.return_value = (package_revision, system)
    session.execute = AsyncMock(return_value=result)

    with patch(
        "ato_service.analysis_runs.load_idempotency_replay",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(AnalysisRunPolicyError) as exc_info:
            _run(
                start_run(
                    session,
                    principal=_principal(),
                    package_revision_id=REVISION_ID,
                    request=StartRunInput(
                        run_type="deterministic_only",
                        parent_run_id=None,
                        assessment_item_ids=(),
                    ),
                    config=_runtime_config(tmp_path),
                    authority_manifest_id="authority.v2",
                    project_root=ROOT,
                    idempotency_key="idempotency-key-01234567",
                    hmac_key=b"x" * MIN_AUDIT_HMAC_KEY_BYTES,
                    now=NOW,
                )
            )
    assert exc_info.value.error_code == "analysis_not_eligible"


def test_start_run_enforces_concurrent_run_limit(tmp_path: Path) -> None:
    session = AsyncMock()
    package_revision = MagicMock(status="ready", data_origin="synthetic")
    system = MagicMock(owner_group="owners", viewer_groups=["viewers"])
    result = MagicMock()
    result.one_or_none.return_value = (package_revision, system)
    session.execute = AsyncMock(return_value=result)
    session.scalar = AsyncMock(return_value=2)

    with (
        patch(
            "ato_service.analysis_runs.load_idempotency_replay",
            new=AsyncMock(return_value=None),
        ),
        pytest.raises(ConcurrentRunLimitExceededError),
    ):
        _run(
            start_run(
                session,
                principal=_principal(),
                package_revision_id=REVISION_ID,
                request=StartRunInput(
                    run_type="deterministic_only",
                    parent_run_id=None,
                    assessment_item_ids=(),
                ),
                config=_runtime_config(tmp_path),
                authority_manifest_id="authority.v2",
                project_root=ROOT,
                idempotency_key="idempotency-key-01234567",
                hmac_key=b"x" * MIN_AUDIT_HMAC_KEY_BYTES,
                now=NOW,
            )
        )


def test_start_run_missing_revision_raises_not_found(tmp_path: Path) -> None:
    session = AsyncMock()
    result = MagicMock()
    result.one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)

    with (
        patch(
            "ato_service.analysis_runs.load_idempotency_replay",
            new=AsyncMock(return_value=None),
        ),
        pytest.raises(PackageRevisionNotFoundError),
    ):
        _run(
            start_run(
                session,
                principal=_principal(),
                package_revision_id=REVISION_ID,
                request=StartRunInput(
                    run_type="deterministic_only",
                    parent_run_id=None,
                    assessment_item_ids=(),
                ),
                config=_runtime_config(tmp_path),
                authority_manifest_id="authority.v2",
                project_root=ROOT,
                idempotency_key="idempotency-key-01234567",
                hmac_key=b"x" * MIN_AUDIT_HMAC_KEY_BYTES,
                now=NOW,
            )
        )
