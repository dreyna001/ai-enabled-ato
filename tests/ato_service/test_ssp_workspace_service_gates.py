from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from ato_service.ssp_workspace.contracts import (
    FactContent,
    Provenance,
    QuestionContent,
    QuestionState,
    RevisionContent,
    SectionContent,
    SectionState,
)
from ato_service.ssp_workspace.editing import (
    WorkspaceQuestionStateError,
    answer_question,
)
from ato_service.ssp_workspace.service import (
    WorkspaceNotReviewableError,
    approve_workspace_revision,
    save_question_answer,
)


WORKSPACE_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
REVISION_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
QUESTION_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
NOW = datetime(2026, 8, 5, tzinfo=UTC)


def _question_content(state: QuestionState) -> RevisionContent:
    answer = "Dana Holloway" if state is QuestionState.ANSWERED else None
    return RevisionContent(
        sections=(
            SectionContent(
                key="system.owner",
                title="System Owner",
                content="Dana Holloway",
                state=SectionState.EDITED,
            ),
        ),
        questions=(
            QuestionContent(
                question_id=QUESTION_ID,
                question="Who is the system owner?",
                target_type="ssp_section",
                target_key="system.owner",
                owner_type="system_owner",
                state=state,
                answer=answer,
            ),
        ),
    )


@pytest.mark.parametrize(
    "state",
    (QuestionState.ANSWERED, QuestionState.DISMISSED),
)
def test_answer_question_rejects_non_open_question_without_changing_content(
    state: QuestionState,
) -> None:
    content = _question_content(state)

    with pytest.raises(WorkspaceQuestionStateError, match="question is not open"):
        answer_question(
            content,
            question_id=QUESTION_ID,
            answer="A replacement owner",
        )

    assert content.sections[0].content == "Dana Holloway"
    assert content.questions[0].state is state


def test_save_question_answer_does_not_persist_rejected_replay() -> None:
    content = _question_content(QuestionState.ANSWERED)
    revision = SimpleNamespace(content=content.model_dump(mode="json"))

    with (
        patch(
            "ato_service.ssp_workspace.service._load_exact_current_revision",
            new=AsyncMock(return_value=revision),
        ),
        patch(
            "ato_service.ssp_workspace.service._save_edited_revision",
            new=AsyncMock(),
        ) as save_revision,
    ):
        with pytest.raises(WorkspaceQuestionStateError):
            asyncio.run(
                save_question_answer(
                    AsyncMock(),
                    workspace_id=WORKSPACE_ID,
                    expected_revision_id=REVISION_ID,
                    question_id=QUESTION_ID,
                    answer="A replacement owner",
                    actor_id="isso@example.gov",
                    now=NOW,
                    audit_hmac_key=b"test-audit-key",
                )
            )

    save_revision.assert_not_awaited()


def test_approval_rejects_provisional_impact_without_confirmation() -> None:
    content = RevisionContent(
        facts=(
            FactContent(
                key="system.provisional_impact_level",
                value="moderate",
                provenance=Provenance.ISSO_ENTERED,
            ),
        )
    )
    revision = SimpleNamespace(content=content.model_dump(mode="json"))
    result = MagicMock()
    result.scalar_one_or_none.return_value = revision
    session = AsyncMock()
    session.execute.return_value = result

    with patch(
        "ato_service.ssp_workspace.service.approve_current_revision",
        new=AsyncMock(),
    ) as approve_revision:
        with pytest.raises(
            WorkspaceNotReviewableError,
            match="categorization must be explicitly confirmed",
        ):
            asyncio.run(
                approve_workspace_revision(
                    session,
                    workspace_id=WORKSPACE_ID,
                    revision_id=REVISION_ID,
                    actor_id="isso@example.gov",
                    now=NOW,
                    audit_hmac_key=b"test-audit-key",
                )
            )

    approve_revision.assert_not_awaited()
    session.execute.assert_awaited_once()


def test_approval_accepts_explicitly_confirmed_categorization() -> None:
    content = RevisionContent(
        facts=(
            FactContent(
                key="system.categorization_status",
                value="confirmed",
                provenance=Provenance.ISSO_ENTERED,
            ),
            FactContent(
                key="system.impact_level",
                value="moderate",
                provenance=Provenance.ISSO_ENTERED,
            ),
        )
    )
    revision = SimpleNamespace(content=content.model_dump(mode="json"))
    revision_result = MagicMock()
    revision_result.scalar_one_or_none.return_value = revision
    profile_result = MagicMock()
    profile_result.scalar_one.return_value = SimpleNamespace()
    evidence_result = MagicMock()
    evidence_result.first.return_value = None
    session = AsyncMock()
    session.execute.side_effect = (
        revision_result,
        profile_result,
        evidence_result,
    )
    policy = SimpleNamespace(
        implementation_statement_rules=SimpleNamespace(
            require_statement_gap_or_question_before_approval=True,
        ),
        control_response=SimpleNamespace(
            evidence_required_for_agent_statement=True,
        ),
    )
    approval = SimpleNamespace(revision_id=REVISION_ID)

    with (
        patch(
            "ato_service.ssp_workspace.service.resolve_stored_profile",
            return_value=object(),
        ),
        patch(
            "ato_service.ssp_workspace.service._metric_requirements",
            return_value=(),
        ),
        patch(
            "ato_service.ssp_workspace.service.SelectedProfilePolicy.from_resolved",
            return_value=policy,
        ),
        patch(
            "ato_service.ssp_workspace.service.load_workspace_envelope",
            new=AsyncMock(return_value={"satisfied_requirement_ids": []}),
        ),
        patch(
            "ato_service.ssp_workspace.service.approve_current_revision",
            new=AsyncMock(return_value=approval),
        ) as approve_revision,
        patch(
            "ato_service.ssp_workspace.service._audit",
            new=AsyncMock(),
        ),
    ):
        result = asyncio.run(
            approve_workspace_revision(
                session,
                workspace_id=WORKSPACE_ID,
                revision_id=REVISION_ID,
                actor_id="isso@example.gov",
                now=NOW,
                audit_hmac_key=b"test-audit-key",
            )
        )

    assert result is approval
    approve_revision.assert_awaited_once()
