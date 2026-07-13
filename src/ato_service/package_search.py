"""Revision-scoped search with deterministic ranking (Component G)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence


_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{1,127}")


@dataclass(frozen=True, slots=True)
class SearchHit:
    reference_id: str
    sha256: str
    excerpt: str
    score: float


def search_revision_content(
    *,
    query: str,
    sealed_document: dict[str, Any] | None,
    artifacts: Sequence[Any],
    limit: int = 25,
) -> dict[str, Any]:
    """Deterministic revision-scoped search without cross-package access."""
    normalized_query = query.strip().lower()
    if not normalized_query:
        return {"items": [], "query": query}
    tokens = [token.lower() for token in _TOKEN_PATTERN.findall(normalized_query)]
    hits: list[SearchHit] = []

    if isinstance(sealed_document, dict):
        for pointer, value in _flatten_document(sealed_document):
            text = str(value).lower()
            if all(token in text for token in tokens):
                hits.append(
                    SearchHit(
                        reference_id=pointer,
                        sha256=_stable_hash(text),
                        excerpt=text[:240],
                        score=_score(tokens=tokens, text=text),
                    )
                )

    for artifact in artifacts:
        filename = str(getattr(artifact, "display_filename", "")).lower()
        if all(token in filename for token in tokens):
            hits.append(
                SearchHit(
                    reference_id=f"artifact:{artifact.artifact_id}",
                    sha256=artifact.sha256,
                    excerpt=filename[:240],
                    score=_score(tokens=tokens, text=filename),
                )
            )

    hits.sort(key=lambda item: (-item.score, item.reference_id))
    limited = hits[: max(1, min(limit, 100))]
    return {
        "items": [
            {
                "reference_id": hit.reference_id,
                "sha256": hit.sha256,
                "excerpt": hit.excerpt,
                "score": hit.score,
            }
            for hit in limited
        ],
        "query": query,
    }


def _flatten_document(document: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    for key in sorted(document):
        value = document[key]
        pointer = f"{prefix}/{key}" if prefix else f"/{key}"
        if isinstance(value, dict):
            items.extend(_flatten_document(value, pointer))
        elif isinstance(value, list):
            for index, entry in enumerate(value):
                if isinstance(entry, (str, int, float, bool)):
                    items.append((f"{pointer}/{index}", entry))
        else:
            items.append((pointer, value))
    return items


def _score(*, tokens: list[str], text: str) -> float:
    return float(sum(text.count(token) for token in tokens))


def _stable_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()
