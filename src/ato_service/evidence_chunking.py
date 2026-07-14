"""Deterministic evidence chunking per ATO spec Section 13.5."""

from __future__ import annotations

from dataclasses import dataclass

from ato_service.citation_validation import derive_chunk_id

CHUNK_MAX_CHARACTERS = 6000
CHUNK_OVERLAP_CHARACTERS = 500


@dataclass(frozen=True, slots=True)
class EvidenceChunkSlice:
    """One immutable searchable chunk with normalized offsets."""

    normalized_start: int
    normalized_end: int
    text: str
    chunk_id: str


def normalize_search_text(text: str) -> str:
    """Normalize extracted text to UTF-8 with normalized line endings."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def chunk_normalized_text(
    *,
    source_sha256: str,
    text: str,
) -> tuple[EvidenceChunkSlice, ...]:
    """Split normalized text into immutable chunks with deterministic overlap."""
    normalized = normalize_search_text(text)
    if not normalized.strip():
        return ()

    if len(normalized) <= CHUNK_MAX_CHARACTERS:
        start = 0
        end = len(normalized)
        chunk_text = normalized[start:end]
        return (
            EvidenceChunkSlice(
                normalized_start=start,
                normalized_end=end,
                text=chunk_text,
                chunk_id=derive_chunk_id(
                    source_sha256=source_sha256,
                    start_offset=start,
                    end_offset=end,
                    text=chunk_text,
                ),
            ),
        )

    slices: list[EvidenceChunkSlice] = []
    step = CHUNK_MAX_CHARACTERS - CHUNK_OVERLAP_CHARACTERS
    if step < 1:
        raise ValueError("chunk overlap must be smaller than chunk size")
    start = 0
    while start < len(normalized):
        end = min(start + CHUNK_MAX_CHARACTERS, len(normalized))
        chunk_text = normalized[start:end]
        slices.append(
            EvidenceChunkSlice(
                normalized_start=start,
                normalized_end=end,
                text=chunk_text,
                chunk_id=derive_chunk_id(
                    source_sha256=source_sha256,
                    start_offset=start,
                    end_offset=end,
                    text=chunk_text,
                ),
            )
        )
        if end >= len(normalized):
            break
        start += step
    return tuple(slices)


__all__ = [
    "CHUNK_MAX_CHARACTERS",
    "CHUNK_OVERLAP_CHARACTERS",
    "EvidenceChunkSlice",
    "chunk_normalized_text",
    "normalize_search_text",
]
