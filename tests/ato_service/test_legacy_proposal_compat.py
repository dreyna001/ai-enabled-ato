"""Tests for legacy FactProposal draft migration compatibility."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from ato_service.db.models import FactProposal
from ato_service.legacy_proposal_compat import (
    LegacyProposalMigrationError,
    assemble_draft_from_legacy_proposals,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)
REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
SYSTEM_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
ARTIFACT_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")


def _revision() -> SimpleNamespace:
    return SimpleNamespace(
        package_revision_id=REVISION_ID,
        system_id=SYSTEM_ID,
        profile_id="fisma_agency_security",
        impact_level="moderate",
        status="awaiting_confirmation",
    )


def _system() -> SimpleNamespace:
    return SimpleNamespace(
        system_id=SYSTEM_ID,
        display_name="Legacy System",
    )


def _proposal(*, pointer: str, value: object) -> FactProposal:
    return FactProposal(
        fact_proposal_id=uuid.uuid5(REVISION_ID, pointer),
        package_revision_id=REVISION_ID,
        json_pointer=pointer,
        proposed_value=value,
        source_artifact_id=ARTIFACT_ID,
        source_sha256="a" * 64,
        source_locator={"kind": "json_pointer", "json_pointer": pointer},
        extraction_method="deterministic",
        model_step_id=None,
        review_status="pending",
        reviewed_by=None,
        reviewed_at=None,
    )


def test_assemble_draft_from_compatible_legacy_proposals() -> None:
    proposals = [
        _proposal(pointer="/package/title", value="Legacy Synthetic Package"),
        _proposal(pointer="/system/name", value="Legacy Portal"),
        _proposal(
            pointer="/security_controls/AC-1/implementation_status",
            value="implemented",
        ),
        _proposal(
            pointer="/security_controls/AC-1/summary",
            value="Access control policy reviewed annually.",
        ),
    ]

    draft = assemble_draft_from_legacy_proposals(
        revision=_revision(),
        system=_system(),
        proposals=proposals,
    )

    assert draft.document["package"]["title"] == "Legacy Synthetic Package"
    assert draft.document["system"]["display_name"] == "Legacy Portal"
    assert draft.document["security_controls"]["AC-1"]["implementation_statement"].startswith(
        "Access control"
    )


def test_assemble_draft_rejects_non_awaiting_revision() -> None:
    revision = _revision()
    revision.status = "ready"
    with pytest.raises(LegacyProposalMigrationError):
        assemble_draft_from_legacy_proposals(
            revision=revision,
            system=_system(),
            proposals=[_proposal(pointer="/package/title", value="Blocked")],
        )


def test_assemble_draft_rejects_incompatible_proposals() -> None:
    with pytest.raises(LegacyProposalMigrationError):
        assemble_draft_from_legacy_proposals(
            revision=_revision(),
            system=_system(),
            proposals=[_proposal(pointer="/package/profile_id", value="unknown_profile")],
        )
