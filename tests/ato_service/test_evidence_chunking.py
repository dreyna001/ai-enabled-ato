"""Unit tests for deterministic evidence chunking."""

from __future__ import annotations

from ato_service.citation_validation import derive_chunk_id
from ato_service.evidence_chunking import (
    CHUNK_MAX_CHARACTERS,
    CHUNK_OVERLAP_CHARACTERS,
    chunk_normalized_text,
)


def test_single_short_text_produces_one_chunk() -> None:
    source_sha256 = "a" * 64
    chunks = chunk_normalized_text(source_sha256=source_sha256, text="policy statement")
    assert len(chunks) == 1
    assert chunks[0].normalized_start == 0
    assert chunks[0].text == "policy statement"
    assert chunks[0].chunk_id == derive_chunk_id(
        source_sha256=source_sha256,
        start_offset=0,
        end_offset=len("policy statement"),
        text="policy statement",
    )


def test_long_text_chunks_with_overlap_are_deterministic() -> None:
    source_sha256 = "b" * 64
    text = "x" * (CHUNK_MAX_CHARACTERS + 1000)
    first = chunk_normalized_text(source_sha256=source_sha256, text=text)
    second = chunk_normalized_text(source_sha256=source_sha256, text=text)
    assert first == second
    assert len(first) >= 2
    assert first[0].normalized_end == CHUNK_MAX_CHARACTERS
    assert first[1].normalized_start == CHUNK_MAX_CHARACTERS - CHUNK_OVERLAP_CHARACTERS


def test_empty_text_produces_no_chunks() -> None:
    assert chunk_normalized_text(source_sha256="c" * 64, text="   ") == ()
