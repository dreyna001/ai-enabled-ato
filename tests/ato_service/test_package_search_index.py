"""Unit tests for package search index collection and cursors."""

from __future__ import annotations

import uuid

from ato_service.package_search_index import (
    _INSERT_SEARCH_CHUNK_SQL,
    collect_searchable_chunks,
    decode_search_cursor,
    encode_search_cursor,
)


def _sealed(
    *,
    statement: str = "policy controls",
    field_provenance: dict[str, object] | None = None,
) -> object:
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
            "field_provenance": field_provenance or {},
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
        sealed=_sealed(
            field_provenance={
                "/security_controls/AC-1/implementation_statement": {
                    "source_artifact_id": str(artifact_id),
                    "source_sha256": "d" * 64,
                }
            }
        ),
        artifacts=[artifact],
        artifact_texts={},
    )
    assert len(pending) == 1
    assert pending[0].package_revision_id == revision_id
    assert "policy" in pending[0].text


def test_orphaned_provenance_is_not_attributed_to_another_artifact() -> None:
    revision_id = uuid.uuid4()
    persisted_artifact_id = uuid.uuid4()
    orphaned_artifact_id = uuid.uuid4()
    artifact = type(
        "Artifact",
        (),
        {"artifact_id": persisted_artifact_id, "sha256": "d" * 64},
    )()
    sealed = _sealed(
        field_provenance={
            "/security_controls/AC-1/implementation_statement": {
                "source_artifact_id": str(orphaned_artifact_id),
                "source_sha256": "e" * 64,
            }
        }
    )

    pending = collect_searchable_chunks(
        package_revision_id=revision_id,
        sealed=sealed,
        artifacts=[artifact],
        artifact_texts={},
    )

    assert pending == ()


def test_sealed_source_without_persisted_artifact_is_skipped() -> None:
    pending = collect_searchable_chunks(
        package_revision_id=uuid.uuid4(),
        sealed=_sealed(),
        artifacts=[],
        artifact_texts={},
    )

    assert pending == ()


def test_insert_search_chunk_sql_populates_search_vector() -> None:
    sql = str(_INSERT_SEARCH_CHUNK_SQL)
    assert "search_vector" in sql
    assert "to_tsvector('english', CAST(:search_text AS text))" in sql
    assert ":chunk_text" in sql


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
