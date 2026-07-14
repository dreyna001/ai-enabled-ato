"""Operator commands for rebuilding package search indexes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from ato_service.blobs import BlobStore
from ato_service.package_search_index import rebuild_revision_search_index_sync
from ato_service.runtime_config import RuntimeConfig


@dataclass(frozen=True, slots=True)
class RebuildSearchIndexReport:
    package_revision_id: uuid.UUID
    chunk_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "package_revision_id": str(self.package_revision_id),
            "chunk_count": self.chunk_count,
        }


def rebuild_package_search_index_sync(
    *,
    config: RuntimeConfig,
    dsn: str,
    package_revision_id: uuid.UUID,
) -> RebuildSearchIndexReport:
    """Rebuild one ready revision search index from operator context."""
    blob_store = BlobStore(config.storage_data_path)
    chunk_count = rebuild_revision_search_index_sync(
        dsn=dsn,
        config=config,
        blob_store=blob_store,
        package_revision_id=package_revision_id,
    )
    return RebuildSearchIndexReport(
        package_revision_id=package_revision_id,
        chunk_count=chunk_count,
    )


__all__ = ["RebuildSearchIndexReport", "rebuild_package_search_index_sync"]
