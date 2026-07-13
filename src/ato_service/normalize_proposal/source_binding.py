"""Source binding and segment evidence verification."""

from __future__ import annotations

import json
import re
from json.decoder import JSONDecodeError
from typing import Any

from ato_service.extraction.errors import ExtractionError
from ato_service.extraction.safety_json import parse_json_strict
from ato_service.normalize_proposal.target_catalog import TargetSpec
from ato_service.normalize_proposal.types import ParsedProposal, SegmentFact

_WHITESPACE_RE = re.compile(r"\s+")

_INSTRUCTION_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*ignore\s+(all\s+)?(previous|prior)\s+instructions?\b", re.IGNORECASE),
    re.compile(r"^\s*disregard\s+(all\s+)?(previous|prior)\s+instructions?\b", re.IGNORECASE),
    re.compile(r"^\s*forget\s+(all\s+)?(previous|prior)\s+instructions?\b", re.IGNORECASE),
    re.compile(r"^\s*you\s+are\s+now\s+(a|an)\s+", re.IGNORECASE),
    re.compile(r"^\s*<\s*/?\s*(system|assistant|developer)\s*>", re.IGNORECASE),
    re.compile(
        r"^\s*(system|assistant|developer)\s*:\s*you\s+(must|should|will)\s+",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*do\s+not\s+follow\s+(the\s+)?(above|prior|previous)\b", re.IGNORECASE),
)


def normalize_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value.strip()).casefold()


def _is_instruction_line(line: str) -> bool:
    return any(pattern.search(line) for pattern in _INSTRUCTION_LINE_PATTERNS)


def _evidence_text(segment_text: str) -> str:
    lines: list[str] = []
    for line in segment_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _is_instruction_line(stripped):
            continue
        lines.append(stripped)
    return "\n".join(lines)


def verify_proposal_source_binding(
    *,
    proposal: ParsedProposal,
    segment: SegmentFact,
) -> bool:
    return proposal.source_locator == segment.locator


def is_value_supported_by_segment(
    *,
    proposed_value: Any,
    segment_text: str,
    target_spec: TargetSpec,
) -> bool:
    """Return whether proposed value is directly supported by segment text."""
    evidence = _evidence_text(segment_text)
    if not evidence:
        return False

    if target_spec.value_kind == "nullable_string" and proposed_value is None:
        normalized = normalize_text(evidence)
        return normalized in {"", "null", "none", "n/a"}

    if target_spec.value_kind == "enum":
        if not isinstance(proposed_value, str):
            return False
        if target_spec.enum_values and proposed_value not in target_spec.enum_values:
            return False
        normalized = normalize_text(evidence)
        token = normalize_text(proposed_value)
        return token in normalized

    if isinstance(proposed_value, str):
        if target_spec.max_length is not None and len(proposed_value) > target_spec.max_length:
            return False
        proposed_norm = normalize_text(proposed_value)
        segment_norm = normalize_text(evidence)
        if not proposed_norm:
            return False
        return proposed_norm in segment_norm

    if isinstance(proposed_value, (bool, int, float)) or proposed_value is None:
        try:
            parsed = parse_json_strict(evidence.strip())
        except (ExtractionError, JSONDecodeError, ValueError):
            literal = json.dumps(proposed_value, ensure_ascii=False, separators=(",", ":"))
            return normalize_text(literal) in normalize_text(evidence)
        return parsed == proposed_value

    if isinstance(proposed_value, (dict, list)):
        try:
            parsed = parse_json_strict(evidence.strip())
        except (ExtractionError, JSONDecodeError, ValueError):
            return False
        return parsed == proposed_value

    return False
