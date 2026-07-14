"""Deterministic POA&M and evidence-request routing after human disposition."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.audit import append_audit_event
from ato_service.domain_mapping import format_uuid


@dataclass(frozen=True, slots=True)
class PoamRoutingResult:
    evidence_request_id: uuid.UUID | None
    poam_candidate_id: uuid.UUID | None
    created: bool


async def route_disposition_side_effects(
    session: AsyncSession,
    *,
    review_revision_id: uuid.UUID,
    disposition_id: uuid.UUID,
    matrix_row_id: uuid.UUID,
    run_id: uuid.UUID,
    assessment_item_id: str,
    assessment_item_type: str,
    system_status: str,
    finding_summary: str,
    decision: str,
    actor_id: str,
    hmac_key: bytes,
    now: datetime,
) -> PoamRoutingResult:
    """Create evidence requests or POA&M candidates atomically with disposition updates."""
    from ato_service.db.models import EvidenceRequest, PoamCandidate

    if decision == "evidence_requested":
        existing = await _load_existing_evidence_request(
            session,
            review_revision_id=review_revision_id,
            matrix_row_id=matrix_row_id,
        )
        if existing is not None:
            return PoamRoutingResult(
                evidence_request_id=existing.evidence_request_id,
                poam_candidate_id=None,
                created=False,
            )
        evidence_request_id = uuid.uuid4()
        provenance = _build_provenance(
            review_revision_id=review_revision_id,
            disposition_id=disposition_id,
            matrix_row_id=matrix_row_id,
            run_id=run_id,
            assessment_item_id=assessment_item_id,
            assessment_item_type=assessment_item_type,
            system_status=system_status,
            decision=decision,
        )
        session.add(
            EvidenceRequest(
                evidence_request_id=evidence_request_id,
                review_revision_id=review_revision_id,
                disposition_id=disposition_id,
                matrix_row_id=matrix_row_id,
                run_id=run_id,
                assessment_item_id=assessment_item_id,
                assessment_item_type=assessment_item_type,
                system_status=system_status,
                finding_summary=finding_summary,
                provenance=provenance,
                created_by=actor_id,
                created_at=now,
            )
        )
        await append_audit_event(
            session,
            hmac_key=hmac_key,
            actor_type="user",
            actor_id=actor_id,
            action="evidence_request.created",
            object_type="evidence_request",
            object_id=str(evidence_request_id).lower(),
            outcome="succeeded",
            reason_code=None,
            metadata={
                "review_revision_id": str(review_revision_id).lower(),
                "matrix_row_id": str(matrix_row_id).lower(),
                "assessment_item_id": assessment_item_id,
            },
            occurred_at=now,
        )
        return PoamRoutingResult(
            evidence_request_id=evidence_request_id,
            poam_candidate_id=None,
            created=True,
        )

    if decision == "weakness_confirmed":
        existing = await _load_existing_poam_candidate(
            session,
            review_revision_id=review_revision_id,
            matrix_row_id=matrix_row_id,
        )
        if existing is not None:
            return PoamRoutingResult(
                evidence_request_id=None,
                poam_candidate_id=existing.poam_candidate_id,
                created=False,
            )
        poam_candidate_id = uuid.uuid4()
        provenance = _build_provenance(
            review_revision_id=review_revision_id,
            disposition_id=disposition_id,
            matrix_row_id=matrix_row_id,
            run_id=run_id,
            assessment_item_id=assessment_item_id,
            assessment_item_type=assessment_item_type,
            system_status=system_status,
            decision=decision,
        )
        session.add(
            PoamCandidate(
                poam_candidate_id=poam_candidate_id,
                review_revision_id=review_revision_id,
                disposition_id=disposition_id,
                matrix_row_id=matrix_row_id,
                run_id=run_id,
                assessment_item_id=assessment_item_id,
                assessment_item_type=assessment_item_type,
                system_status=system_status,
                weakness_summary=finding_summary,
                provenance=provenance,
                created_by=actor_id,
                created_at=now,
            )
        )
        try:
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            existing = await _load_existing_poam_candidate(
                session,
                review_revision_id=review_revision_id,
                matrix_row_id=matrix_row_id,
            )
            if existing is None:
                raise
            return PoamRoutingResult(
                evidence_request_id=None,
                poam_candidate_id=existing.poam_candidate_id,
                created=False,
            )
        await append_audit_event(
            session,
            hmac_key=hmac_key,
            actor_type="user",
            actor_id=actor_id,
            action="poam_candidate.created",
            object_type="poam_candidate",
            object_id=str(poam_candidate_id).lower(),
            outcome="succeeded",
            reason_code=None,
            metadata={
                "review_revision_id": str(review_revision_id).lower(),
                "matrix_row_id": str(matrix_row_id).lower(),
                "assessment_item_id": assessment_item_id,
            },
            occurred_at=now,
        )
        return PoamRoutingResult(
            evidence_request_id=None,
            poam_candidate_id=poam_candidate_id,
            created=True,
        )

    return PoamRoutingResult(
        evidence_request_id=None,
        poam_candidate_id=None,
        created=False,
    )


def _build_provenance(
    *,
    review_revision_id: uuid.UUID,
    disposition_id: uuid.UUID,
    matrix_row_id: uuid.UUID,
    run_id: uuid.UUID,
    assessment_item_id: str,
    assessment_item_type: str,
    system_status: str,
    decision: str,
) -> dict[str, Any]:
    return {
        "review_revision_id": format_uuid(review_revision_id),
        "disposition_id": format_uuid(disposition_id),
        "matrix_row_id": format_uuid(matrix_row_id),
        "run_id": format_uuid(run_id),
        "assessment_item_id": assessment_item_id,
        "assessment_item_type": assessment_item_type,
        "system_status": system_status,
        "decision": decision,
        "source": "human_disposition",
    }


async def _load_existing_evidence_request(
    session: AsyncSession,
    *,
    review_revision_id: uuid.UUID,
    matrix_row_id: uuid.UUID,
) -> Any | None:
    from ato_service.db.models import EvidenceRequest

    result = await session.execute(
        select(EvidenceRequest).where(
            EvidenceRequest.review_revision_id == review_revision_id,
            EvidenceRequest.matrix_row_id == matrix_row_id,
        )
    )
    return result.scalar_one_or_none()


async def _load_existing_poam_candidate(
    session: AsyncSession,
    *,
    review_revision_id: uuid.UUID,
    matrix_row_id: uuid.UUID,
) -> Any | None:
    from ato_service.db.models import PoamCandidate

    result = await session.execute(
        select(PoamCandidate).where(
            PoamCandidate.review_revision_id == review_revision_id,
            PoamCandidate.matrix_row_id == matrix_row_id,
        )
    )
    return result.scalar_one_or_none()
