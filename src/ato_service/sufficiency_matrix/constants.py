"""Frozen sufficiency_matrix contract constants."""

from __future__ import annotations

import hashlib
from pathlib import Path

PROMPT_VERSION = "1.0.0"
RESPONSE_SCHEMA_VERSION = "1.0.0"
RESPONSE_SCHEMA_ID = "ato.sufficiency-matrix-response.v1"
MAX_BATCH_SIZE = 10
MAX_LLM_CALLS_PER_BATCH = 2
INSTRUCTION_OVERHEAD_TOKENS = 2048
CHARS_PER_TOKEN = 4
MINIMUM_BUNDLE_RESERVE_TOKENS = 256
MAX_REPAIR_PRIOR_RESPONSE_CHARS = 4096
MAX_EVIDENCE_EXCERPT_CHARS = 4000

PROHIBITED_TEXT_MARKERS: tuple[str, ...] = (
    "/assessor_inputs",
    "/findings/",
    "/poam_candidates",
    "authorized to operate",
    "certified compliant",
    "risk accepted",
    "assessor verification",
    "assessor validation",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def response_schema_path() -> Path:
    return _repo_root() / "docs" / "contracts" / "sufficiency-matrix-response.schema.json"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
