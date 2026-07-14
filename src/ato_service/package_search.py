"""Revision-scoped package search API helpers."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.package_search_index import (
    InvalidSearchCursorError,
    InvalidSearchLimitError,
    InvalidSearchQueryError,
    SearchIndexNotReadyError,
    search_hit_to_api_item,
    search_revision_chunks,
)


async def search_revision_content(
    session: AsyncSession,
    *,
    package_revision_id: Any,
    query: str,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Search one authorized revision using PostgreSQL full-text search."""
    page = await search_revision_chunks(
        session,
        package_revision_id=package_revision_id,
        query=query,
        limit=limit,
        cursor=cursor,
    )
    return {
        "items": [search_hit_to_api_item(hit) for hit in page.items],
        "next_cursor": page.next_cursor,
    }


__all__ = [
    "InvalidSearchCursorError",
    "InvalidSearchLimitError",
    "InvalidSearchQueryError",
    "SearchIndexNotReadyError",
    "search_revision_content",
]
