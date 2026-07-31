"""Validated structured-output contracts for SSP generation and contextual edits."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from ato_service.ssp_workspace.profile_bundles import (
    ImplementationStatementDeterministicPolicy,
    ResolvedProfile,
)

GENERATION_SCHEMA_VERSION = "1.0.0"
PATCH_SCHEMA_VERSION = "1.0.0"
MAX_SECTIONS_PER_RESPONSE = 100
MAX_CONTROLS_PER_RESPONSE = 2_000
MAX_QUESTIONS_PER_RESPONSE = 2_000
MAX_PATCHES_PER_RESPONSE = 50
MAX_CONTENT_LENGTH = 200_000
MAX_QUESTION_LENGTH = 8_000

ImplementationStatus = Literal[
    "implemented",
    "partially_implemented",
    "planned",
    "not_implemented",
    "not_applicable",
    "unknown",
]
Responsibility = Literal["system_specific", "hybrid", "inherited", "unknown"]
OwnerType = Literal["isso", "agency", "technical", "system_owner"]
TargetType = Literal["ssp_section", "control"]
ImpactLevel = Literal["low", "moderate", "high"]

_IMPLEMENTATION_STATUSES = frozenset(
    {
        "implemented",
        "partially_implemented",
        "planned",
        "not_implemented",
        "not_applicable",
        "unknown",
    }
)
_RESPONSIBILITIES = frozenset({"system_specific", "hybrid", "inherited", "unknown"})
_OWNER_TYPES = frozenset({"isso", "agency", "technical", "system_owner"})
_TARGET_TYPES = frozenset({"ssp_section", "control"})
_WHITESPACE = re.compile(r"\s+")
_OSCAL_PARAM_INSERT = re.compile(
    r"\{\{\s*insert\s*:\s*param\s*,",
    re.IGNORECASE,
)


def requirement_text_has_unresolved_organization_parameters(text: str) -> bool:
    """Return True when OSCAL requirement text still contains param insert tokens."""
    return bool(_OSCAL_PARAM_INSERT.search(text))


def contains_oscal_parameter_insert_syntax(text: str) -> bool:
    """Return True when narrative text includes OSCAL organization-parameter placeholders."""
    if not text:
        return False
    return bool(_OSCAL_PARAM_INSERT.search(text))


@dataclass(frozen=True, slots=True)
class GeneratedSection:
    section_id: str
    content: str
    supporting_fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GeneratedControl:
    control_id: str
    implementation_status: ImplementationStatus
    responsibility: Responsibility
    implementation_statement: str
    supporting_fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GeneratedQuestion:
    question_key: str
    target_type: TargetType
    target_id: str
    question: str
    owner_type: OwnerType


@dataclass(frozen=True, slots=True)
class GeneratedCategorization:
    confidentiality: ImpactLevel
    integrity: ImpactLevel
    availability: ImpactLevel
    confidentiality_rationale: str
    integrity_rationale: str
    availability_rationale: str
    supporting_fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GenerationResult:
    sections: tuple[GeneratedSection, ...]
    controls: tuple[GeneratedControl, ...]
    questions: tuple[GeneratedQuestion, ...]
    categorization: GeneratedCategorization | None = None


@dataclass(frozen=True, slots=True)
class TargetedPatch:
    target_type: TargetType
    target_id: str
    expected_revision: int
    changes: dict[str, str]
    supporting_fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PatchResult:
    patches: tuple[TargetedPatch, ...]
    questions_to_add: tuple[GeneratedQuestion, ...]
    question_ids_to_resolve: tuple[str, ...]
    change_summary: str


class GenerationContractError(ValueError):
    """Raised when model output violates a generation or patch contract."""

    def __init__(
        self,
        detail: str,
        *,
        failure_kind: str = "schema",
        repairable: bool = True,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.failure_kind = failure_kind
        self.repairable = repairable


class ProfileValidationError(ValueError):
    """Raised when workspace content violates the selected profile policy."""


@dataclass(frozen=True, slots=True)
class ControlResponsePolicy:
    implementation_statuses: frozenset[str]
    responsibilities: frozenset[str]
    question_owner_types: frozenset[str]
    evidence_required_for_agent_statement: bool


@dataclass(frozen=True, slots=True)
class SspItemPolicy:
    item_id: str
    required: bool
    value_type: Literal["string", "string_list"]
    min_length: int
    allowed_values: frozenset[str]
    standard_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SelectedProfilePolicy:
    sections: Mapping[str, SspItemPolicy]
    control_response: ControlResponsePolicy
    implementation_statement_rules: ImplementationStatementDeterministicPolicy
    parameterized_control_ids: frozenset[str] = frozenset()

    @classmethod
    def from_resolved(cls, profile: ResolvedProfile) -> SelectedProfilePolicy:
        statement_deterministic = profile.implementation_statement_policy.deterministic
        control_response_raw = getattr(profile, "control_response", None)
        if control_response_raw is not None:
            control_response = ControlResponsePolicy(
                implementation_statuses=frozenset(
                    control_response_raw.implementation_statuses
                ),
                responsibilities=frozenset(control_response_raw.responsibilities),
                question_owner_types=frozenset(
                    control_response_raw.question_owner_types
                ),
                evidence_required_for_agent_statement=(
                    control_response_raw.evidence_required_for_agent_statement
                    and statement_deterministic.require_evidence_for_agent_non_unknown_claims
                ),
            )
        else:
            control_response = ControlResponsePolicy(
                implementation_statuses=_IMPLEMENTATION_STATUSES,
                responsibilities=_RESPONSIBILITIES,
                question_owner_types=_OWNER_TYPES,
                evidence_required_for_agent_statement=(
                    statement_deterministic.require_evidence_for_agent_non_unknown_claims
                ),
            )
        sections: dict[str, SspItemPolicy] = {}
        for item in profile.ssp_required_items:
            min_length = item.min_length if item.min_length is not None else 1
            sections[item.item_id] = SspItemPolicy(
                item_id=item.item_id,
                required=getattr(item, "required", True),
                value_type=item.value_type,
                min_length=min_length,
                allowed_values=frozenset(item.allowed_values),
                standard_refs=tuple(getattr(item, "standard_refs", ()) or ()),
            )
        parameterized_control_ids = frozenset(
            control.control_id
            for control in profile.controls
            if requirement_text_has_unresolved_organization_parameters(
                control.requirement_text
            )
        )
        return cls(
            sections=sections,
            control_response=control_response,
            implementation_statement_rules=statement_deterministic,
            parameterized_control_ids=parameterized_control_ids,
        )


def parse_generation_response(
    raw_text: str,
    *,
    allowed_section_ids: set[str] | frozenset[str],
    allowed_control_ids: set[str] | frozenset[str],
    allowed_fact_ids: set[str] | frozenset[str],
    profile_policy: SelectedProfilePolicy | None = None,
    allowed_implementation_statuses: frozenset[str] | None = None,
    allowed_responsibilities: frozenset[str] | None = None,
    allowed_owner_types: frozenset[str] | None = None,
    evidence_required_for_agent_statement: bool | None = None,
) -> GenerationResult:
    """Parse and validate a bounded initial-generation response."""
    payload = _parse_json_object(raw_text)
    payload.setdefault("categorization", None)
    _require_schema_version(payload, GENERATION_SCHEMA_VERSION)
    _require_exact_keys(
        payload,
        {
            "schema_version",
            "sections",
            "controls",
            "questions",
            "categorization",
        },
        context="generation response",
    )

    section_entries = _object_list(
        payload.get("sections"),
        field_name="sections",
        maximum=MAX_SECTIONS_PER_RESPONSE,
    )
    control_entries = _object_list(
        payload.get("controls"),
        field_name="controls",
        maximum=MAX_CONTROLS_PER_RESPONSE,
    )
    question_entries = _object_list(
        payload.get("questions"),
        field_name="questions",
        maximum=MAX_QUESTIONS_PER_RESPONSE,
    )
    (
        status_allowlist,
        responsibility_allowlist,
        owner_allowlist,
        agent_statement_evidence,
        section_requirements,
    ) = _resolve_enforcement_policy(
        profile_policy=profile_policy,
        allowed_implementation_statuses=allowed_implementation_statuses,
        allowed_responsibilities=allowed_responsibilities,
        allowed_owner_types=allowed_owner_types,
        evidence_required_for_agent_statement=evidence_required_for_agent_statement,
    )
    reject_oscal_parameter_insert_syntax = (
        profile_policy.implementation_statement_rules.reject_oscal_parameter_insert_syntax
        if profile_policy is not None
        else True
    )

    sections = tuple(
        _parse_section(
            entry,
            allowed_section_ids=allowed_section_ids,
            allowed_fact_ids=allowed_fact_ids,
            section_requirements=section_requirements,
        )
        for entry in section_entries
    )
    controls = tuple(
        _parse_control(
            entry,
            allowed_control_ids=allowed_control_ids,
            allowed_fact_ids=allowed_fact_ids,
            allowed_implementation_statuses=status_allowlist,
            allowed_responsibilities=responsibility_allowlist,
            evidence_required_for_agent_statement=agent_statement_evidence,
            reject_oscal_parameter_insert_syntax=reject_oscal_parameter_insert_syntax,
        )
        for entry in control_entries
    )
    questions = tuple(
        _parse_question(
            entry,
            allowed_section_ids=allowed_section_ids,
            allowed_control_ids=allowed_control_ids,
            allowed_owner_types=owner_allowlist,
        )
        for entry in question_entries
    )
    categorization = _parse_categorization(
        payload.get("categorization"),
        allowed_fact_ids=allowed_fact_ids,
    )
    _require_unique(
        [section.section_id for section in sections],
        field_name="section_id",
    )
    _require_unique(
        [control.control_id for control in controls],
        field_name="control_id",
    )
    _require_unique(
        [question.question_key for question in questions],
        field_name="question_key",
    )
    _validate_parameterized_control_questions(
        controls=controls,
        questions=questions,
        profile_policy=profile_policy,
    )
    return GenerationResult(
        sections=sections,
        controls=controls,
        questions=questions,
        categorization=categorization,
    )


def _parse_categorization(
    value: Any,
    *,
    allowed_fact_ids: set[str] | frozenset[str],
) -> GeneratedCategorization | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise GenerationContractError("categorization must be an object or null")
    _require_exact_keys(
        value,
        {
            "confidentiality",
            "integrity",
            "availability",
            "confidentiality_rationale",
            "integrity_rationale",
            "availability_rationale",
            "supporting_fact_ids",
        },
        context="categorization",
    )
    impacts = {
        key: _allowed_text(
            value.get(key),
            field_name=key,
            allowed=frozenset({"low", "moderate", "high"}),
        )
        for key in ("confidentiality", "integrity", "availability")
    }
    rationales = {
        key: _text(
            value.get(key),
            field_name=key,
            maximum=MAX_CONTENT_LENGTH,
        )
        for key in (
            "confidentiality_rationale",
            "integrity_rationale",
            "availability_rationale",
        )
    }
    if any(not rationale for rationale in rationales.values()):
        raise GenerationContractError("categorization rationales must be non-empty")
    fact_ids = _allowed_fact_ids(
        value.get("supporting_fact_ids"),
        allowed_fact_ids=allowed_fact_ids,
    )
    if not fact_ids:
        raise GenerationContractError(
            "categorization requires supporting facts",
            failure_kind="source_binding",
            repairable=False,
        )
    return GeneratedCategorization(
        confidentiality=impacts["confidentiality"],  # type: ignore[arg-type]
        integrity=impacts["integrity"],  # type: ignore[arg-type]
        availability=impacts["availability"],  # type: ignore[arg-type]
        confidentiality_rationale=rationales["confidentiality_rationale"],
        integrity_rationale=rationales["integrity_rationale"],
        availability_rationale=rationales["availability_rationale"],
        supporting_fact_ids=fact_ids,
    )


def parse_patch_response(
    raw_text: str,
    *,
    allowed_section_ids: set[str] | frozenset[str],
    allowed_control_ids: set[str] | frozenset[str],
    allowed_fact_ids: set[str] | frozenset[str],
    allowed_question_ids: set[str] | frozenset[str],
    current_revisions: dict[tuple[str, str], int],
    profile_policy: SelectedProfilePolicy | None = None,
    allowed_implementation_statuses: frozenset[str] | None = None,
    allowed_responsibilities: frozenset[str] | None = None,
    allowed_owner_types: frozenset[str] | None = None,
    evidence_required_for_agent_statement: bool | None = None,
) -> PatchResult:
    """Parse and validate a contextual-edit response without applying it."""
    payload = _parse_json_object(raw_text)
    _require_schema_version(payload, PATCH_SCHEMA_VERSION)
    _require_exact_keys(
        payload,
        {
            "schema_version",
            "patches",
            "questions_to_add",
            "question_ids_to_resolve",
            "change_summary",
        },
        context="patch response",
    )
    patch_entries = _object_list(
        payload.get("patches"),
        field_name="patches",
        maximum=MAX_PATCHES_PER_RESPONSE,
    )
    question_entries = _object_list(
        payload.get("questions_to_add"),
        field_name="questions_to_add",
        maximum=MAX_QUESTIONS_PER_RESPONSE,
    )
    resolve_ids = _string_list(
        payload.get("question_ids_to_resolve"),
        field_name="question_ids_to_resolve",
        maximum=MAX_QUESTIONS_PER_RESPONSE,
    )
    unknown_question_ids = sorted(set(resolve_ids) - set(allowed_question_ids))
    if unknown_question_ids:
        raise GenerationContractError(
            f"unknown question_ids_to_resolve: {unknown_question_ids}",
            failure_kind="allowlist",
            repairable=False,
        )
    (
        status_allowlist,
        responsibility_allowlist,
        owner_allowlist,
        agent_statement_evidence,
        section_requirements,
    ) = _resolve_enforcement_policy(
        profile_policy=profile_policy,
        allowed_implementation_statuses=allowed_implementation_statuses,
        allowed_responsibilities=allowed_responsibilities,
        allowed_owner_types=allowed_owner_types,
        evidence_required_for_agent_statement=evidence_required_for_agent_statement,
    )
    reject_oscal_parameter_insert_syntax = (
        profile_policy.implementation_statement_rules.reject_oscal_parameter_insert_syntax
        if profile_policy is not None
        else True
    )

    patches = tuple(
        _parse_patch(
            entry,
            allowed_section_ids=allowed_section_ids,
            allowed_control_ids=allowed_control_ids,
            allowed_fact_ids=allowed_fact_ids,
            current_revisions=current_revisions,
            allowed_implementation_statuses=status_allowlist,
            allowed_responsibilities=responsibility_allowlist,
            section_requirements=section_requirements,
            evidence_required_for_agent_statement=agent_statement_evidence,
            reject_oscal_parameter_insert_syntax=reject_oscal_parameter_insert_syntax,
        )
        for entry in patch_entries
    )
    questions = tuple(
        _parse_question(
            entry,
            allowed_section_ids=allowed_section_ids,
            allowed_control_ids=allowed_control_ids,
            allowed_owner_types=owner_allowlist,
        )
        for entry in question_entries
    )
    _require_unique(
        [(patch.target_type, patch.target_id) for patch in patches],
        field_name="patch target",
    )
    _require_unique(
        [question.question_key for question in questions],
        field_name="question_key",
    )
    _require_unique(resolve_ids, field_name="question_ids_to_resolve")
    change_summary = _text(
        payload.get("change_summary"),
        field_name="change_summary",
        maximum=4_000,
    )
    return PatchResult(
        patches=patches,
        questions_to_add=questions,
        question_ids_to_resolve=tuple(resolve_ids),
        change_summary=change_summary,
    )


def deterministic_question_key(
    *,
    target_type: str,
    target_id: str,
    question: str,
) -> str:
    """Return the stable deduplication key for a generated question."""
    normalized_question = _WHITESPACE.sub(" ", question.strip()).casefold()
    source = f"{target_type}\n{target_id.strip().casefold()}\n{normalized_question}"
    return "q_" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]


def validate_workspace_section_content(
    section_id: str,
    content: str,
    profile_policy: SelectedProfilePolicy,
) -> None:
    """Validate one section value against the workspace selected profile."""

    if not content.strip():
        return
    requirement = profile_policy.sections.get(section_id)
    if requirement is None:
        raise ProfileValidationError(f"unknown SSP section: {section_id}")
    try:
        _validate_section_content_against_policy(
            section_id,
            content,
            requirement,
        )
    except GenerationContractError as exc:
        raise ProfileValidationError(exc.detail) from exc


def validate_workspace_control_fields(
    *,
    implementation_status: str | None,
    responsibility: str | None,
    profile_policy: SelectedProfilePolicy,
) -> None:
    """Validate control metadata against the workspace selected profile."""

    policy = profile_policy.control_response
    status = (implementation_status or "unknown").strip()
    resp = (responsibility or "unknown").strip()
    if status not in policy.implementation_statuses:
        raise ProfileValidationError(f"implementation_status is not allowed: {status}")
    if resp not in policy.responsibilities:
        raise ProfileValidationError(f"responsibility is not allowed: {resp}")


def validate_workspace_implementation_statement(
    statement: str,
    *,
    profile_policy: SelectedProfilePolicy | None = None,
) -> None:
    """Reject OSCAL parameter placeholders in ISSO-authored control narrative."""

    reject = (
        profile_policy.implementation_statement_rules.reject_oscal_parameter_insert_syntax
        if profile_policy is not None
        else True
    )
    if not reject:
        return
    if contains_oscal_parameter_insert_syntax(statement):
        raise ProfileValidationError(
            "implementation_statement must not contain OSCAL "
            "organization-defined parameter placeholder syntax"
        )


def validate_workspace_question_owner(
    owner_type: str,
    profile_policy: SelectedProfilePolicy,
) -> None:
    if owner_type not in profile_policy.control_response.question_owner_types:
        raise ProfileValidationError(f"owner_type is not allowed: {owner_type}")


def validate_applied_patch_result(
    result: PatchResult,
    profile_policy: SelectedProfilePolicy,
    *,
    allowed_fact_ids: set[str] | frozenset[str],
) -> None:
    """Re-validate a stored patch against the workspace selected profile."""

    for question in result.questions_to_add:
        validate_workspace_question_owner(question.owner_type, profile_policy)
    control_policy = profile_policy.control_response
    for patch in result.patches:
        unknown_facts = sorted(set(patch.supporting_fact_ids) - set(allowed_fact_ids))
        if unknown_facts:
            raise ProfileValidationError(
                f"unknown supporting_fact_ids: {unknown_facts}"
            )
        if patch.target_type == "ssp_section":
            content = patch.changes.get("content", "")
            validate_workspace_section_content(
                patch.target_id,
                content,
                profile_policy,
            )
            if content.strip() and not patch.supporting_fact_ids:
                raise ProfileValidationError(
                    f"section {patch.target_id} has content without supporting facts"
                )
            continue
        status = patch.changes.get("implementation_status", "unknown")
        responsibility = patch.changes.get("responsibility", "unknown")
        statement = patch.changes.get("implementation_statement", "")
        validate_workspace_control_fields(
            implementation_status=status,
            responsibility=responsibility,
            profile_policy=profile_policy,
        )
        if "implementation_statement" in patch.changes:
            validate_workspace_implementation_statement(
                statement,
                profile_policy=profile_policy,
            )
        if (
            _agent_control_requires_facts(
                implementation_status=status,
                responsibility=responsibility,
                statement=statement,
                evidence_required=(
                    control_policy.evidence_required_for_agent_statement
                ),
            )
            and not patch.supporting_fact_ids
        ):
            raise ProfileValidationError(
                f"control {patch.target_id} requires supporting facts "
                "for agent metadata"
            )


def _parse_section(
    entry: dict[str, Any],
    *,
    allowed_section_ids: set[str] | frozenset[str],
    allowed_fact_ids: set[str] | frozenset[str],
    section_requirements: Mapping[str, SspItemPolicy] | None = None,
) -> GeneratedSection:
    _require_exact_keys(
        entry,
        {"section_id", "content", "supporting_fact_ids"},
        context="section",
    )
    section_id = _allowed_text(
        entry.get("section_id"),
        field_name="section_id",
        allowed=allowed_section_ids,
    )
    content = _text(
        entry.get("content"),
        field_name="content",
        maximum=MAX_CONTENT_LENGTH,
    )
    fact_ids = _allowed_fact_ids(
        entry.get("supporting_fact_ids"),
        allowed_fact_ids=allowed_fact_ids,
    )
    if content and not fact_ids:
        raise GenerationContractError(
            f"section {section_id} has content without supporting facts",
            failure_kind="source_binding",
            repairable=False,
        )
    if content.strip() and section_requirements is not None:
        requirement = section_requirements.get(section_id)
        if requirement is None:
            raise GenerationContractError(
                f"section {section_id} is not in the selected profile",
                failure_kind="allowlist",
                repairable=False,
            )
        _validate_section_content_against_policy(
            section_id,
            content,
            requirement,
        )
    return GeneratedSection(
        section_id=section_id,
        content=content,
        supporting_fact_ids=fact_ids,
    )


def _parse_control(
    entry: dict[str, Any],
    *,
    allowed_control_ids: set[str] | frozenset[str],
    allowed_fact_ids: set[str] | frozenset[str],
    allowed_implementation_statuses: frozenset[str] = _IMPLEMENTATION_STATUSES,
    allowed_responsibilities: frozenset[str] = _RESPONSIBILITIES,
    evidence_required_for_agent_statement: bool = True,
    reject_oscal_parameter_insert_syntax: bool = True,
) -> GeneratedControl:
    _require_exact_keys(
        entry,
        {
            "control_id",
            "implementation_status",
            "responsibility",
            "implementation_statement",
            "supporting_fact_ids",
        },
        context="control",
    )
    control_id = _allowed_text(
        entry.get("control_id"),
        field_name="control_id",
        allowed=allowed_control_ids,
    )
    implementation_status = _allowed_text(
        entry.get("implementation_status"),
        field_name="implementation_status",
        allowed=allowed_implementation_statuses,
        repairable=True,
    )
    responsibility = _allowed_text(
        entry.get("responsibility"),
        field_name="responsibility",
        allowed=allowed_responsibilities,
        repairable=True,
    )
    statement = _text(
        entry.get("implementation_statement"),
        field_name="implementation_statement",
        maximum=MAX_CONTENT_LENGTH,
    )
    if reject_oscal_parameter_insert_syntax and contains_oscal_parameter_insert_syntax(
        statement
    ):
        raise GenerationContractError(
            f"control {control_id} implementation_statement must not contain "
            "OSCAL organization-defined parameter placeholder syntax",
            failure_kind="organization_parameter",
        )
    fact_ids = _allowed_fact_ids(
        entry.get("supporting_fact_ids"),
        allowed_fact_ids=allowed_fact_ids,
    )
    if (
        _agent_control_requires_facts(
            implementation_status=implementation_status,
            responsibility=responsibility,
            statement=statement,
            evidence_required=evidence_required_for_agent_statement,
        )
        and not fact_ids
    ):
        raise GenerationContractError(
            f"control {control_id} requires supporting facts for agent metadata",
            failure_kind="source_binding",
            repairable=False,
        )
    return GeneratedControl(
        control_id=control_id,
        implementation_status=implementation_status,  # type: ignore[arg-type]
        responsibility=responsibility,  # type: ignore[arg-type]
        implementation_statement=statement,
        supporting_fact_ids=fact_ids,
    )


def _parse_question(
    entry: dict[str, Any],
    *,
    allowed_section_ids: set[str] | frozenset[str],
    allowed_control_ids: set[str] | frozenset[str],
    allowed_owner_types: frozenset[str] = _OWNER_TYPES,
) -> GeneratedQuestion:
    _require_exact_keys(
        entry,
        {"target_type", "target_id", "question", "owner_type"},
        context="question",
    )
    target_type = _allowed_text(
        entry.get("target_type"),
        field_name="target_type",
        allowed=_TARGET_TYPES,
    )
    allowed_targets = (
        allowed_section_ids if target_type == "ssp_section" else allowed_control_ids
    )
    target_id = _allowed_text(
        entry.get("target_id"),
        field_name="target_id",
        allowed=allowed_targets,
    )
    question = _text(
        entry.get("question"),
        field_name="question",
        maximum=MAX_QUESTION_LENGTH,
    )
    owner_type = _allowed_text(
        entry.get("owner_type"),
        field_name="owner_type",
        allowed=allowed_owner_types,
        repairable=True,
    )
    return GeneratedQuestion(
        question_key=deterministic_question_key(
            target_type=target_type,
            target_id=target_id,
            question=question,
        ),
        target_type=target_type,  # type: ignore[arg-type]
        target_id=target_id,
        question=question,
        owner_type=owner_type,  # type: ignore[arg-type]
    )


def _parse_patch(
    entry: dict[str, Any],
    *,
    allowed_section_ids: set[str] | frozenset[str],
    allowed_control_ids: set[str] | frozenset[str],
    allowed_fact_ids: set[str] | frozenset[str],
    current_revisions: dict[tuple[str, str], int],
    allowed_implementation_statuses: frozenset[str] = _IMPLEMENTATION_STATUSES,
    allowed_responsibilities: frozenset[str] = _RESPONSIBILITIES,
    section_requirements: Mapping[str, SspItemPolicy] | None = None,
    evidence_required_for_agent_statement: bool = True,
    reject_oscal_parameter_insert_syntax: bool = True,
) -> TargetedPatch:
    _require_exact_keys(
        entry,
        {
            "target_type",
            "target_id",
            "expected_revision",
            "changes",
            "supporting_fact_ids",
        },
        context="patch",
    )
    target_type = _allowed_text(
        entry.get("target_type"),
        field_name="target_type",
        allowed=_TARGET_TYPES,
    )
    allowed_targets = (
        allowed_section_ids if target_type == "ssp_section" else allowed_control_ids
    )
    target_id = _allowed_text(
        entry.get("target_id"),
        field_name="target_id",
        allowed=allowed_targets,
    )
    expected_revision = entry.get("expected_revision")
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 1
    ):
        raise GenerationContractError("expected_revision must be a positive integer")
    current_revision = current_revisions.get((target_type, target_id))
    if current_revision is None:
        raise GenerationContractError(
            f"patch target has no current revision: {target_type}:{target_id}",
            failure_kind="allowlist",
            repairable=False,
        )
    if expected_revision != current_revision:
        raise GenerationContractError(
            f"stale patch target: {target_type}:{target_id}",
            failure_kind="stale",
            repairable=False,
        )

    changes_raw = entry.get("changes")
    if not isinstance(changes_raw, dict) or not changes_raw:
        raise GenerationContractError("changes must be a non-empty object")
    allowed_fields = (
        {"content"}
        if target_type == "ssp_section"
        else {
            "implementation_statement",
            "implementation_status",
            "responsibility",
        }
    )
    unknown_fields = sorted(set(changes_raw) - allowed_fields)
    if unknown_fields:
        raise GenerationContractError(
            f"unsupported patch fields: {unknown_fields}",
            failure_kind="allowlist",
            repairable=False,
        )
    changes: dict[str, str] = {}
    for field_name, value in changes_raw.items():
        if field_name == "implementation_status":
            changes[field_name] = _allowed_text(
                value,
                field_name=field_name,
                allowed=allowed_implementation_statuses,
                repairable=True,
            )
        elif field_name == "responsibility":
            changes[field_name] = _allowed_text(
                value,
                field_name=field_name,
                allowed=allowed_responsibilities,
                repairable=True,
            )
        else:
            changes[field_name] = _text(
                value,
                field_name=field_name,
                maximum=MAX_CONTENT_LENGTH,
            )
    fact_ids = _allowed_fact_ids(
        entry.get("supporting_fact_ids"),
        allowed_fact_ids=allowed_fact_ids,
    )
    if target_type == "ssp_section":
        content = changes.get("content", "")
        if content.strip() and section_requirements is not None:
            requirement = section_requirements.get(target_id)
            if requirement is None:
                raise GenerationContractError(
                    f"section {target_id} is not in the selected profile",
                    failure_kind="allowlist",
                    repairable=False,
                )
            _validate_section_content_against_policy(
                target_id,
                content,
                requirement,
            )
        if content.strip() and not fact_ids:
            raise GenerationContractError(
                f"patch {target_type}:{target_id} has content without supporting facts",
                failure_kind="source_binding",
                repairable=False,
            )
    else:
        statement = changes.get("implementation_statement", "")
        if (
            reject_oscal_parameter_insert_syntax
            and contains_oscal_parameter_insert_syntax(statement)
        ):
            raise GenerationContractError(
                f"patch {target_type}:{target_id} implementation_statement must not "
                "contain OSCAL organization-defined parameter placeholder syntax",
                failure_kind="organization_parameter",
            )
        status = changes.get("implementation_status", "unknown")
        responsibility = changes.get("responsibility", "unknown")
        if (
            _agent_control_requires_facts(
                implementation_status=status,
                responsibility=responsibility,
                statement=statement,
                evidence_required=evidence_required_for_agent_statement,
            )
            and not fact_ids
        ):
            raise GenerationContractError(
                f"patch {target_type}:{target_id} requires supporting facts "
                "for agent control metadata",
                failure_kind="source_binding",
                repairable=False,
            )
    return TargetedPatch(
        target_type=target_type,  # type: ignore[arg-type]
        target_id=target_id,
        expected_revision=expected_revision,
        changes=changes,
        supporting_fact_ids=fact_ids,
    )


def _resolve_enforcement_policy(
    *,
    profile_policy: SelectedProfilePolicy | None,
    allowed_implementation_statuses: frozenset[str] | None,
    allowed_responsibilities: frozenset[str] | None,
    allowed_owner_types: frozenset[str] | None,
    evidence_required_for_agent_statement: bool | None,
) -> tuple[
    frozenset[str],
    frozenset[str],
    frozenset[str],
    bool,
    Mapping[str, SspItemPolicy] | None,
]:
    if profile_policy is not None:
        control = profile_policy.control_response
        return (
            control.implementation_statuses,
            control.responsibilities,
            control.question_owner_types,
            control.evidence_required_for_agent_statement,
            profile_policy.sections,
        )
    return (
        allowed_implementation_statuses or _IMPLEMENTATION_STATUSES,
        allowed_responsibilities or _RESPONSIBILITIES,
        allowed_owner_types or _OWNER_TYPES,
        True
        if evidence_required_for_agent_statement is None
        else evidence_required_for_agent_statement,
        None,
    )


def _validate_section_content_against_policy(
    section_id: str,
    content: str,
    requirement: SspItemPolicy,
) -> None:
    if requirement.value_type == "string_list":
        items = _normalized_string_list_items(content)
        if len(items) < requirement.min_length:
            raise GenerationContractError(
                f"section {section_id} requires at least "
                f"{requirement.min_length} list items",
                failure_kind="schema",
            )
        if requirement.allowed_values:
            invalid = sorted(set(items) - requirement.allowed_values)
            if invalid:
                raise GenerationContractError(
                    f"section {section_id} contains disallowed values: {invalid}",
                    failure_kind="schema",
                )
        return
    normalized = content.strip()
    if len(normalized) < requirement.min_length:
        raise GenerationContractError(
            f"section {section_id} requires at least "
            f"{requirement.min_length} characters",
            failure_kind="schema",
        )
    if requirement.allowed_values and normalized not in requirement.allowed_values:
        raise GenerationContractError(
            f"section {section_id} value is not allowed: {normalized}",
            failure_kind="schema",
        )


def _normalized_string_list_items(content: str) -> list[str]:
    return [
        line.strip().lstrip("-*•").strip()
        for line in content.splitlines()
        if line.strip().lstrip("-*•").strip()
    ]


def _validate_parameterized_control_questions(
    *,
    controls: tuple[GeneratedControl, ...],
    questions: tuple[GeneratedQuestion, ...],
    profile_policy: SelectedProfilePolicy | None,
) -> None:
    if profile_policy is None or not profile_policy.parameterized_control_ids:
        return
    if not profile_policy.implementation_statement_rules.require_question_for_unresolved_parameterized_controls:
        return
    # Only validate controls the model included in its response. Omitted ODP controls
    # stay empty in the workspace for later focused generation or ISSO review.
    controls_by_id = {control.control_id: control for control in controls}
    question_control_targets = {
        question.target_id
        for question in questions
        if question.target_type == "control"
    }
    missing_questions: list[str] = []
    for control_id in sorted(profile_policy.parameterized_control_ids):
        control = controls_by_id.get(control_id)
        if control is None:
            continue
        if not _parameterized_control_response_unresolved(control):
            continue
        if control_id not in question_control_targets:
            missing_questions.append(control_id)
    if missing_questions:
        raise GenerationContractError(
            "parameterized controls with unresolved implementation responses "
            f"require targeted questions: {missing_questions}",
            failure_kind="organization_parameter",
        )


def _parameterized_control_response_unresolved(control: GeneratedControl) -> bool:
    return not control.implementation_statement.strip()


def _agent_control_requires_facts(
    *,
    implementation_status: str,
    responsibility: str,
    statement: str,
    evidence_required: bool,
) -> bool:
    if not evidence_required:
        return False
    if statement.strip():
        return True
    if implementation_status != "unknown":
        return True
    if responsibility != "unknown":
        return True
    return False


def _strip_markdown_json_fence(raw_text: str) -> str:
    """Remove optional ``` / ```json wrappers common in model output."""
    text = raw_text.strip()
    if not text.startswith("```"):
        return raw_text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    stripped = "\n".join(lines).strip()
    return stripped if stripped else raw_text


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    last_exc: Exception | None = None
    for candidate in (raw_text, _strip_markdown_json_fence(raw_text)):
        try:
            payload = json.loads(candidate)
        except (TypeError, json.JSONDecodeError) as exc:
            last_exc = exc
            continue
        if not isinstance(payload, dict):
            raise GenerationContractError("response must be a JSON object")
        return payload
    raise GenerationContractError(
        "response must be strict JSON",
        failure_kind="parse",
    ) from last_exc


def _require_schema_version(document: dict[str, Any], expected: str) -> None:
    if document.get("schema_version") != expected:
        raise GenerationContractError("unsupported schema_version")


def _require_exact_keys(
    document: dict[str, Any],
    expected: set[str],
    *,
    context: str,
) -> None:
    missing = sorted(expected - set(document))
    extra = sorted(set(document) - expected)
    if missing or extra:
        raise GenerationContractError(
            f"{context} keys invalid; missing={missing}, extra={extra}"
        )


def _object_list(
    value: Any,
    *,
    field_name: str,
    maximum: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise GenerationContractError(f"{field_name} must be an array")
    if len(value) > maximum:
        raise GenerationContractError(
            f"{field_name} exceeds maximum of {maximum}",
            repairable=False,
        )
    if any(not isinstance(item, dict) for item in value):
        raise GenerationContractError(f"{field_name} must contain objects")
    return value


def _string_list(
    value: Any,
    *,
    field_name: str,
    maximum: int,
) -> list[str]:
    if not isinstance(value, list):
        raise GenerationContractError(f"{field_name} must be an array")
    if len(value) > maximum:
        raise GenerationContractError(
            f"{field_name} exceeds maximum of {maximum}",
            repairable=False,
        )
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise GenerationContractError(f"{field_name} must contain non-empty strings")
    return value


def _allowed_fact_ids(
    value: Any,
    *,
    allowed_fact_ids: set[str] | frozenset[str],
) -> tuple[str, ...]:
    fact_ids = _string_list(
        value,
        field_name="supporting_fact_ids",
        maximum=5_000,
    )
    _require_unique(fact_ids, field_name="supporting_fact_ids")
    unknown = sorted(set(fact_ids) - set(allowed_fact_ids))
    if unknown:
        raise GenerationContractError(
            f"unknown supporting_fact_ids: {unknown}",
            failure_kind="source_binding",
            repairable=False,
        )
    return tuple(fact_ids)


def _allowed_text(
    value: Any,
    *,
    field_name: str,
    allowed: set[str] | frozenset[str],
    repairable: bool = False,
) -> str:
    normalized = _text(value, field_name=field_name, maximum=500)
    if normalized not in allowed:
        raise GenerationContractError(
            f"{field_name} is not allowed: {normalized}",
            failure_kind="allowlist",
            repairable=repairable,
        )
    return normalized


def _text(
    value: Any,
    *,
    field_name: str,
    maximum: int,
) -> str:
    if not isinstance(value, str):
        raise GenerationContractError(f"{field_name} must be a string")
    if len(value) > maximum:
        raise GenerationContractError(
            f"{field_name} exceeds maximum length of {maximum}",
            repairable=False,
        )
    return value.strip()


def _require_unique(values: list[Any], *, field_name: str) -> None:
    if len(values) != len(set(values)):
        raise GenerationContractError(
            f"duplicate {field_name} values are not allowed",
            failure_kind="duplicate",
            repairable=False,
        )
