"""Validated, provider-neutral contracts for SSP workspace content."""

from __future__ import annotations

import hashlib
import json
import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WorkspaceState(StrEnum):
    WORKING = "working"
    ARCHIVED = "archived"


class RevisionState(StrEnum):
    WORKING = "working"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class EvidenceState(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class FactState(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class SectionState(StrEnum):
    EMPTY = "empty"
    GENERATED = "generated"
    EDITED = "edited"
    REVIEWED = "reviewed"


class ControlState(StrEnum):
    EMPTY = "empty"
    GENERATED = "generated"
    PARTIAL = "partial"
    REVIEWED = "reviewed"


class QuestionState(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    DISMISSED = "dismissed"


class PatchState(StrEnum):
    PROPOSED = "proposed"
    APPLIED = "applied"
    REJECTED = "rejected"
    STALE = "stale"


class ProfileState(StrEnum):
    INACTIVE = "inactive"
    ACTIVE = "active"
    ARCHIVED = "archived"


class Provenance(StrEnum):
    EXTRACTED = "extracted"
    AGENT_GENERATED = "agent_generated"
    ISSO_ENTERED = "isso_entered"


class StrictContract(BaseModel):
    """Base for closed boundary contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceLink(StrictContract):
    artifact_id: uuid.UUID
    locator: dict[str, Any] = Field(min_length=1)


class EvidenceArtifactContent(StrictContract):
    """Direct workspace artifact metadata independent of the legacy package flow."""

    evidence_artifact_id: uuid.UUID
    workspace_id: uuid.UUID
    source_artifact_id: uuid.UUID | None = None
    storage_key: str = Field(pattern=r"^[a-f0-9]{2}/[a-f0-9]{64}$")
    size_bytes: int = Field(ge=1)
    display_filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=255)
    detected_format: str | None = Field(default=None, min_length=1, max_length=32)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: EvidenceState
    extracted_segments: tuple[dict[str, Any], ...] = ()

    @model_validator(mode="after")
    def storage_key_matches_digest(self) -> EvidenceArtifactContent:
        if self.storage_key.split("/", maxsplit=1)[1] != self.sha256:
            raise ValueError("storage_key must contain the artifact sha256")
        if self.state is EvidenceState.PROCESSED and self.detected_format is None:
            raise ValueError("processed evidence requires detected_format")
        return self


class FactContent(StrictContract):
    key: str = Field(min_length=1, max_length=255)
    value: Any
    provenance: Provenance
    evidence: tuple[EvidenceLink, ...] = ()
    state: FactState = FactState.ACTIVE

    @model_validator(mode="after")
    def require_evidence_for_non_human_fact(self) -> FactContent:
        if self.provenance is not Provenance.ISSO_ENTERED and not self.evidence:
            raise ValueError("extracted and agent-generated facts require evidence")
        return self


class SectionContent(StrictContract):
    key: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=100_000)
    state: SectionState
    evidence: tuple[EvidenceLink, ...] = ()

    @model_validator(mode="after")
    def state_matches_content(self) -> SectionContent:
        has_content = bool(self.content.strip())
        if self.state is SectionState.EMPTY and has_content:
            raise ValueError("empty sections cannot contain content")
        if self.state is not SectionState.EMPTY and not has_content:
            raise ValueError("non-empty section states require content")
        return self


class ControlContent(StrictContract):
    control_id: str = Field(
        pattern=r"^[A-Z]{2,4}-[0-9]+(?:\([0-9]+\)|(?:\.[0-9]+)+)?$"
    )
    title: str = Field(min_length=1, max_length=500)
    implementation_status: str | None = Field(default=None, max_length=64)
    implementation_statement: str = Field(default="", max_length=100_000)
    responsibility: str | None = Field(default=None, max_length=64)
    state: ControlState
    evidence: tuple[EvidenceLink, ...] = ()
    unresolved_reason: str | None = Field(default=None, max_length=4_000)

    @model_validator(mode="after")
    def state_matches_statement(self) -> ControlContent:
        has_statement = bool(self.implementation_statement.strip())
        if self.state is ControlState.EMPTY and has_statement:
            raise ValueError("empty controls cannot contain a statement")
        if self.state in {ControlState.GENERATED, ControlState.REVIEWED} and not has_statement:
            raise ValueError("generated and reviewed controls require a statement")
        if self.state is ControlState.PARTIAL and not (
            has_statement or (self.unresolved_reason and self.unresolved_reason.strip())
        ):
            raise ValueError("partial controls require content or an unresolved reason")
        return self


class QuestionContent(StrictContract):
    question_id: uuid.UUID
    question: str = Field(min_length=1, max_length=4_000)
    target_type: Literal["fact", "ssp_section", "control"]
    target_key: str = Field(min_length=1, max_length=255)
    owner_type: Literal["agency", "technical", "isso", "system_owner", "other"]
    state: QuestionState = QuestionState.OPEN
    answer: str | None = Field(default=None, max_length=20_000)

    @model_validator(mode="after")
    def state_matches_answer(self) -> QuestionContent:
        if self.state is QuestionState.ANSWERED and not (
            self.answer and self.answer.strip()
        ):
            raise ValueError("answered questions require an answer")
        if self.state is QuestionState.OPEN and self.answer is not None:
            raise ValueError("open questions cannot contain an answer")
        return self


class RevisionContent(StrictContract):
    """Complete hashable content of one immutable workspace revision."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    facts: tuple[FactContent, ...] = ()
    sections: tuple[SectionContent, ...] = ()
    controls: tuple[ControlContent, ...] = ()
    questions: tuple[QuestionContent, ...] = ()

    @field_validator("facts")
    @classmethod
    def unique_fact_keys(cls, values: tuple[FactContent, ...]) -> tuple[FactContent, ...]:
        _require_unique((item.key for item in values), label="fact key")
        return values

    @field_validator("sections")
    @classmethod
    def unique_section_keys(
        cls, values: tuple[SectionContent, ...]
    ) -> tuple[SectionContent, ...]:
        _require_unique((item.key for item in values), label="section key")
        return values

    @field_validator("controls")
    @classmethod
    def unique_control_ids(
        cls, values: tuple[ControlContent, ...]
    ) -> tuple[ControlContent, ...]:
        _require_unique((item.control_id for item in values), label="control ID")
        return values

    @field_validator("questions")
    @classmethod
    def unique_question_ids(
        cls, values: tuple[QuestionContent, ...]
    ) -> tuple[QuestionContent, ...]:
        _require_unique(
            (str(item.question_id) for item in values),
            label="question ID",
        )
        return values


class ProfileRequirement(StrictContract):
    key: str = Field(min_length=1, max_length=255)
    value_type: Literal["string", "boolean", "number", "array", "object"]
    required: bool = True
    enum_values: tuple[str, ...] = ()
    min_length: int = Field(default=1, ge=0, le=100_000)
    evidence_required_for_agent_value: bool = True

    @model_validator(mode="after")
    def enum_applies_only_to_string(self) -> ProfileRequirement:
        if self.enum_values and self.value_type != "string":
            raise ValueError("enum_values are supported only for string requirements")
        if len(set(self.enum_values)) != len(self.enum_values):
            raise ValueError("enum_values must be unique")
        return self


def _require_unique(values: Any, *, label: str) -> None:
    items = tuple(values)
    if len(items) != len(set(items)):
        raise ValueError(f"duplicate {label}")


def revision_content_sha256(content: RevisionContent) -> str:
    """Return the stable SHA-256 digest for exact validated revision content."""

    payload = content.model_dump(mode="json")
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
