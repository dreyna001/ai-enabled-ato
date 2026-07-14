"""Unit tests for package search index collection and cursors."""

from __future__ import annotations

import uuid

from ato_service.package_search_index import (
    collect_searchable_chunks,
    decode_search_cursor,
    encode_search_cursor,
)


def _sealed(*, statement: str = "policy controls") -> object:
    return type(
        "Sealed",
        (),
        {
            "document": {
                "security_controls": {
                    "AC-1": {
                        "implementation_statement": statement,
                    }
                },
                "evidence": {},
            },
            "field_provenance": {},
        },
    )()


def test_collect_searchable_chunks_from_sealed_document() -> None:
    revision_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    artifact = type(
        "Artifact",
        (),
        {"artifact_id": artifact_id, "sha256": "d" * 64},
    )()
    pending = collect_searchable_chunks(
        package_revision_id=revision_id,
        sealed=_sealed(),
        artifacts=[artifact],
        artifact_texts={},
    )
    assert len(pending) == 1
    assert pending[0].package_revision_id == revision_id
    assert "policy" in pending[0].text


def test_search_cursor_round_trip_is_opaque_and_stable() -> None:
    cursor = encode_search_cursor(score=0.123456789, chunk_id="e" * 64)
    decoded = decode_search_cursor(cursor)
    assert decoded.score == 0.123456789
    assert decoded.chunk_id == "e" * 64


def test_pending_chunks_deduplicate_by_chunk_id() -> None:
    revision_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    pending = collect_searchable_chunks(
        package_revision_id=revision_id,
        sealed=_sealed(statement="alpha"),
        artifacts=[type("Artifact", (), {"artifact_id": artifact_id, "sha256": "a" * 64})()],
        artifact_texts={artifact_id: "alpha"},
    )
    assert len(pending) == len({item.chunk_id for item in pending})
