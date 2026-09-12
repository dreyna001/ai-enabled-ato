"""PostgreSQL retrieval breadth, source freshness, and bounded memory checks."""

from __future__ import annotations

import asyncio
from pathlib import Path
import uuid

import pytest
from sqlalchemy import update

from ato_service.db.models import SspControlStatement, SspEvidenceArtifact
from ato_service.db.session import create_session_factory
from ato_service.ssp_workspace import chat
from ato_service.ssp_workspace.chat_contracts import ChatMessageRequest
from tests.ato_service.test_ssp_workspace_boundaries import _dev_model_config, _seed_workspace
from tests.integration_support.postgres import postgres_integration_harness


@pytest.mark.integration
def test_retrieval_ranks_all_records_and_late_segments_before_bounding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat, "MAX_CHAT_CANONICAL_ROWS_PER_KIND", 1)

    async def exercise() -> None:
        async with postgres_integration_harness(tmp_path, ordinary_session=True) as h:
            workspace, _ = await _seed_workspace(
                create_session_factory(h.engine), h.isolated_schema,
                actor_id="reader", hmac_key=h.hmac_key, now=h.now,
            )
            target_id = uuid.UUID(int=(1 << 128) - 1)
            h.session.add_all([
                SspControlStatement(
                    control_statement_id=uuid.UUID(int=1), revision_id=workspace.current_revision_id,
                    control_id="XX-1", title="Unrelated", status="empty", implementation_statement="",
                ),
                SspControlStatement(
                    control_statement_id=target_id, revision_id=workspace.current_revision_id,
                    control_id="XX-2", title="Later control", status="generated",
                    implementation_statement="x" * 5000 + " quantumcert account protection",
                ),
            ])
            artifact_id = uuid.uuid4()
            h.session.add(SspEvidenceArtifact(
                evidence_artifact_id=artifact_id, workspace_id=workspace.workspace_id,
                storage_key="aa/" + "a" * 64, sha256="a" * 64, size_bytes=1,
                display_filename="source.txt", media_type="text/plain", detected_format="text",
                status="processed", uploaded_by="reader", uploaded_at=h.now, processed_at=h.now,
                extracted_segments=[{"text": "unrelated"}] * 10 + [
                    {"text": "x" * 5000 + " quantumcert documented here", "locator": {"page": 11}},
                ],
            ))
            await h.session.commit()
            context = await chat._load_context(
                h.session, workspace_id=workspace.workspace_id, now=h.now, query="quantumcert",
            )
            control = next(source for source in context.sources if source.source_id == f"control:{target_id}")
            evidence = next(source for source in context.sources if source.source_id == f"evidence:{artifact_id}")
            assert "quantumcert" in control.text
            assert "quantumcert" in evidence.text
            assert len(control.text) <= chat.MAX_CHAT_SOURCE_SNIPPET_CHARACTERS
            assert len(evidence.text) <= chat.MAX_CHAT_SOURCE_SNIPPET_CHARACTERS
            assert all(source.kind != "chat" for source in context.sources)

    asyncio.run(exercise())


@pytest.mark.integration
def test_freshness_tracks_evidence_outside_selected_page_and_query_is_not_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat, "MAX_CHAT_CANONICAL_ROWS_PER_KIND", 1)

    async def exercise() -> None:
        async with postgres_integration_harness(tmp_path, ordinary_session=True) as h:
            workspace, _ = await _seed_workspace(
                create_session_factory(h.engine), h.isolated_schema,
                actor_id="reader", hmac_key=h.hmac_key, now=h.now,
            )
            identities = [uuid.UUID(int=10), uuid.UUID(int=11)]
            for index, identity in enumerate(identities):
                digest = str(index + 1) * 64
                h.session.add(SspEvidenceArtifact(
                    evidence_artifact_id=identity, workspace_id=workspace.workspace_id,
                    storage_key=digest[:2] + "/" + digest, sha256=digest, size_bytes=1,
                    display_filename=f"{index}.txt", media_type="text/plain", status="uploaded",
                    uploaded_by="reader", uploaded_at=h.now, extracted_segments=[],
                ))
            await h.session.commit()
            before = await chat._load_context(h.session, workspace_id=workspace.workspace_id, now=h.now)
            other_query = await chat._load_context(
                h.session, workspace_id=workspace.workspace_id, now=h.now, query="something else",
                include_sources=False,
            )
            assert before.fingerprint == other_query.fingerprint
            assert other_query.sources == ()
            assert f"evidence:{identities[1]}" not in {item.source_id for item in before.sources}
            await h.session.execute(update(SspEvidenceArtifact).where(
                SspEvidenceArtifact.evidence_artifact_id == identities[1],
            ).values(removed_at=h.now, removed_by="reader"))
            await h.session.commit()
            after = await chat._load_context(
                h.session, workspace_id=workspace.workspace_id, now=h.now, include_sources=False,
            )
            assert before.fingerprint != after.fingerprint

    asyncio.run(exercise())


def test_prompt_omits_old_advisory_memory_to_fit_without_duplicating_sources(tmp_path: Path) -> None:
    config = _dev_model_config(tmp_path)
    revision = uuid.uuid4()
    context = chat._ContextSnapshot(
        uuid.uuid4(), revision, "f" * 64,
        (
            chat._source(source_id="profile:example", label="Pinned profile", revision_id=None,
                         kind="profile", text="Synthetic requirements"),
            chat._source(source_id="revision:example", label="Current revision", revision_id=revision,
                         kind="revision", text="Working revision"),
            chat._source(source_id="fact:example", label="Boundary", revision_id=revision,
                         kind="fact", text="Boundary is documented; human entered, not certified."),
        ),
    )
    prompt, allowed = chat._build_prompt(
        context=context, history=[(i, "user", "界" * 800, False) for i in range(8)],
        payload=ChatMessageRequest(message="Explain the boundary", expected_revision_id=revision,
                                   request_id=uuid.uuid4(), expected_sequence=8),
        limits=config.chat_limits, config=config,
    )
    assert "fact:example" in allowed
    assert prompt.user.count("profile:example") == 1
    assert prompt.user.count("界") < 8 * 800
