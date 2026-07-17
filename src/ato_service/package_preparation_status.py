"""Read-only external-facing package preparation status derivation.

``package_preparation_status`` is a computed view over export approval state.
It is not persisted and does not replace ``PackageRevision.status`` (intake /
sealed lifecycle) or export-draft lifecycle states.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.export_service import compute_current_payload_manifest_sha256

PACKAGE_PREPARATION_STATUS_IN_PROGRESS = "in_progress"
PACKAGE_PREPARATION_STATUS_READY_FOR_EXTERNAL_REVIEW = "ready_for_external_review"

PACKAGE_PREPARATION_STATUS_VALUES = frozenset(
    {
        PACKAGE_PREPARATION_STATUS_IN_PROGRESS,
        PACKAGE_PREPARATION_STATUS_READY_FOR_EXTERNAL_REVIEW,
    }
)


@dataclass(frozen=True, slots=True)
class _ExportCandidate:
    package_revision_id: uuid.UUID
    export_status: str
    review_revision_id: uuid.UUID
    run_id: uuid.UUID
    draft_hash: str
    approval_hash: str
    expires_at: datetime


async def _load_export_candidates(
    session: AsyncSession,
    *,
    package_revision_ids: Sequence[uuid.UUID],
) -> tuple[_ExportCandidate, ...]:
    if not package_revision_ids:
        return ()

    from ato_service.db.models import AnalysisRun, Approval, ExportDraft, ReviewRevision

    result = await session.execute(
        select(
            AnalysisRun.package_revision_id,
            ExportDraft.status.label("export_status"),
            ExportDraft.review_revision_id,
            AnalysisRun.run_id,
            ExportDraft.payload_manifest_sha256.label("draft_hash"),
            Approval.payload_manifest_sha256.label("approval_hash"),
            Approval.expires_at,
        )
        .join(ReviewRevision, ReviewRevision.run_id == AnalysisRun.run_id)
        .join(ExportDraft, ExportDraft.review_revision_id == ReviewRevision.review_revision_id)
        .join(Approval, Approval.export_draft_id == ExportDraft.export_draft_id)
        .where(AnalysisRun.package_revision_id.in_(tuple(package_revision_ids)))
        .where(ExportDraft.status.in_(("approved", "exported")))
        .where(Approval.decision == "approved")
        .where(ExportDraft.payload_manifest_sha256 == Approval.payload_manifest_sha256)
    )
    return tuple(
        _ExportCandidate(
            package_revision_id=row.package_revision_id,
            export_status=row.export_status,
            review_revision_id=row.review_revision_id,
            run_id=row.run_id,
            draft_hash=row.draft_hash,
            approval_hash=row.approval_hash,
            expires_at=row.expires_at,
        )
        for row in result.all()
    )


async def _load_review_binding_contexts(
    session: AsyncSession,
    *,
    review_revision_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, tuple[Any, Any, Any]]:
    if not review_revision_ids:
        return {}

    from ato_service.db.models import AnalysisRun, PackageRevision, ReviewRevision, SealedPackageContent

    review_result = await session.execute(
        select(ReviewRevision).where(
            ReviewRevision.review_revision_id.in_(tuple(review_revision_ids))
        )
    )
    review_revisions = {row.review_revision_id: row for row in review_result.scalars().all()}
    run_ids = {row.run_id for row in review_revisions.values()}
    if not run_ids:
        return {}

    run_result = await session.execute(
        select(AnalysisRun).where(AnalysisRun.run_id.in_(tuple(run_ids)))
    )
    runs = {row.run_id: row for row in run_result.scalars().all()}
    revision_ids = {row.package_revision_id for row in runs.values()}
    if not revision_ids:
        return {}

    revision_result = await session.execute(
        select(PackageRevision).where(
            PackageRevision.package_revision_id.in_(tuple(revision_ids))
        )
    )
    revisions = {
        row.package_revision_id: row for row in revision_result.scalars().all()
    }
    sealed_result = await session.execute(
        select(SealedPackageContent).where(
            SealedPackageContent.package_revision_id.in_(tuple(revision_ids))
        )
    )
    sealed_by_revision = {
        row.package_revision_id: row for row in sealed_result.scalars().all()
    }

    contexts: dict[uuid.UUID, tuple[Any, Any, Any]] = {}
    for review_revision_id, review_revision in review_revisions.items():
        run = runs.get(review_revision.run_id)
        if run is None:
            continue
        revision = revisions.get(run.package_revision_id)
        if revision is None:
            continue
        sealed = sealed_by_revision.get(revision.package_revision_id)
        if sealed is None:
            continue
        contexts[review_revision_id] = (run, revision, sealed)
    return contexts


async def _candidate_is_ready(
    candidate: _ExportCandidate,
    *,
    binding_contexts: dict[uuid.UUID, tuple[Any, Any, Any]],
    project_root: Path,
    now: datetime,
    session: AsyncSession,
) -> bool:
    if candidate.export_status == "exported":
        return True
    if candidate.export_status != "approved":
        return False
    if now >= candidate.expires_at:
        return False
    context = binding_contexts.get(candidate.review_revision_id)
    if context is None:
        return False
    run, revision, sealed = context
    current_hash = await compute_current_payload_manifest_sha256(
        session,
        review_revision_id=candidate.review_revision_id,
        run_id=run.run_id,
        revision=revision,
        sealed=sealed,
        project_root=project_root,
        authority_manifest_id=revision.authority_manifest_id,
    )
    return current_hash == candidate.approval_hash


async def resolve_preparation_status_batch(
    session: AsyncSession,
    package_revision_ids: Sequence[uuid.UUID],
    *,
    project_root: Path | None,
    now: datetime,
) -> dict[uuid.UUID, str]:
    """Resolve preparation status for many revisions with bounded queries.

    Uses one join query for export candidates, then one batched context load
    and at most one hash recompute per distinct approved review revision.
    """
    unique_ids = tuple(dict.fromkeys(package_revision_ids))
    default_status = {
        revision_id: PACKAGE_PREPARATION_STATUS_IN_PROGRESS for revision_id in unique_ids
    }
    if not unique_ids:
        return default_status

    candidates = await _load_export_candidates(session, package_revision_ids=unique_ids)
    if not candidates:
        return default_status

    approved_review_ids = tuple(
        dict.fromkeys(
            candidate.review_revision_id
            for candidate in candidates
            if candidate.export_status == "approved"
        )
    )
    binding_contexts: dict[uuid.UUID, tuple[Any, Any, Any]] = {}
    if approved_review_ids and project_root is not None:
        binding_contexts = await _load_review_binding_contexts(
            session,
            review_revision_ids=approved_review_ids,
        )

    ready_revision_ids: set[uuid.UUID] = set()
    for candidate in candidates:
        if candidate.export_status == "exported":
            ready_revision_ids.add(candidate.package_revision_id)
            continue
        if project_root is None or not await _candidate_is_ready(
            candidate,
            binding_contexts=binding_contexts,
            project_root=project_root,
            now=now,
            session=session,
        ):
            continue
        ready_revision_ids.add(candidate.package_revision_id)

    return {
        revision_id: (
            PACKAGE_PREPARATION_STATUS_READY_FOR_EXTERNAL_REVIEW
            if revision_id in ready_revision_ids
            else PACKAGE_PREPARATION_STATUS_IN_PROGRESS
        )
        for revision_id in unique_ids
    }


async def resolve_preparation_status(
    session: AsyncSession,
    *,
    package_revision_id: uuid.UUID,
    project_root: Path | None,
    now: datetime,
) -> str:
    """Resolve preparation status for one revision."""
    statuses = await resolve_preparation_status_batch(
        session,
        (package_revision_id,),
        project_root=project_root,
        now=now,
    )
    return statuses[package_revision_id]
