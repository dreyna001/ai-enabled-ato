"""Closed JSON contracts for agency template DOCX mapping and review."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

SCHEMA_VERSION = "1.0.0"
MAX_PLACEMENTS = 500
MAX_ISSUES = 200
MAX_EXCEPTIONS = 100
MAX_SUMMARY_LENGTH = 8_000
MAX_MESSAGE_LENGTH = 4_000
MAX_CODE_LENGTH = 128
MAX_MODEL_RESPONSE_CHARACTERS = 2_000_000
MAX_PROMPT_TEXT_CHARACTERS = 200_000

PlacementMode = Literal["replace", "append"]
IssueSeverity = Literal["blocker", "warning"]

_SYSTEM_SOURCE_REFS = frozenset(
    {
        "system.display_name",
        "system.external_system_id",
        "profile.profile_id",
        "profile.version",
        "profile.impact_level",
    }
)
_PLACEMENT_MODES = frozenset({"replace", "append"})
_ISSUE_SEVERITIES = frozenset({"blocker", "warning"})
_CONTROL_TABLE_COLUMNS = (
    "control_id",
    "title",
    "implementation_status",
    "responsibility",
    "implementation_statement",
    "evidence_links",
)
_LOCATOR_PARAGRAPH = re.compile(r"^paragraph:(\d+)$")
_LOCATOR_CELL = re.compile(r"^table:(\d+):cell:(\d+):(\d+)$")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class OutlineParagraph:
    locator: str
    text: str


@dataclass(frozen=True, slots=True)
class OutlineCell:
    locator: str
    text: str


@dataclass(frozen=True, slots=True)
class TemplateOutline:
    """Immutable bounded view of one agency template."""

    paragraphs: tuple[OutlineParagraph, ...]
    cells: tuple[OutlineCell, ...]

    @property
    def locators(self) -> frozenset[str]:
        return frozenset(
            [entry.locator for entry in self.paragraphs]
            + [entry.locator for entry in self.cells]
        )

    @property
    def table_count(self) -> int:
        if not self.cells:
            return 0
        return max(parse_cell_locator(cell.locator)[0] for cell in self.cells) + 1

    def table_column_count(self, table_index: int) -> int:
        column_indices = [
            parse_cell_locator(cell.locator)[2]
            for cell in self.cells
            if parse_cell_locator(cell.locator)[0] == table_index
        ]
        if not column_indices:
            return 0
        return max(column_indices) + 1


@dataclass(frozen=True, slots=True)
class TextPlacement:
    target_locator: str
    source_ref: str
    mode: PlacementMode


@dataclass(frozen=True, slots=True)
class ControlTablePlan:
    table_index: int | None
    column_map: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class MappingException:
    severity: IssueSeverity
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class MappingPlan:
    text_placements: tuple[TextPlacement, ...]
    control_table: ControlTablePlan
    exceptions: tuple[MappingException, ...]
    summary: str


@dataclass(frozen=True, slots=True)
class ReviewIssue:
    severity: IssueSeverity
    code: str
    message: str
    locator: str | None


@dataclass(frozen=True, slots=True)
class ReviewFacts:
    section_count: int
    control_count: int
    plan_exception_count: int
    rendered_paragraph_count: int
    rendered_cell_count: int
    rendered_table_count: int


@dataclass(frozen=True, slots=True)
class ReviewResult:
    summary: str
    issues: tuple[ReviewIssue, ...]
    facts: ReviewFacts


class AgencyDocxContractError(ValueError):
    """Raised when model output violates an agency DOCX contract."""

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


def allowed_source_refs(section_ids: set[str] | frozenset[str]) -> frozenset[str]:
    dynamic = {f"section:{section_id}" for section_id in section_ids}
    return _SYSTEM_SOURCE_REFS | dynamic


def parse_mapping_plan(
    raw_text: str,
    *,
    outline: TemplateOutline,
    allowed_section_ids: set[str] | frozenset[str],
) -> MappingPlan:
    """Parse and validate one bounded mapping plan response."""
    payload = _parse_json_object(raw_text)
    _require_schema_version(payload, SCHEMA_VERSION)
    _require_exact_keys(
        payload,
        {
            "schema_version",
            "text_placements",
            "control_table",
            "exceptions",
            "summary",
        },
        context="mapping plan",
    )

    allowed_refs = allowed_source_refs(allowed_section_ids)
    allowed_locators = outline.locators
    placement_entries = _object_list(
        payload.get("text_placements"),
        field_name="text_placements",
        maximum=MAX_PLACEMENTS,
    )
    placements: list[TextPlacement] = []
    seen_targets: set[str] = set()
    for index, entry in enumerate(placement_entries):
        _require_exact_keys(
            entry,
            {"target_locator", "source_ref", "mode"},
            context=f"text_placements[{index}]",
        )
        target = _bounded_text(
            entry.get("target_locator"),
            field_name="target_locator",
            maximum=128,
        )
        source_ref = _bounded_text(
            entry.get("source_ref"),
            field_name="source_ref",
            maximum=256,
        )
        mode = entry.get("mode")
        if mode not in _PLACEMENT_MODES:
            raise AgencyDocxContractError("placement mode is not allowed")
        if target not in allowed_locators:
            raise AgencyDocxContractError(
                f"target_locator is not in template outline: {target}",
                failure_kind="allowlist",
            )
        if source_ref not in allowed_refs:
            raise AgencyDocxContractError(
                f"source_ref is not allowlisted: {source_ref}",
                failure_kind="allowlist",
            )
        if target in seen_targets:
            raise AgencyDocxContractError(
                f"duplicate target_locator: {target}",
                failure_kind="duplicate",
                repairable=False,
            )
        seen_targets.add(target)
        placements.append(
            TextPlacement(
                target_locator=target,
                source_ref=source_ref,
                mode=mode,  # type: ignore[arg-type]
            )
        )

    control_table_raw = payload.get("control_table")
    if not isinstance(control_table_raw, dict):
        raise AgencyDocxContractError("control_table must be an object")
    _require_exact_keys(
        control_table_raw,
        {"table_index", "column_map"},
        context="control_table",
    )
    table_index_value = control_table_raw.get("table_index")
    if table_index_value is None:
        table_index: int | None = None
    else:
        if not isinstance(table_index_value, int) or isinstance(table_index_value, bool):
            raise AgencyDocxContractError("control_table.table_index must be integer or null")
        if table_index_value < 0:
            raise AgencyDocxContractError("control_table.table_index must be non-negative")
        if table_index_value >= outline.table_count:
            raise AgencyDocxContractError(
                "control_table.table_index out of bounds",
                failure_kind="allowlist",
            )
        table_index = table_index_value

    column_map = _parse_control_table_column_map(
        control_table_raw.get("column_map"),
        outline=outline,
        table_index=table_index,
    )

    exception_entries = _object_list(
        payload.get("exceptions"),
        field_name="exceptions",
        maximum=MAX_EXCEPTIONS,
    )
    exceptions = tuple(
        _parse_mapping_exception(entry, index=index)
        for index, entry in enumerate(exception_entries)
    )
    summary = _bounded_text(
        payload.get("summary"),
        field_name="summary",
        maximum=MAX_SUMMARY_LENGTH,
        allow_empty=True,
    )
    return MappingPlan(
        text_placements=tuple(placements),
        control_table=ControlTablePlan(table_index=table_index, column_map=column_map),
        exceptions=exceptions,
        summary=summary,
    )


def parse_review_response(raw_text: str) -> tuple[str, tuple[ReviewIssue, ...]]:
    """Parse model review output (summary and issues only)."""
    payload = _parse_json_object(raw_text)
    _require_schema_version(payload, SCHEMA_VERSION)
    _require_exact_keys(
        payload,
        {"schema_version", "summary", "issues"},
        context="review response",
    )
    summary = _bounded_text(
        payload.get("summary"),
        field_name="summary",
        maximum=MAX_SUMMARY_LENGTH,
        allow_empty=True,
    )
    issue_entries = _object_list(
        payload.get("issues"),
        field_name="issues",
        maximum=MAX_ISSUES,
    )
    issues: list[ReviewIssue] = []
    for index, entry in enumerate(issue_entries):
        allowed_keys = {"severity", "code", "message", "locator"}
        extra = sorted(set(entry) - allowed_keys)
        missing = sorted(allowed_keys - set(entry))
        if missing:
            raise AgencyDocxContractError(
                f"issues[{index}] keys invalid; missing={missing}, extra={extra}"
            )
        if extra:
            raise AgencyDocxContractError(
                f"issues[{index}] keys invalid; missing={missing}, extra={extra}"
            )
        severity = entry.get("severity")
        if severity not in _ISSUE_SEVERITIES:
            raise AgencyDocxContractError("issue severity is not allowed")
        code = _bounded_text(entry.get("code"), field_name="code", maximum=MAX_CODE_LENGTH)
        message = _bounded_text(
            entry.get("message"),
            field_name="message",
            maximum=MAX_MESSAGE_LENGTH,
        )
        locator_raw = entry.get("locator")
        locator: str | None
        if locator_raw is None:
            locator = None
        else:
            locator = _bounded_text(locator_raw, field_name="locator", maximum=128)
        issues.append(
            ReviewIssue(
                severity=severity,  # type: ignore[arg-type]
                code=code,
                message=message,
                locator=locator,
            )
        )
    return summary, tuple(issues)


def control_table_column_names() -> tuple[str, ...]:
    return _CONTROL_TABLE_COLUMNS


def canonical_append_column_map() -> dict[str, int]:
    """Sequential column indices used when appending a new control table."""
    return {column: index for index, column in enumerate(_CONTROL_TABLE_COLUMNS)}


def _parse_mapping_exception(entry: dict[str, Any], *, index: int) -> MappingException:
    _require_exact_keys(
        entry,
        {"severity", "code", "message"},
        context=f"exceptions[{index}]",
    )
    severity = entry.get("severity")
    if severity not in _ISSUE_SEVERITIES:
        raise AgencyDocxContractError("exception severity is not allowed")
    return MappingException(
        severity=severity,  # type: ignore[arg-type]
        code=_bounded_text(entry.get("code"), field_name="code", maximum=MAX_CODE_LENGTH),
        message=_bounded_text(
            entry.get("message"),
            field_name="message",
            maximum=MAX_MESSAGE_LENGTH,
        ),
    )


def _parse_control_table_column_map(
    value: Any,
    *,
    outline: TemplateOutline,
    table_index: int | None,
) -> dict[str, int]:
    if not isinstance(value, dict):
        raise AgencyDocxContractError("control_table.column_map must be an object")
    _require_exact_keys(
        value,
        set(_CONTROL_TABLE_COLUMNS),
        context="control_table.column_map",
    )
    parsed: dict[str, int] = {}
    seen_indices: set[int] = set()
    for column in _CONTROL_TABLE_COLUMNS:
        raw_index = value.get(column)
        if not isinstance(raw_index, int) or isinstance(raw_index, bool):
            raise AgencyDocxContractError(
                f"control_table.column_map.{column} must be a non-negative integer"
            )
        if raw_index < 0:
            raise AgencyDocxContractError(
                f"control_table.column_map.{column} must be a non-negative integer"
            )
        if raw_index in seen_indices:
            raise AgencyDocxContractError(
                "control_table.column_map indices must be distinct",
                failure_kind="duplicate",
            )
        seen_indices.add(raw_index)
        parsed[column] = raw_index

    if table_index is None:
        return canonical_append_column_map()

    column_count = outline.table_column_count(table_index)
    if column_count <= 0:
        raise AgencyDocxContractError(
            "control_table.table_index does not reference a template table",
            failure_kind="allowlist",
        )
    for column, column_index in parsed.items():
        if column_index >= column_count:
            raise AgencyDocxContractError(
                f"control_table.column_map.{column} out of bounds for template table",
                failure_kind="allowlist",
            )
    return parsed


def _strip_markdown_json_fence(raw_text: str) -> str:
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
            raise AgencyDocxContractError("response must be a JSON object")
        return payload
    raise AgencyDocxContractError(
        "response must be strict JSON",
        failure_kind="parse",
    ) from last_exc


def _require_schema_version(document: dict[str, Any], expected: str) -> None:
    if document.get("schema_version") != expected:
        raise AgencyDocxContractError("unsupported schema_version")


def _require_exact_keys(
    document: dict[str, Any],
    expected: set[str],
    *,
    context: str,
) -> None:
    missing = sorted(expected - set(document))
    extra = sorted(set(document) - expected)
    if missing or extra:
        raise AgencyDocxContractError(
            f"{context} keys invalid; missing={missing}, extra={extra}"
        )


def _object_list(
    value: Any,
    *,
    field_name: str,
    maximum: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise AgencyDocxContractError(f"{field_name} must be an array")
    if len(value) > maximum:
        raise AgencyDocxContractError(
            f"{field_name} exceeds maximum of {maximum}",
            repairable=False,
        )
    if any(not isinstance(item, dict) for item in value):
        raise AgencyDocxContractError(f"{field_name} must contain objects")
    return value


def _bounded_text(
    value: Any,
    *,
    field_name: str,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise AgencyDocxContractError(f"{field_name} must be a string")
    normalized = _WHITESPACE.sub(" ", value).strip()
    if not normalized and not allow_empty:
        raise AgencyDocxContractError(f"{field_name} must be non-empty")
    if len(normalized) > maximum:
        raise AgencyDocxContractError(f"{field_name} exceeds maximum of {maximum}")
    return normalized


def parse_paragraph_locator(locator: str) -> int:
    match = _LOCATOR_PARAGRAPH.match(locator)
    if not match:
        raise ValueError(f"invalid paragraph locator: {locator}")
    return int(match.group(1))


def parse_cell_locator(locator: str) -> tuple[int, int, int]:
    match = _LOCATOR_CELL.match(locator)
    if not match:
        raise ValueError(f"invalid cell locator: {locator}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))
