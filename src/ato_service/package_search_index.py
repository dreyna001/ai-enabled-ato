"""Revision-scoped PostgreSQL full-text search index lifecycle."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.blobs import BlobStore
from ato_service.citation_validation import (
    CitableSource,
    build_sealed_citable_sources,
)
from ato_service.db.models import (
    PackageRevision,
    PackageRevisionSearchChunk,
    PackageRevisionSearchIndex,
    SealedPackageContent,
    SourceArtifact,
)
from ato_service.evidence_chunking import chunk_normalized_text, normalize_search_text
from ato_service.extraction import extract_content
from ato_service.extraction.limits import resolve_extraction_limits_from_config
from ato_service.extraction.types import ExtractionContext, VisionPolicy
from ato_service.runtime_config import RuntimeConfig
from ato_service.source_artifacts import read_source_artifact_bytes

SEARCH_CURSOR_VERSION = 1
MAX_SEARCH_QUERY_LENGTH = 500
MIN_SEARCH_LIMIT = 1
MAX_SEARCH_LIMIT = 100
DEFAULT_SEARCH_LIMIT = 50
MAX_SEARCH_CURSOR_LENGTH = 2048
_MAX_SEARCH_CURSOR_JSON_BYTES = 512
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_URLSAFE_BASE64_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class SearchIndexError(Exception):
    """Base error for package search index operations."""

    error_code = "state_artifact_inconsistent"


class SearchIndexNotReadyError(SearchIndexError):
    error_code = "resource_not_found"


class InvalidSearchQueryError(Exception):
    error_code = "malformed_request"


class InvalidSearchCursorError(Exception):
    error_code = "malformed_request"


class InvalidSearchLimitError(Exception):
    error_code = "malformed_request"


@dataclass(frozen=True, slots=True)
class SearchCursor:
    score: float
    chunk_id: str


@dataclass(frozen=True, slots=True)
class SearchChunkHit:
    chunk_id: str
    artifact_id: uuid.UUID
    score: float
    citation: dict[str, Any]
    text: str


@dataclass(frozen=True, slots=True)
class SearchPage:
    items: tuple[SearchChunkHit, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class PendingSearchChunk:
    chunk_id: str
    package_revision_id: uuid.UUID
    artifact_id: uuid.UUID
    artifact_sha256: str
    normalized_start: int
    normalized_end: int
    text: str


def validate_search_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_SEARCH_LIMIT
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise InvalidSearchLimitError()
    if limit < MIN_SEARCH_LIMIT or limit > MAX_SEARCH_LIMIT:
        raise InvalidSearchLimitError()
    return limit


def validate_search_query(query: str) -> str:
    normalized = query.strip()
    if not normalized:
        raise InvalidSearchQueryError()
    if len(normalized) > MAX_SEARCH_QUERY_LENGTH:
        raise InvalidSearchQueryError()
    return normalized


def encode_search_cursor(*, score: float, chunk_id: str) -> str:
    payload = {
        "v": SEARCH_CURSOR_VERSION,
        "score": round(float(score), 12),
        "id": chunk_id,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    return encoded.rstrip("=")


def decode_search_cursor(cursor: str) -> SearchCursor:
    if not isinstance(cursor, str) or not cursor or len(cursor) > MAX_SEARCH_CURSOR_LENGTH:
        raise InvalidSearchCursorError()
    if "=" in cursor or _URLSAFE_BASE64_CURSOR_PATTERN.fullmatch(cursor) is None:
        raise InvalidSearchCursorError()
    padding = "=" * (-len(cursor) % 4)
    try:
        decoded = base64.urlsafe_b64decode((cursor + padding).encode("ascii"))
    except (binascii.Error, ValueError):
        raise InvalidSearchCursorError() from None
    canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    if canonical != cursor:
        raise InvalidSearchCursorError()
    if len(decoded) > _MAX_SEARCH_CURSOR_JSON_BYTES:
        raise InvalidSearchCursorError()
    try:
        payload = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InvalidSearchCursorError() from None
    if not isinstance(payload, dict) or set(payload.keys()) != {"v", "score", "id"}:
        raise InvalidSearchCursorError()
    version = payload.get("v")
    score = payload.get("score")
    chunk_id = payload.get("id")
    if version != SEARCH_CURSOR_VERSION:
        raise InvalidSearchCursorError()
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        raise InvalidSearchCursorError()
    if not isinstance(chunk_id, str) or _SHA256_PATTERN.fullmatch(chunk_id) is None:
        raise InvalidSearchCursorError()
    return SearchCursor(score=float(score), chunk_id=chunk_id)


def collect_searchable_chunks(
    *,
    package_revision_id: uuid.UUID,
    sealed: SealedPackageContent,
    artifacts: Sequence[SourceArtifact],
    artifact_texts: dict[uuid.UUID, str],
) -> tuple[PendingSearchChunk, ...]:
    """Collect deterministic chunks from sealed content and extracted artifact text."""
    pending: dict[str, PendingSearchChunk] = {}

    citable_sources = build_sealed_citable_sources(
        sealed_document=sealed.document,
        field_provenance=sealed.field_provenance,
    )
    for source in citable_sources.values():
        artifact_id = _artifact_id_for_source(source=source, artifacts=artifacts)
        for chunk in chunk_normalized_text(
            source_sha256=source.source_sha256,
            text=source.text,
        ):
            pending[chunk.chunk_id] = PendingSearchChunk(
                chunk_id=chunk.chunk_id,
                package_revision_id=package_revision_id,
                artifact_id=artifact_id,
                artifact_sha256=source.source_sha256,
                normalized_start=chunk.normalized_start,
                normalized_end=chunk.normalized_end,
                text=chunk.text,
            )

    for artifact in artifacts:
        extracted = artifact_texts.get(artifact.artifact_id)
        if not extracted:
            continue
        normalized = normalize_search_text(extracted)
        for chunk in chunk_normalized_text(
            source_sha256=artifact.sha256,
            text=normalized,
        ):
            pending[chunk.chunk_id] = PendingSearchChunk(
                chunk_id=chunk.chunk_id,
                package_revision_id=package_revision_id,
                artifact_id=artifact.artifact_id,
                artifact_sha256=artifact.sha256,
                normalized_start=chunk.normalized_start,
                normalized_end=chunk.normalized_end,
                text=chunk.text,
            )

    return tuple(pending[key] for key in sorted(pending))


async def rebuild_revision_search_index(
    session: AsyncSession,
    *,
    package_revision_id: uuid.UUID,
    config: RuntimeConfig,
    blob_store: BlobStore,
    now: datetime,
) -> int:
    """Delete and rebuild the revision search index atomically without partial state."""
    revision = (
        await session.execute(
            select(PackageRevision).where(
                PackageRevision.package_revision_id == package_revision_id
            )
        )
    ).scalar_one_or_none()
    if revision is None:
        raise SearchIndexError("package revision not found")
    if revision.status != "ready":
        raise SearchIndexError("search index requires a ready package revision")

    sealed = (
        await session.execute(
            select(SealedPackageContent).where(
                SealedPackageContent.package_revision_id == package_revision_id
            )
        )
    ).scalar_one_or_none()
    if sealed is None:
        raise SearchIndexError("sealed package content is required")

    artifacts = (
        await session.execute(
            select(SourceArtifact).where(
                SourceArtifact.package_revision_id == package_revision_id
            )
        )
    ).scalars().all()

    artifact_texts = await _load_artifact_texts(
        artifacts=artifacts,
        config=config,
        blob_store=blob_store,
    )
    pending = collect_searchable_chunks(
        package_revision_id=package_revision_id,
        sealed=sealed,
        artifacts=artifacts,
        artifact_texts=artifact_texts,
    )

    await session.execute(
        delete(PackageRevisionSearchChunk).where(
            PackageRevisionSearchChunk.package_revision_id == package_revision_id
        )
    )
    await session.execute(
        delete(PackageRevisionSearchIndex).where(
            PackageRevisionSearchIndex.package_revision_id == package_revision_id
        )
    )

    if pending:
        rows = [
            {
                "chunk_id": chunk.chunk_id,
                "package_revision_id": chunk.package_revision_id,
                "artifact_id": chunk.artifact_id,
                "artifact_sha256": chunk.artifact_sha256,
                "normalized_start": chunk.normalized_start,
                "normalized_end": chunk.normalized_end,
                "text": chunk.text,
            }
            for chunk in pending
        ]
        await session.execute(insert(PackageRevisionSearchChunk), rows)
        await session.execute(
            text(
                """
                UPDATE package_revision_search_chunks
                SET search_vector = to_tsvector('english', text)
                WHERE package_revision_id = :package_revision_id
                """
            ),
            {"package_revision_id": package_revision_id},
        )

    await session.execute(
        insert(PackageRevisionSearchIndex).values(
            package_revision_id=package_revision_id,
            content_sha256=sealed.content_sha256,
            chunk_count=len(pending),
            indexed_at=now,
        )
    )
    return len(pending)


async def delete_revision_search_index(
    session: AsyncSession,
    *,
    package_revision_id: uuid.UUID,
) -> None:
    """Remove all persisted search chunks and index metadata for one revision."""
    await session.execute(
        delete(PackageRevisionSearchChunk).where(
            PackageRevisionSearchChunk.package_revision_id == package_revision_id
        )
    )
    await session.execute(
        delete(PackageRevisionSearchIndex).where(
            PackageRevisionSearchIndex.package_revision_id == package_revision_id
        )
    )


async def search_revision_chunks(
    session: AsyncSession,
    *,
    package_revision_id: uuid.UUID,
    query: str,
    limit: int | None = None,
    cursor: str | None = None,
) -> SearchPage:
    """Search one authorized revision with deterministic ranking and opaque cursors."""
    normalized_query = validate_search_query(query)
    page_limit = validate_search_limit(limit)
    decoded_cursor = None if cursor is None else decode_search_cursor(cursor)

    index_row = (
        await session.execute(
            select(PackageRevisionSearchIndex).where(
                PackageRevisionSearchIndex.package_revision_id == package_revision_id
            )
        )
    ).scalar_one_or_none()
    if index_row is None:
        raise SearchIndexNotReadyError()

    ts_query = func.websearch_to_tsquery("english", normalized_query)
    rank_expr = func.ts_rank_cd(
        PackageRevisionSearchChunk.search_vector,
        ts_query,
        32,
    )

    statement = (
        select(PackageRevisionSearchChunk, rank_expr.label("score"))
        .where(
            PackageRevisionSearchChunk.package_revision_id == package_revision_id,
            PackageRevisionSearchChunk.search_vector.op("@@")(ts_query),
        )
        .order_by(rank_expr.desc(), PackageRevisionSearchChunk.chunk_id.asc())
        .limit(page_limit + 1)
    )
    if decoded_cursor is not None:
        statement = statement.where(
            (rank_expr < decoded_cursor.score)
            | (
                (rank_expr == decoded_cursor.score)
                & (PackageRevisionSearchChunk.chunk_id > decoded_cursor.chunk_id)
            )
        )

    rows = (await session.execute(statement)).all()
    has_more = len(rows) > page_limit
    page_rows = rows[:page_limit]
    hits: list[SearchChunkHit] = []
    for chunk, score in page_rows:
        citation = _chunk_row_citation(chunk)
        hits.append(
            SearchChunkHit(
                chunk_id=chunk.chunk_id,
                artifact_id=chunk.artifact_id,
                score=float(score),
                citation=citation,
                text=chunk.text,
            )
        )

    next_cursor = None
    if has_more and page_rows:
        last_chunk, last_score = page_rows[-1]
        next_cursor = encode_search_cursor(score=float(last_score), chunk_id=last_chunk.chunk_id)
    return SearchPage(items=tuple(hits), next_cursor=next_cursor)


def search_hit_to_api_item(hit: SearchChunkHit) -> dict[str, Any]:
    return {
        "chunk_id": hit.chunk_id,
        "artifact_id": str(hit.artifact_id),
        "score": hit.score,
        "citation": hit.citation,
    }


async def _load_artifact_texts(
    *,
    artifacts: Sequence[SourceArtifact],
    config: RuntimeConfig,
    blob_store: BlobStore,
) -> dict[uuid.UUID, str]:
    limits = resolve_extraction_limits_from_config(config)
    vision_allowed = bool(config.document.get("VISION_MODEL_ENABLED"))
    texts: dict[uuid.UUID, str] = {}
    for artifact in artifacts:
        if artifact.extraction_status not in {"succeeded", "evidence_only"}:
            continue
        try:
            content_bytes = await asyncio.to_thread(
                read_source_artifact_bytes,
                blob_store,
                artifact,
            )
        except Exception:
            continue
        declared_format = _declared_format_for_artifact(artifact)
        try:
            outcome = await asyncio.to_thread(
                extract_content,
                content_bytes,
                limits=limits,
                vision_policy=VisionPolicy(vision_allowed=vision_allowed),
                context=ExtractionContext(
                    declared_media_type=artifact.declared_media_type,
                    detected_media_type=artifact.detected_media_type,
                    declared_format=declared_format,
                    artifact_kind=artifact.artifact_kind,
                    filename=artifact.display_filename,
                ),
            )
        except Exception:
            continue
        combined = "\n".join(segment.text for segment in outcome.segments if segment.text)
        if combined.strip():
            texts[artifact.artifact_id] = combined
    return texts


def _declared_format_for_artifact(artifact: SourceArtifact) -> str | None:
    mapping = {
        "application/pdf": "pdf",
        "text/plain": "text",
        "text/markdown": "markdown",
        "application/json": "json",
        "application/xml": "xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    }
    return mapping.get(artifact.declared_media_type)


def _artifact_id_for_source(
    *,
    source: CitableSource,
    artifacts: Sequence[SourceArtifact],
) -> uuid.UUID:
    try:
        return uuid.UUID(source.source_id)
    except ValueError:
        pass
    if artifacts:
        return artifacts[0].artifact_id
    return uuid.uuid5(uuid.NAMESPACE_URL, f"ato.search-source:{source.source_id}")


def _chunk_row_citation(chunk: PackageRevisionSearchChunk) -> dict[str, Any]:
    return {
        "source_kind": "evidence",
        "source_id": str(chunk.artifact_id),
        "source_sha256": chunk.artifact_sha256,
        "chunk_id": chunk.chunk_id,
        "start_offset": chunk.normalized_start,
        "end_offset": chunk.normalized_end,
        "page_or_section": None,
    }


def rebuild_revision_search_index_sync(
    *,
    dsn: str,
    config: RuntimeConfig,
    blob_store: BlobStore,
    package_revision_id: uuid.UUID,
) -> int:
    """Synchronous operator entrypoint for rebuilding one revision index."""
    import asyncio

    from ato_service.db.session import create_async_engine_from_url, create_session_factory

    async def _run() -> int:
        engine = create_async_engine_from_url(dsn)
        session_factory = create_session_factory(engine)
        try:
            async with session_factory() as session:
                async with session.begin():
                    return await rebuild_revision_search_index(
                        session,
                        package_revision_id=package_revision_id,
                        config=config,
                        blob_store=blob_store,
                        now=datetime.now(timezone.utc),
                    )
        finally:
            await engine.dispose()

    return asyncio.run(_run())


__all__ = [
    "InvalidSearchCursorError",
    "InvalidSearchLimitError",
    "InvalidSearchQueryError",
    "PendingSearchChunk",
    "SearchChunkHit",
    "SearchIndexError",
    "SearchIndexNotReadyError",
    "SearchPage",
    "collect_searchable_chunks",
    "decode_search_cursor",
    "delete_revision_search_index",
    "encode_search_cursor",
    "rebuild_revision_search_index",
    "rebuild_revision_search_index_sync",
    "search_hit_to_api_item",
    "search_revision_chunks",
    "validate_search_limit",
    "validate_search_query",
]
