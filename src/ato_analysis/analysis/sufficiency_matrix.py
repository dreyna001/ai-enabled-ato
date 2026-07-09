"""Evidence sufficiency matrix via bounded LLM batch calls."""

from __future__ import annotations

import json
from typing import Any

from ato_analysis.config import Settings
from ato_analysis.llm.client import LLMClient
from ato_analysis.llm.prompts import MATRIX_SYSTEM, MATRIX_USER
from ato_analysis.llm.structured_output import repair_matrix_call, validate_matrix_rows
from ato_analysis.models.package_schema import PackageModel
from ato_analysis.models.report_schema import EvidenceMatrixRow

BATCH_SIZE = 10

_MATRIX_SCHEMA_HINT = """Each row in "rows" must include:
- control_id (string)
- sufficiency_status: supported | partial | unsupported | insufficient_evidence
- finding_summary (string, cite [EV-xxx] where applicable)
- gaps (string array)
- stale_evidence_ids (string array, subset of pre-computed stale IDs for linked evidence)
- assessor_questions (string array)
- citations: array of { evidence_id, excerpt } with verbatim excerpts from evidence text
For sufficiency_status "supported", citations must be non-empty.
"""


class MatrixValidationError(RuntimeError):
    """Raised when matrix LLM output fails validation after one repair attempt."""


def run_sufficiency_matrix(
    package: PackageModel,
    stale_ids: list[str],
    client: LLMClient,
    settings: Settings,
) -> list[EvidenceMatrixRow]:
    """Run batched sufficiency matrix analysis for all package controls."""
    _ = settings
    stale_set = set(stale_ids)
    evidence_by_id = {item.evidence_id: item for item in package.evidence_items}
    all_rows: list[EvidenceMatrixRow] = []

    controls = package.controls
    for batch_start in range(0, len(controls), BATCH_SIZE):
        batch = controls[batch_start : batch_start + BATCH_SIZE]
        batch_facts = [
            _build_control_fact_record(
                control,
                evidence_by_id,
                package=package,
                stale_set=stale_set,
            )
            for control in batch
        ]
        batch_facts_json = json.dumps(batch_facts, indent=2, default=str)
        stale_for_prompt = json.dumps(sorted(stale_set))

        user = MATRIX_USER.format(
            stale_ids=stale_for_prompt,
            batch_facts_json=batch_facts_json,
            schema_hint=_MATRIX_SCHEMA_HINT,
        )
        parsed = client.complete_json(
            system=MATRIX_SYSTEM,
            user=user,
            schema_hint="",
        )
        rows_raw = _extract_rows(parsed)
        validated, errors = validate_matrix_rows(rows_raw, package, stale_set)

        if errors:
            repaired = repair_matrix_call(
                client,
                batch_facts_json=batch_facts_json,
                invalid_output=parsed,
                errors=errors,
                schema_hint=_MATRIX_SCHEMA_HINT,
            )
            rows_raw = _extract_rows(repaired)
            validated, errors = validate_matrix_rows(rows_raw, package, stale_set)
            if errors:
                detail = "; ".join(errors)
                raise MatrixValidationError(
                    f"Matrix validation failed after repair for batch starting at "
                    f"index {batch_start}: {detail}"
                )

        all_rows.extend(validated)

    return all_rows


def _build_control_fact_record(
    control: Any,
    evidence_by_id: dict[str, Any],
    *,
    package: PackageModel,
    stale_set: set[str],
) -> dict[str, Any]:
    linked_evidence = []
    for evidence_id in control.linked_evidence_ids:
        item = evidence_by_id.get(evidence_id)
        if item is None:
            continue
        linked_evidence.append(
            {
                "evidence_id": item.evidence_id,
                "title": item.title,
                "source_type": item.source_type,
                "source_owner": item.source_owner,
                "collected_at": item.collected_at.isoformat(),
                "is_stale": item.evidence_id in stale_set,
                "text": item.text,
            }
        )
    return {
        "control_id": control.control_id,
        "control_title": control.control_title,
        "control_requirement": control.control_requirement,
        "implementation_statement": control.implementation_statement,
        "package_context": {
            "assessment_date": package.assessment_date.isoformat(),
            "freshness_threshold_days": package.freshness_threshold_days,
        },
        "linked_evidence": linked_evidence,
    }


def _extract_rows(parsed: dict[str, Any]) -> list[Any]:
    rows = parsed.get("rows")
    if not isinstance(rows, list):
        raise MatrixValidationError(
            'Matrix LLM output must contain a "rows" array'
        )
    return rows
