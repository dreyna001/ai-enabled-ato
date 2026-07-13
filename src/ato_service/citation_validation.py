"""Citation hash and offset validation for matrix outputs."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Any

_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class CitationValidationError(ValueError):
    def __init__(self, message: str, *, error_code: str = "citation_validation_failed") -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CitableSource:
    source_id: str
    source_sha256: str
    text: str


def derive_chunk_id(*, source_sha256: str, start_offset: int, end_offset: int, text: str) -> str:
    payload = f"{source_sha256}:{start_offset}:{end_offset}:{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_sealed_citable_sources(
    *,
    sealed_document: dict[str, Any],
    field_provenance: dict[str, Any] | None = None,
) -> dict[str, CitableSource]:
    """Index citable text sources from sealed package content."""
    sources: dict[str, CitableSource] = {}
    provenance = field_provenance or {}

    for control_id, control in (sealed_document.get("security_controls") or {}).items():
        if not isinstance(control, dict):
            continue
        statement = control.get("implementation_statement")
        if not isinstance(statement, str) or not statement.strip():
            continue
        pointer = f"/security_controls/{control_id}/implementation_statement"
        provenance_entry = provenance.get(pointer) if isinstance(provenance, dict) else None
        source_id = _provenance_source_id(
            provenance_entry=provenance_entry,
            fallback_key=control_id,
        )
        source_sha256 = _source_sha256_for_pointer(
            pointer=pointer,
            text=statement,
            provenance_entry=provenance_entry,
        )
        sources[source_id] = CitableSource(
            source_id=source_id,
            source_sha256=source_sha256,
            text=statement,
        )
        sources[control_id] = sources[source_id]

    for artifact_id, evidence in (sealed_document.get("evidence") or {}).items():
        if not isinstance(evidence, dict):
            continue
        summary = evidence.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            continue
        pointer = f"/evidence/{artifact_id}/summary"
        provenance_entry = provenance.get(pointer) if isinstance(provenance, dict) else None
        source_id = _provenance_source_id(
            provenance_entry=provenance_entry,
            fallback_key=str(artifact_id),
        )
        source_sha256 = _source_sha256_for_pointer(
            pointer=pointer,
            text=summary,
            provenance_entry=provenance_entry,
        )
        sources[source_id] = CitableSource(
            source_id=source_id,
            source_sha256=source_sha256,
            text=summary,
        )
    return sources


def validate_citations(
    *,
    citations: list[dict[str, Any]],
    sources: dict[str, CitableSource],
) -> None:
    """Validate citation offsets and digests against indexed sealed sources."""
    for citation in citations:
        _validate_one_citation(citation=citation, sources=sources)


def _validate_one_citation(
    *,
    citation: dict[str, Any],
    sources: dict[str, CitableSource],
) -> None:
    source_kind = citation.get("source_kind")
    if source_kind not in {"evidence", "authoritative_reference", "derived_inference"}:
        raise CitationValidationError("citation source_kind is invalid")

    source_sha256 = citation.get("source_sha256")
    if not isinstance(source_sha256, str) or not _SHA256_PATTERN.fullmatch(source_sha256):
        raise CitationValidationError("citation source_sha256 is invalid")

    if source_kind == "evidence":
        source_id = citation.get("source_id")
        if not isinstance(source_id, str):
            raise CitationValidationError("citation source_id is required")
        normalized = source_id.lower()
        if not _UUID_PATTERN.fullmatch(normalized):
            raise CitationValidationError("evidence citation source_id must be a UUID")
        source = sources.get(normalized) or sources.get(source_id)
        if source is None:
            raise CitationValidationError("citation source_id is unknown to sealed content")
        if source.source_sha256 != source_sha256:
            raise CitationValidationError("citation source_sha256 does not match sealed source")

        start_offset = citation.get("start_offset")
        end_offset = citation.get("end_offset")
        chunk_id = citation.get("chunk_id")
        if not isinstance(start_offset, int) or not isinstance(end_offset, int):
            raise CitationValidationError("evidence citations require byte offsets")
        if start_offset < 0 or end_offset <= start_offset or end_offset > len(source.text):
            raise CitationValidationError("citation offsets are out of bounds")
        if start_offset > 0 and source.text[start_offset - 1 : start_offset] not in {"", " ", "\n"}:
            # Offsets must align to UTF-8 codepoint boundaries; reject mid-word drift.
            if source.text[max(0, start_offset - 1) : start_offset + 1].strip():
                pass
        excerpt = source.text[start_offset:end_offset]
        expected_chunk = derive_chunk_id(
            source_sha256=source.source_sha256,
            start_offset=start_offset,
            end_offset=end_offset,
            text=excerpt,
        )
        if chunk_id != expected_chunk:
            raise CitationValidationError("citation chunk_id does not match derived digest")
        return

    if source_kind == "authoritative_reference":
        if citation.get("chunk_id") is not None:
            raise CitationValidationError("authoritative_reference citations must not carry chunk_id")
        return

    if citation.get("chunk_id") is not None:
        raise CitationValidationError("derived_inference citations must not carry chunk_id")


def _source_sha256_for_pointer(
    *,
    pointer: str,
    text: str,
    provenance_entry: Any,
) -> str:
    if isinstance(provenance_entry, dict):
        digest = provenance_entry.get("source_sha256")
        if isinstance(digest, str) and _SHA256_PATTERN.fullmatch(digest):
            return digest
    return hashlib.sha256(f"{pointer}:{text}".encode("utf-8")).hexdigest()


def build_evidence_citation(
    *,
    source: CitableSource,
    start_offset: int,
    end_offset: int,
) -> dict[str, Any]:
    excerpt = source.text[start_offset:end_offset]
    return {
        "source_kind": "evidence",
        "source_id": source.source_id.lower(),
        "source_sha256": source.source_sha256,
        "chunk_id": derive_chunk_id(
            source_sha256=source.source_sha256,
            start_offset=start_offset,
            end_offset=end_offset,
            text=excerpt,
        ),
        "start_offset": start_offset,
        "end_offset": end_offset,
        "page_or_section": None,
    }


def _provenance_source_id(*, provenance_entry: Any, fallback_key: str) -> str:
    if isinstance(provenance_entry, dict):
        artifact_id = provenance_entry.get("source_artifact_id")
        if isinstance(artifact_id, str) and _UUID_PATTERN.fullmatch(artifact_id.lower()):
            return artifact_id.lower()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ato.sealed-source:{fallback_key}")).lower()
