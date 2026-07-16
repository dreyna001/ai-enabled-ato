"""Frozen normalize_proposal contract constants."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ato_service.project_root import contract_path

PROMPT_VERSION = "1.0.0"
RESPONSE_SCHEMA_VERSION = "1.0.0"
MAX_PROPOSALS = 64
MAX_LLM_CALLS = 2
INSTRUCTION_OVERHEAD_TOKENS = 2048
CHARS_PER_TOKEN = 4
MINIMUM_BUNDLE_RESERVE_TOKENS = 256
MAX_SEGMENT_EXCERPT_CHARS = 4000
MAX_REPAIR_PRIOR_RESPONSE_CHARS = 4096

PROHIBITED_TARGET_PREFIXES: tuple[str, ...] = (
    "/assessor_inputs",
    "/findings",
    "/poam_candidates",
    "/fedramp_20x/independent_assessment",
    "/fedramp_rev5_transition/sar",
    "/package/profile_id",
)

DETERMINISTIC_EXTRACTION_METHODS: frozenset[str] = frozenset(
    {"deterministic", "text", "vision"}
)

def response_schema_path() -> Path:
    return contract_path("normalize-proposal-response.schema.json")


def package_draft_schema_path() -> Path:
    return contract_path("package-draft-document.schema.json")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
