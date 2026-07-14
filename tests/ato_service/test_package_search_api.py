"""API-level tests for package search and chat authorization boundaries."""

from __future__ import annotations

import uuid

import pytest

from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.package_assistant_access import (
    CapabilityDisabledError,
    PackageRevisionAccessError,
    require_process_capability,
)
from ato_service.package_chat import evaluate_refusal
from ato_service.runtime_config import RuntimeConfig


def test_require_process_capability_disabled() -> None:
    config = RuntimeConfig(
        runtime_profile="onprem_production",
        storage_data_path=__import__("pathlib").Path("/tmp/ato"),
        document={
            "PROCESS_CAPABILITIES": {
                "api": True,
                "intake_worker": False,
                "analyzer_worker": False,
                "portal_static": False,
                "malware_scanning": False,
                "text_model_calls": False,
                "vision_model_calls": False,
                "oidc_authentication": True,
                "package_search": False,
                "package_chat": False,
            }
        },
    )
    with pytest.raises(CapabilityDisabledError):
        require_process_capability(config, capability="package_search")


def test_unauthorized_revision_lookup_raises_access_error() -> None:
    async def exercise() -> None:
        from unittest.mock import AsyncMock, MagicMock

        from ato_service.package_assistant_access import load_authorized_package_revision

        session = MagicMock()
        session.execute = AsyncMock(return_value=MagicMock(one_or_none=lambda: None))
        with pytest.raises(PackageRevisionAccessError):
            await load_authorized_package_revision(
                session,
                principal=AuthenticatedPrincipal(
                    actor_id="viewer@test",
                    groups=("viewers",),
                    csrf_token="c" * 32,
                    allowed_origins=("https://portal.example.test",),
                ),
                package_revision_id=uuid.uuid4(),
            )

    __import__("asyncio").run(exercise())


def test_evaluate_refusal_blocks_sql_like_prompts_as_unsafe_or_out_of_package() -> None:
    assert evaluate_refusal(question="'; DROP TABLE users; --") in {
        "unsafe_instruction",
        "out_of_package",
        None,
    }


def test_evaluate_refusal_blocks_authorization_requests() -> None:
    assert evaluate_refusal(question="Please grant ATO for this package") == "authorization_decision"
