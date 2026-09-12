"""Focused tests for safe SSP model-invocation error mapping."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from ato_service.ssp_workspace.agency_docx import (
    AgencyDocxError,
    _invoke_with_one_repair as invoke_agency_with_one_repair,
)
from ato_service.ssp_workspace.diagram_analysis import (
    DiagramAnalysisError,
    analyze_architecture_diagram,
)
from ato_service.ssp_workspace.generation import (
    ModelPrompt,
    SspGenerationError,
    _invoke_with_one_repair as invoke_generation_with_one_repair,
)
from ato_service.ssp_workspace.model_runtime import SspContextBudgetError


def _budget_failure() -> SspContextBudgetError:
    return SspContextBudgetError("configured SSP context budget was exceeded")


def test_generation_preserves_context_budget_failure_as_nonrepairable() -> None:
    def model(_prompt: ModelPrompt) -> str:
        raise _budget_failure()

    with pytest.raises(SspGenerationError) as caught:
        asyncio.run(
            invoke_generation_with_one_repair(
                model=model,
                prompt=ModelPrompt("system", "user"),
                parser=lambda raw: raw,
            )
        )

    assert caught.value.detail == "configured SSP context budget was exceeded"
    assert caught.value.failure_kind == "context_budget"
    assert caught.value.attempts == 1
    assert caught.value.repair_attempted is False


def test_agency_docx_preserves_context_budget_failure_as_nonrepairable() -> None:
    def model(_prompt: ModelPrompt) -> str:
        raise _budget_failure()

    with pytest.raises(AgencyDocxError) as caught:
        asyncio.run(
            invoke_agency_with_one_repair(
                model=model,
                prompt=ModelPrompt("system", "user"),
                parser=lambda raw: raw,
            )
        )

    assert str(caught.value) == "configured SSP context budget was exceeded"
    assert caught.value.failure_kind == "context_budget"
    assert caught.value.repairable is False


def test_diagram_preserves_context_budget_failure_as_nonrepairable() -> None:
    def model(_prompt: object) -> str:
        raise _budget_failure()

    with pytest.raises(DiagramAnalysisError) as caught:
        asyncio.run(
            analyze_architecture_diagram(
                artifact_id=uuid.uuid4(),
                image_bytes=b"synthetic image",
                media_type="image/png",
                text_context=(),
                model=model,
            )
        )

    assert str(caught.value) == "configured SSP context budget was exceeded"
    assert caught.value.failure_kind == "context_budget"
    assert caught.value.repairable is False
