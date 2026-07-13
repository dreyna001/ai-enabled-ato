"""Validate proposed values against package draft target constraints."""

from __future__ import annotations

from functools import cache
import json
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from ato_service.normalize_proposal.constants import package_draft_schema_path
from ato_service.normalize_proposal.json_utils import set_json_pointer, stable_json_dumps
from ato_service.normalize_proposal.parse import ResponseValidationError
from ato_service.normalize_proposal.source_binding import is_value_supported_by_segment
from ato_service.normalize_proposal.target_catalog import TargetSpec, target_spec_for_pointer
from ato_service.normalize_proposal.types import ParsedProposal


@cache
def _draft_schema() -> dict[str, Any]:
    return json.loads(package_draft_schema_path().read_text(encoding="utf-8"))


@cache
def _draft_validator() -> Draft202012Validator:
    schema = _draft_schema()
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    validator.check_schema(schema)
    return validator


def validate_proposed_value_for_target(
    *,
    profile_id: str,
    pointer: str,
    proposed_value: Any,
    document_shell: dict[str, Any],
) -> None:
    candidate = json.loads(stable_json_dumps(document_shell))
    set_json_pointer(candidate, pointer, proposed_value)
    errors = sorted(
        _draft_validator().iter_errors(candidate),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        raise ResponseValidationError(
            failure_kind="value_support",
            detail=f"{pointer}: {errors[0].message}",
            repairable=False,
        )

    spec = target_spec_for_pointer(profile_id=profile_id, pointer=pointer)
    if spec is None:
        raise ResponseValidationError(
            failure_kind="allowlist",
            detail=f"unknown target spec: {pointer}",
            repairable=False,
        )
    _validate_against_target_spec(spec=spec, proposed_value=proposed_value)


def _validate_against_target_spec(*, spec: TargetSpec, proposed_value: Any) -> None:
    if spec.value_kind == "nullable_string":
        if proposed_value is None:
            return
        if not isinstance(proposed_value, str):
            raise ResponseValidationError(
                failure_kind="value_support",
                detail=f"{spec.pointer} requires string or null",
                repairable=False,
            )
        if spec.max_length is not None and len(proposed_value) > spec.max_length:
            raise ResponseValidationError(
                failure_kind="value_support",
                detail=f"{spec.pointer} exceeds max length",
                repairable=False,
            )
        return

    if spec.value_kind == "enum":
        if not isinstance(proposed_value, str):
            raise ResponseValidationError(
                failure_kind="value_support",
                detail=f"{spec.pointer} requires enum string",
                repairable=False,
            )
        if spec.enum_values and proposed_value not in spec.enum_values:
            raise ResponseValidationError(
                failure_kind="value_support",
                detail=f"{spec.pointer} enum value not allowed",
                repairable=False,
            )
        return

    if not isinstance(proposed_value, str):
        raise ResponseValidationError(
            failure_kind="value_support",
            detail=f"{spec.pointer} requires string value",
            repairable=False,
        )
    if spec.max_length is not None and len(proposed_value) > spec.max_length:
        raise ResponseValidationError(
            failure_kind="value_support",
            detail=f"{spec.pointer} exceeds max length",
            repairable=False,
        )


def verify_proposal_value(
    *,
    profile_id: str,
    proposal: ParsedProposal,
    segment_text: str,
    document_shell: dict[str, Any],
) -> None:
    spec = target_spec_for_pointer(profile_id=profile_id, pointer=proposal.target)
    if spec is None:
        raise ResponseValidationError(
            failure_kind="allowlist",
            detail=f"unknown target: {proposal.target}",
            repairable=False,
        )
    validate_proposed_value_for_target(
        profile_id=profile_id,
        pointer=proposal.target,
        proposed_value=proposal.proposed_value,
        document_shell=document_shell,
    )
    if not is_value_supported_by_segment(
        proposed_value=proposal.proposed_value,
        segment_text=segment_text,
        target_spec=spec,
    ):
        raise ResponseValidationError(
            failure_kind="value_support",
            detail=f"proposed value not supported by segment text for {proposal.target}",
            repairable=False,
        )
