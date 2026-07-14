"""Citation hash and offset validation tests."""

from __future__ import annotations


import pytest

from ato_service.citation_validation import (
    CitationValidationError,
    build_evidence_citation,
    build_sealed_citable_sources,
    derive_chunk_id,
    validate_citations,
)

ARTIFACT_ID = "44444444-4444-4444-8444-444444444444"


def _document() -> dict:
    return {
        "security_controls": {
            "AC-2": {
                "implementation_statement": "Access control policy is implemented for all users.",
            }
        },
        "evidence": {},
    }


def test_derive_chunk_id_is_stable() -> None:
    digest_a = derive_chunk_id(
        source_sha256="a" * 64,
        start_offset=0,
        end_offset=10,
        text="0123456789",
    )
    digest_b = derive_chunk_id(
        source_sha256="a" * 64,
        start_offset=0,
        end_offset=10,
        text="0123456789",
    )
    assert digest_a == digest_b


def test_validate_evidence_citation_accepts_matching_offsets() -> None:
    provenance = {
        "/security_controls/AC-2/implementation_statement": {
            "source_artifact_id": ARTIFACT_ID,
            "source_sha256": "b" * 64,
        }
    }
    sources = build_sealed_citable_sources(
        sealed_document=_document(),
        field_provenance=provenance,
    )
    source = sources[ARTIFACT_ID]
    citation = build_evidence_citation(source=source, start_offset=0, end_offset=20)
    validate_citations(citations=[citation], sources=sources)


def test_validate_evidence_citation_rejects_out_of_bounds_offsets() -> None:
    provenance = {
        "/security_controls/AC-2/implementation_statement": {
            "source_artifact_id": ARTIFACT_ID,
            "source_sha256": "b" * 64,
        }
    }
    sources = build_sealed_citable_sources(
        sealed_document=_document(),
        field_provenance=provenance,
    )
    source = sources[ARTIFACT_ID]
    citation = build_evidence_citation(source=source, start_offset=0, end_offset=500)
    with pytest.raises(CitationValidationError):
        validate_citations(citations=[citation], sources=sources)
