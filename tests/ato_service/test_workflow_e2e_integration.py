"""Profile-parametrized PostgreSQL end-to-end workflow integration tests."""

from __future__ import annotations

import hashlib

import pytest

from tests.integration_support.factories import PROFILE_CASES
from tests.integration_support.postgres import postgres_integration_harness, run_async
from tests.integration_support.workflow import assert_tenant_isolation, run_profile_workflow


@pytest.mark.integration
@pytest.mark.parametrize(
    ("profile_id", "certification_class", "impact_level"),
    PROFILE_CASES,
)
def test_profile_workflow_reaches_exact_export_hash(
    tmp_path,
    profile_id: str,
    certification_class: str | None,
    impact_level: str | None,
) -> None:
    async def exercise() -> None:
        async with postgres_integration_harness(tmp_path) as harness:
            artifacts = await run_profile_workflow(
                harness,
                profile_id=profile_id,
                certification_class=certification_class,
                impact_level=impact_level,
            )
            assert len(artifacts.zip_sha256) == 64
            assert hashlib.sha256(artifacts.zip_bytes).hexdigest() == artifacts.zip_sha256
            await assert_tenant_isolation(harness, artifacts=artifacts)

    run_async(exercise())
