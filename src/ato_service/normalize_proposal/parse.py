"""Response parsing and contract validation for normalize_proposal."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from functools import cache
from typing import Sequence

from jsonschema import Draft202012Validator

from ato_service.normalize_proposal.constants import (
    MAX_PROPOSALS,
    RESPONSE_SCHEMA_VERSION,
    response_schema_path,
)
from ato_service.normalize_proposal.json_utils import NormalizeJsonError, parse_response_json
from ato_service.normalize_proposal.target_catalog import (
    is_prohibited_target,
    is_target_allowed,
)
from ato_service.normalize_proposal.types import ArtifactFacts, ParsedProposal, ParsedResponse


@dataclass(frozen=True, slots=True)
class ResponseValidationError(Exception):
    failure_kind: str
    detail: str
    repairable: bool

    def __str__(self) -> str:
        return self.detail


@cache
def _response_validator() -> Draft202012Validator:
    schema = json.loads(response_schema_path().read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    validator.check_schema(schema)
    return validator


def validate_and_parse_response(
    *,
    raw_text: str,
    profile_id: str,
    empty_targets: Sequence[str],
    artifacts: Sequence[ArtifactFacts],
) -> ParsedResponse:
    """Parse strict JSON and validate the normalize_proposal response contract."""
    try:
        payload = parse_response_json(raw_text)
    except NormalizeJsonError as exc:
        raise ResponseValidationError(
            failure_kind="parse",
            detail=str(exc),
            repairable=True,
        ) from exc

    if not isinstance(payload, dict):
        raise ResponseValidationError(
            failure_kind="schema",
            detail="response must be a JSON object",
            repairable=True,
        )

    schema_errors = sorted(
        _response_validator().iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if schema_errors:
        raise ResponseValidationError(
            failure_kind="schema",
            detail=schema_errors[0].message,
            repairable=True,
        )

    if payload.get("schema_version") != RESPONSE_SCHEMA_VERSION:
        raise ResponseValidationError(
            failure_kind="schema",
            detail="unsupported schema_version",
            repairable=True,
        )

    proposals_raw = payload.get("proposals")
    if not isinstance(proposals_raw, list):
        raise ResponseValidationError(
            failure_kind="schema",
            detail="proposals must be an array",
            repairable=True,
        )
    if len(proposals_raw) > MAX_PROPOSALS:
        raise ResponseValidationError(
            failure_kind="schema",
            detail=f"proposals exceed max of {MAX_PROPOSALS}",
            repairable=False,
        )

    empty_target_set = set(empty_targets)
    seen_targets: set[str] = set()
    parsed: list[ParsedProposal] = []
    artifact_lookup = {artifact.artifact_id: artifact for artifact in artifacts}
    segment_lookup = {
        (artifact.artifact_id, segment.segment_index): segment
        for artifact in artifacts
        for segment in artifact.segments
    }

    for index, entry in enumerate(proposals_raw):
        if not isinstance(entry, dict):
            raise ResponseValidationError(
                failure_kind="schema",
                detail=f"proposal {index} must be an object",
                repairable=True,
            )

        target = entry.get("target_pointer")
        if not isinstance(target, str) or not target:
            raise ResponseValidationError(
                failure_kind="schema",
                detail=f"proposal {index} missing target_pointer",
                repairable=True,
            )
        if is_prohibited_target(target):
            raise ResponseValidationError(
                failure_kind="prohibited_prefix",
                detail=f"prohibited target {target}",
                repairable=False,
            )
        if not is_target_allowed(profile_id=profile_id, pointer=target):
            raise ResponseValidationError(
                failure_kind="allowlist",
                detail=f"target not in catalog: {target}",
                repairable=False,
            )
        if target not in empty_target_set:
            raise ResponseValidationError(
                failure_kind="allowlist",
                detail=f"target not empty or not eligible: {target}",
                repairable=False,
            )
        if target in seen_targets:
            raise ResponseValidationError(
                failure_kind="duplicate_target",
                detail=f"duplicate target in response: {target}",
                repairable=False,
            )
        seen_targets.add(target)

        artifact_raw = entry.get("source_artifact_id")
        if not isinstance(artifact_raw, str):
            raise ResponseValidationError(
                failure_kind="source_binding",
                detail=f"proposal {index} missing source_artifact_id",
                repairable=False,
            )
        try:
            artifact_id = uuid.UUID(artifact_raw)
        except ValueError as exc:
            raise ResponseValidationError(
                failure_kind="source_binding",
                detail=f"invalid source_artifact_id: {artifact_raw}",
                repairable=False,
            ) from exc

        segment_index = entry.get("segment_index")
        if isinstance(segment_index, bool) or not isinstance(segment_index, int):
            raise ResponseValidationError(
                failure_kind="source_binding",
                detail=f"proposal {index} missing segment_index",
                repairable=False,
            )

        artifact = artifact_lookup.get(artifact_id)
        segment = segment_lookup.get((artifact_id, segment_index))
        if artifact is None or segment is None:
            raise ResponseValidationError(
                failure_kind="source_binding",
                detail=(
                    f"unknown artifact or segment for proposal {index}: "
                    f"{artifact_raw}:{segment_index}"
                ),
                repairable=False,
            )

        if "proposed_value" not in entry:
            raise ResponseValidationError(
                failure_kind="schema",
                detail=f"proposal {index} missing proposed_value",
                repairable=True,
            )

        parsed.append(
            ParsedProposal(
                target=target,
                proposed_value=entry["proposed_value"],
                source_artifact_id=artifact_id,
                segment_index=segment_index,
                source_sha256=artifact.sha256,
                source_locator=segment.locator,
            )
        )

    return ParsedResponse(proposals=tuple(parsed))
