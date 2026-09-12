"""Focused tests for the SSP model policy gate."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ato_service.ssp_workspace.model_policy import (
    SspModelPolicyError,
    require_ssp_model_allowed,
)


def _config(
    *,
    text_calls: bool,
    approval: bool,
    vision_calls: bool = False,
    vision_enabled: bool = False,
    runtime_profile: str = "dev_local",
) -> SimpleNamespace:
    return SimpleNamespace(
        runtime_profile=runtime_profile,
        storage_data_path=Path("/tmp/ssp-policy-test"),
        document={
            "PROCESS_CAPABILITIES": {
                "text_model_calls": text_calls,
                "vision_model_calls": vision_calls,
            },
            "TEXT_MODEL_ENDPOINT_PROFILE": "mock",
            "VISION_MODEL_ENDPOINT_PROFILE": "mock",
            "TEXT_MODEL_ENDPOINT_POLICY_APPROVED": approval,
            "VISION_MODEL_ENABLED": vision_enabled,
        },
    )


@pytest.mark.parametrize(
    ("text_calls", "approval", "expected"),
    [
        (False, True, "prohibited_model_action"),
        (True, False, "model_policy_not_approved"),
        (False, False, "prohibited_model_action"),
    ],
)
def test_text_gate_rejects_disabled_or_unapproved_routes(
    text_calls: bool,
    approval: bool,
    expected: str,
) -> None:
    calls = 0

    async def model(_prompt: object) -> str:
        nonlocal calls
        calls += 1
        return "unexpected"

    with pytest.raises(SspModelPolicyError) as caught:
        require_ssp_model_allowed(
            _config(text_calls=text_calls, approval=approval)
        )

    assert caught.value.error_code == expected
    assert calls == 0


def test_vision_gate_requires_both_capability_and_enablement() -> None:
    with pytest.raises(SspModelPolicyError) as caught:
        require_ssp_model_allowed(
            _config(
                text_calls=True,
                approval=True,
                vision_calls=True,
                vision_enabled=False,
            ),
            vision=True,
        )

    assert caught.value.error_code == "prohibited_model_action"


def test_production_without_explicit_data_classification_fails_closed() -> None:
    with pytest.raises(SspModelPolicyError) as caught:
        require_ssp_model_allowed(
            _config(
                text_calls=True,
                approval=True,
                runtime_profile="onprem_production",
            )
        )

    assert caught.value.error_code == "model_routing_denied"


def test_unknown_runtime_boundary_fails_closed() -> None:
    with pytest.raises(SspModelPolicyError) as caught:
        require_ssp_model_allowed(
            _config(
                text_calls=True,
                approval=True,
                runtime_profile="unknown",
            )
        )

    assert caught.value.error_code == "model_routing_denied"
