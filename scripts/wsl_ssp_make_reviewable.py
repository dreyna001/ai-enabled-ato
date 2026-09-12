#!/usr/bin/env python3
"""Dev-only: patch the current SSP workspace revision so ISSO approval can succeed."""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ato_service.db.dsn import read_database_dsn_from_file
from ato_service.db.session import create_async_engine_from_url, require_postgresql_url
from ato_service.ssp_workspace.contracts import (
    ControlContent,
    ControlState,
    EvidenceLink,
    FactContent,
    FactState,
    Provenance,
    RevisionContent,
    SectionState,
)
from ato_service.ssp_workspace.metrics import (
    requirement_is_satisfied,
    workspace_is_reviewable,
)
from ato_service.ssp_workspace.persistence import save_revision
from ato_service.ssp_workspace.service import _effective_metric_facts, _metric_requirements


def _placeholder_value(requirement: object) -> object:
    value_type = requirement.value_type
    if value_type == "array":
        return ["Documented in ISSO review prep."]
    if value_type == "boolean":
        return True
    if value_type == "number":
        return 1
    if requirement.enum_values:
        return requirement.enum_values[0]
    return f"Documented value for {requirement.key}."


def _link(artifact_id: uuid.UUID) -> EvidenceLink:
    return EvidenceLink(artifact_id=artifact_id, locator={"page": 1})


def _patch_revision_content(
    content: RevisionContent,
    requirements: tuple,
    artifact_id: uuid.UUID,
) -> RevisionContent:
    link = _link(artifact_id)
    facts = {item.key: item for item in content.facts}
    sections = {item.key: item for item in content.sections}
    controls: list[ControlContent] = []

    for requirement in requirements:
        if not requirement.required:
            continue
        section = sections.get(requirement.key)
        if section is not None:
            new_content = section.content.strip() or str(_placeholder_value(requirement))
            sections[requirement.key] = section.model_copy(
                update={
                    "content": new_content,
                    "state": SectionState.EDITED,
                    "evidence": section.evidence or (link,),
                }
            )
            continue
        existing = facts.get(requirement.key)
        if existing is None or existing.state.value != "active":
            facts[requirement.key] = FactContent(
                key=requirement.key,
                value=_placeholder_value(requirement),
                provenance=Provenance.ISSO_ENTERED,
                evidence=(),
                state=FactState.ACTIVE,
            )
        elif existing.provenance is not Provenance.ISSO_ENTERED:
            facts[requirement.key] = existing.model_copy(
                update={
                    "provenance": Provenance.ISSO_ENTERED,
                    "evidence": existing.evidence or (),
                }
            )

    for control in content.controls:
        statement = control.implementation_statement.strip()
        updates: dict[str, object] = {}
        if statement:
            updates["state"] = ControlState.REVIEWED
            if not control.evidence:
                updates["evidence"] = (link,)
        elif not (control.unresolved_reason or "").strip():
            updates["unresolved_reason"] = "Tracked gap pending ISSO follow-up."
        controls.append(
            control.model_copy(update=updates) if updates else control
        )

    cat_keys = (
        "system.categorization_status",
        "system.confidentiality_impact",
        "system.integrity_impact",
        "system.availability_impact",
        "system.impact_level",
    )
    for key in cat_keys:
        if key in facts:
            continue
        if key == "system.categorization_status":
            value: object = "confirmed"
        elif key == "system.impact_level":
            value = "moderate"
        else:
            value = "moderate"
        facts[key] = FactContent(
            key=key,
            value=value,
            provenance=Provenance.ISSO_ENTERED,
            evidence=(),
            state=FactState.ACTIVE,
        )

    return content.model_copy(
        update={
            "facts": tuple(facts[key] for key in sorted(facts)),
            "sections": tuple(sections[key] for key in sorted(sections)),
            "controls": tuple(controls),
        }
    )


def _resolve_dsn(*, dsn_file: Path) -> str:
    inline = os.environ.get("ATO_DATABASE_DSN", "").strip()
    if inline:
        return require_postgresql_url(inline)
    return read_database_dsn_from_file(dsn_file)


async def _run(
    *,
    dsn_file: Path,
    workspace_id: uuid.UUID | None,
    actor_id: str,
) -> None:
    from ato_service.db.models import (
        SspEvidenceArtifact,
        SspProfileVersion,
        SspWorkspace,
        SspWorkspaceRevision,
    )

    dsn = _resolve_dsn(dsn_file=dsn_file)
    engine = create_async_engine_from_url(dsn)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    async with session_factory() as session:
        if workspace_id is None:
            workspace_id = (
                await session.execute(
                    select(SspWorkspace.workspace_id).order_by(
                        SspWorkspace.created_at.desc()
                    )
                )
            ).scalars().first()
        if workspace_id is None:
            raise SystemExit("No SSP workspace found.")

        workspace = (
            await session.execute(
                select(SspWorkspace).where(SspWorkspace.workspace_id == workspace_id)
            )
        ).scalar_one()
        if workspace.current_revision_id is None:
            raise SystemExit("Workspace has no current revision.")

        profile_row = (
            await session.execute(
                select(SspProfileVersion).where(
                    SspProfileVersion.profile_version_id
                    == workspace.profile_version_id
                )
            )
        ).scalar_one()

        artifact_row = (
            await session.execute(
                select(SspEvidenceArtifact)
                .where(
                    SspEvidenceArtifact.workspace_id == workspace_id,
                    SspEvidenceArtifact.removed_at.is_(None),
                )
                .order_by(SspEvidenceArtifact.uploaded_at.asc())
            )
        ).scalars().first()
        if artifact_row is None:
            raise SystemExit("Workspace has no evidence artifacts to link.")
        if artifact_row.status in {"uploaded", "processing"}:
            artifact_row.status = "processed"
            artifact_row.detected_format = artifact_row.detected_format or "text"
            artifact_row.processed_at = now

        revision = (
            await session.execute(
                select(SspWorkspaceRevision).where(
                    SspWorkspaceRevision.revision_id == workspace.current_revision_id
                )
            )
        ).scalar_one()

        requirements = _metric_requirements(profile_row)
        content = RevisionContent.model_validate(revision.content)
        patched = _patch_revision_content(content, requirements, artifact_row.evidence_artifact_id)

        effective = _effective_metric_facts(patched, requirements)
        fact_by_key = {item.key: item for item in effective}
        if not workspace_is_reviewable(
            requirements=requirements,
            facts=effective,
            controls=patched.controls,
            questions=patched.questions,
            all_jobs_terminal=True,
            revision_saved=True,
            content_consistent=True,
            evidence_required_for_agent_statement=True,
            require_statement_gap_or_question_before_approval=True,
        ):
            missing = [
                req.key
                for req in requirements
                if req.required
                and not requirement_is_satisfied(req, fact_by_key.get(req.key))
            ]
            raise SystemExit(
                "Patch did not reach reviewable state; missing requirements: "
                + ", ".join(missing[:20])
            )

        new_revision = await save_revision(
            session,
            workspace_id=workspace_id,
            content=patched,
            created_by=actor_id,
            now=now,
            expected_revision_id=revision.revision_id,
        )
        await session.commit()
        print(
            f"workspace_id={workspace_id} "
            f"revision_id={new_revision.revision_id} "
            f"version={new_revision.version}"
        )

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", type=uuid.UUID, default=None)
    parser.add_argument("--actor-id", default="dev-portal-user")
    parser.add_argument(
        "--dsn-file",
        type=Path,
        default=Path(os.environ.get("ATO_DATABASE_DSN_FILE", "/etc/ato-analyzer/credentials/database-dsn")),
    )
    args = parser.parse_args()
    if not args.dsn_file.is_file():
        raise SystemExit(f"Missing DSN file: {args.dsn_file}")
    asyncio.run(
        _run(
            dsn_file=args.dsn_file,
            workspace_id=args.workspace_id,
            actor_id=args.actor_id,
        )
    )


if __name__ == "__main__":
    main()
