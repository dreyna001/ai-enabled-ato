"""Typed internal contract for immutable AI qualification evaluation records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

EvaluationOutcome = Literal["passed", "failed", "invalid"]
HardStopState = Literal["unresolved", "resolved"]
AdjudicatorRole = Literal["holdout_labeler", "holdout_custodian", "evaluation_operator"]


@dataclass(frozen=True, slots=True)
class EvaluationRecordValidationReport:
    """Structured validation outcome for one evaluation record document."""

    valid: bool
    evaluation_id: str | None
    outcome: EvaluationOutcome | None
    schema_errors: tuple[str, ...]
    semantic_errors: tuple[str, ...]
    digest_errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "evaluation_id": self.evaluation_id,
            "outcome": self.outcome,
            "schema_errors": list(self.schema_errors),
            "semantic_errors": list(self.semantic_errors),
            "digest_errors": list(self.digest_errors),
        }


@dataclass(frozen=True, slots=True)
class DigestVerificationTarget:
    """One digest field verified against bytes at an explicit filesystem path."""

    field_path: str
    expected_sha256: str
    source_path: str
