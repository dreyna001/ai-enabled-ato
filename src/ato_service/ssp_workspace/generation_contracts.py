"""Validated structured-output contracts for SSP generation and contextual edits."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

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
_RESPONSIBILITIES = frozenset(
    {"system_specific", "hybrid", "inherited", "unknown"}
)
_OWNER_TYPES = frozenset({"isso", "agency", "technical", "system_owner"})
_TARGET_TYPES = frozenset({"ssp_section", "control"})
_WHITESPACE = re.compile(r"\s+")


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


def parse_generation_response(
    raw_text: str,
    *,
    allowed_section_ids: set[str] | frozenset[str],
    allowed_control_ids: set[str] | frozenset[str],
    allowed_fact_ids: set[str] | frozenset[str],
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

    sections = tuple(
        _parse_section(
            entry,
            allowed_section_ids=allowed_section_ids,
            allowed_fact_ids=allowed_fact_ids,
        )
        for entry in section_entries
    )
    controls = tuple(
        _parse_control(
            entry,
            allowed_control_ids=allowed_control_ids,
            allowed_fact_ids=allowed_fact_ids,
        )
        for entry in control_entries
    )
    questions = tuple(
        _parse_question(
            entry,
            allowed_section_ids=allowed_section_ids,
            allowed_control_ids=allowed_control_ids,
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
        raise GenerationContractError(
            "categorization rationales must be non-empty"
        )
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

    patches = tuple(
        _parse_patch(
            entry,
            allowed_section_ids=allowed_section_ids,
            allowed_control_ids=allowed_control_ids,
            allowed_fact_ids=allowed_fact_ids,
            current_revisions=current_revisions,
        )
        for entry in patch_entries
    )
    questions = tuple(
        _parse_question(
            entry,
            allowed_section_ids=allowed_section_ids,
            allowed_control_ids=allowed_control_ids,
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


def _parse_section(
    entry: dict[str, Any],
    *,
    allowed_section_ids: set[str] | frozenset[str],
    allowed_fact_ids: set[str] | frozenset[str],
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
        allowed=_IMPLEMENTATION_STATUSES,
    )
    responsibility = _allowed_text(
        entry.get("responsibility"),
        field_name="responsibility",
        allowed=_RESPONSIBILITIES,
    )
    statement = _text(
        entry.get("implementation_statement"),
        field_name="implementation_statement",
        maximum=MAX_CONTENT_LENGTH,
    )
    fact_ids = _allowed_fact_ids(
        entry.get("supporting_fact_ids"),
        allowed_fact_ids=allowed_fact_ids,
    )
    if statement and not fact_ids:
        raise GenerationContractError(
            f"control {control_id} has a statement without supporting facts",
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
        allowed=_OWNER_TYPES,
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
        raise GenerationContractError(
            "expected_revision must be a positive integer"
        )
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
                allowed=_IMPLEMENTATION_STATUSES,
            )
        elif field_name == "responsibility":
            changes[field_name] = _allowed_text(
                value,
                field_name=field_name,
                allowed=_RESPONSIBILITIES,
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
    content_fields = {"content", "implementation_statement"} & set(changes)
    if any(changes[field_name] for field_name in content_fields) and not fact_ids:
        raise GenerationContractError(
            f"patch {target_type}:{target_id} has content without supporting facts",
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


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw_text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise GenerationContractError(
            "response must be strict JSON",
            failure_kind="parse",
        ) from exc
    if not isinstance(payload, dict):
        raise GenerationContractError("response must be a JSON object")
    return payload


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
        raise GenerationContractError(
            f"{field_name} must contain non-empty strings"
        )
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
) -> str:
    normalized = _text(value, field_name=field_name, maximum=500)
    if normalized not in allowed:
        raise GenerationContractError(
            f"{field_name} is not allowed: {normalized}",
            failure_kind="allowlist",
            repairable=False,
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
