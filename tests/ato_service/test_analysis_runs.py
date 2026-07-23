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
from tests.ato_service.test_analysis_profile import fisma_runtime_config, write_digest_pinned_fisma_profile

ROOT = Path(__file__).resolve().parents[2]

REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
RUNTIME_AUTHORITY_MANIFEST_ID = "ato-authorities-2026-07-10-draft"


def _run(awaitable: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(awaitable)


def _principal() -> MagicMock:
    principal = MagicMock()
    principal.actor_id = "actor-1"
    principal.groups = ("owners",)
    return principal


def _dev_local_config(tmp_path: Path) -> object:
    profile_file, digest, _profile, _impact_level = write_digest_pinned_fisma_profile(tmp_path)
    return fisma_runtime_config(
        tmp_path,
        profile_path=profile_file,
        expected_sha256=digest,
    )


def _fisma_package_revision_mock(*, run_type_fields: bool = True) -> MagicMock:
    package_revision = MagicMock()
    package_revision.package_revision_id = REVISION_ID
    package_revision.profile_id = "fisma_agency_security"
    package_revision.certification_class = None
    package_revision.impact_level = "moderate"
    package_revision.data_origin = "synthetic"
    package_revision.status = "ready"
    package_revision.authority_manifest_id = RUNTIME_AUTHORITY_MANIFEST_ID
    package_revision.package_content_sha256 = "a" * 64
    if not run_type_fields:
        del package_revision.certification_class
    return package_revision


def test_start_run_allows_full_run_type_in_dev_local(tmp_path: Path) -> None:
    session = AsyncMock()
    package_revision = _fisma_package_revision_mock()
    system = MagicMock()
    system.owner_group = "owners"
    system.viewer_groups = ["viewers"]
    result = MagicMock()
    result.one_or_none.return_value = (package_revision, system)
    session.execute = AsyncMock(return_value=result)
    session.scalar = AsyncMock(return_value=0)
    session.add_all = MagicMock()

    with (
        patch(
            "ato_service.analysis_runs.load_idempotency_replay",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "ato_service.analysis_runs.append_audit_event",
            new=AsyncMock(),
        ),
        patch(
            "ato_service.analysis_runs.record_idempotency_outcome",
            new=AsyncMock(),
        ),
    ):
        response = _run(
            start_run(
                session,
                principal=_principal(),
                package_revision_id=REVISION_ID,
                request=StartRunInput(
                    run_type="full",
                    parent_run_id=None,
                    assessment_item_ids=(),
                ),
                config=_dev_local_config(tmp_path),
                authority_manifest_id=RUNTIME_AUTHORITY_MANIFEST_ID,
                project_root=ROOT,
                idempotency_key="idempotency-key-01234567",
                hmac_key=b"x" * MIN_AUDIT_HMAC_KEY_BYTES,
                now=NOW,
            )
        )
    assert response.status == 202
    assert response.payload["run_type"] == "full"


def test_start_run_rejects_full_run_type_outside_dev_local(tmp_path: Path) -> None:
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
    config = MagicMock()
    config.runtime_profile = "onprem_production"

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
                config=config,
                authority_manifest_id=RUNTIME_AUTHORITY_MANIFEST_ID,
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
                    config=_dev_local_config(tmp_path),
                    authority_manifest_id=RUNTIME_AUTHORITY_MANIFEST_ID,
                    project_root=ROOT,
                    idempotency_key="idempotency-key-01234567",
                    hmac_key=b"x" * MIN_AUDIT_HMAC_KEY_BYTES,
                    now=NOW,
                )
            )
    assert exc_info.value.error_code == "analysis_not_eligible"


def test_start_run_enforces_concurrent_run_limit(tmp_path: Path) -> None:
    session = AsyncMock()
    package_revision = _fisma_package_revision_mock()
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
                config=_dev_local_config(tmp_path),
                authority_manifest_id=RUNTIME_AUTHORITY_MANIFEST_ID,
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
                config=_dev_local_config(tmp_path),
                authority_manifest_id=RUNTIME_AUTHORITY_MANIFEST_ID,
                project_root=ROOT,
                idempotency_key="idempotency-key-01234567",
                hmac_key=b"x" * MIN_AUDIT_HMAC_KEY_BYTES,
                now=NOW,
            )
        )


def test_start_run_rejects_profile_authority_manifest_mismatch(tmp_path: Path) -> None:
    session = AsyncMock()
    package_revision = _fisma_package_revision_mock()
    system = MagicMock(owner_group="owners", viewer_groups=["viewers"])
    result = MagicMock()
    result.one_or_none.return_value = (package_revision, system)
    session.execute = AsyncMock(return_value=result)
    session.scalar = AsyncMock(return_value=0)

    with (
        patch(
            "ato_service.analysis_runs.load_idempotency_replay",
            new=AsyncMock(return_value=None),
        ),
        pytest.raises(AnalysisRunValidationError) as exc_info,
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
                config=_dev_local_config(tmp_path),
                authority_manifest_id="wrong.manifest",
                project_root=ROOT,
                idempotency_key="idempotency-key-01234567",
                hmac_key=b"x" * MIN_AUDIT_HMAC_KEY_BYTES,
                now=NOW,
            )
        )

    assert exc_info.value.error_code == "reconciliation_required"


def test_start_run_rejects_revision_authority_manifest_mismatch(tmp_path: Path) -> None:
    session = AsyncMock()
    package_revision = _fisma_package_revision_mock()
    package_revision.authority_manifest_id = "stale.revision.manifest"
    system = MagicMock(owner_group="owners", viewer_groups=["viewers"])
    result = MagicMock()
    result.one_or_none.return_value = (package_revision, system)
    session.execute = AsyncMock(return_value=result)
    session.scalar = AsyncMock(return_value=0)

    with (
        patch(
            "ato_service.analysis_runs.load_idempotency_replay",
            new=AsyncMock(return_value=None),
        ),
        pytest.raises(AnalysisRunValidationError) as exc_info,
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
                config=_dev_local_config(tmp_path),
                authority_manifest_id=RUNTIME_AUTHORITY_MANIFEST_ID,
                project_root=ROOT,
                idempotency_key="idempotency-key-01234567",
                hmac_key=b"x" * MIN_AUDIT_HMAC_KEY_BYTES,
                now=NOW,
            )
        )

    assert exc_info.value.error_code == "reconciliation_required"
