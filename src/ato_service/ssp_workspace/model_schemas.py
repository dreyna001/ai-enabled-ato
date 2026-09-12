"""Pydantic JSON-schema contracts for SSP model responses.

These models define the provider-facing shape only. The existing SSP parsers
remain authoritative for request-scoped identifiers, evidence grounding,
profile policy, revisions, and cross-field domain rules.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ato_service.ssp_workspace.agency_docx_contracts import (
    MAX_CODE_LENGTH,
    MAX_EXCEPTIONS,
    MAX_ISSUES,
    MAX_MESSAGE_LENGTH,
    MAX_PLACEMENTS,
    MAX_SUMMARY_LENGTH,
    SCHEMA_VERSION as AGENCY_DOCX_SCHEMA_VERSION,
)
from ato_service.ssp_workspace.generation_contracts import (
    CATEGORIZATION_PROPOSAL_SCHEMA_VERSION,
    GENERATION_SCHEMA_VERSION,
    MAX_CONTROLS_PER_RESPONSE,
    MAX_CONTENT_LENGTH,
    MAX_PATCHES_PER_RESPONSE,
    MAX_QUESTION_LENGTH,
    MAX_QUESTIONS_PER_RESPONSE,
    MAX_SECTIONS_PER_RESPONSE,
    PATCH_SCHEMA_VERSION,
)
Schema = dict[str, object]

# Kept local to avoid importing prompt modules back into this schema registry.
# These values are the versioned constants used by diagram_analysis.py and vision.py.
DIAGRAM_ANALYSIS_SCHEMA_VERSION = "1.0.0"
MAX_COMPONENTS = 100
MAX_CONFLICTS = 50
MAX_INTERCONNECTIONS = 100
VISION_SCHEMA_VERSION = "1.0.0"
MAX_EXCERPT_CHARACTERS = 4_000
MAX_FACT_TEXT_CHARACTERS = 8_000
MAX_VISION_FACTS = 200

INITIAL_GENERATION_SCHEMA_NAME = "initial_generation"
CATEGORIZATION_SCHEMA_NAME = "categorization"
PATCH_SCHEMA_NAME = "patch"
AGENCY_DOCX_MAPPING_SCHEMA_NAME = "agency_docx_mapping"
AGENCY_DOCX_REVIEW_SCHEMA_NAME = "agency_docx_review"
VISION_FACTS_SCHEMA_NAME = "vision_facts"
DIAGRAM_PROPOSAL_SCHEMA_NAME = "diagram_proposal"


class _StrictOutputModel(BaseModel):
    """Base model that closes every provider-facing object."""

    model_config = ConfigDict(extra="forbid")


FactId: TypeAlias = Annotated[str, Field(min_length=1)]
ImpactLevel: TypeAlias = Literal["low", "moderate", "high"]
ImplementationStatus: TypeAlias = Literal[
    "implemented",
    "partially_implemented",
    "planned",
    "not_implemented",
    "not_applicable",
    "unknown",
]
Responsibility: TypeAlias = Literal[
    "system_specific",
    "hybrid",
    "inherited",
    "unknown",
]
OwnerType: TypeAlias = Literal["isso", "agency", "technical", "system_owner"]
TargetType: TypeAlias = Literal["ssp_section", "control"]


class CategorizationOutput(_StrictOutputModel):
    """Grounded FIPS 199 impact proposal nested in generation responses."""

    confidentiality: ImpactLevel
    integrity: ImpactLevel
    availability: ImpactLevel
    confidentiality_rationale: Annotated[
        str,
        Field(min_length=1, max_length=MAX_CONTENT_LENGTH),
    ]
    integrity_rationale: Annotated[
        str,
        Field(min_length=1, max_length=MAX_CONTENT_LENGTH),
    ]
    availability_rationale: Annotated[
        str,
        Field(min_length=1, max_length=MAX_CONTENT_LENGTH),
    ]
    supporting_fact_ids: list[FactId] = Field(max_length=5_000)


class GenerationSectionOutput(_StrictOutputModel):
    """One profile-scoped SSP section draft."""

    section_id: Annotated[str, Field(min_length=1, max_length=500)]
    content: str = Field(max_length=MAX_CONTENT_LENGTH)
    supporting_fact_ids: list[FactId] = Field(max_length=5_000)


class GenerationControlOutput(_StrictOutputModel):
    """One profile-scoped control implementation draft."""

    control_id: Annotated[str, Field(min_length=1, max_length=500)]
    implementation_status: ImplementationStatus
    responsibility: Responsibility
    implementation_statement: str = Field(max_length=MAX_CONTENT_LENGTH)
    supporting_fact_ids: list[FactId] = Field(max_length=5_000)


class GenerationQuestionOutput(_StrictOutputModel):
    """One targeted question for a generated SSP gap."""

    target_type: TargetType
    target_id: Annotated[str, Field(min_length=1, max_length=500)]
    question: str = Field(max_length=MAX_QUESTION_LENGTH)
    owner_type: OwnerType


class InitialGenerationOutput(_StrictOutputModel):
    """Closed provider-facing contract for initial SSP generation."""

    schema_version: Literal[GENERATION_SCHEMA_VERSION]
    sections: list[GenerationSectionOutput] = Field(max_length=MAX_SECTIONS_PER_RESPONSE)
    controls: list[GenerationControlOutput] = Field(max_length=MAX_CONTROLS_PER_RESPONSE)
    questions: list[GenerationQuestionOutput] = Field(
        max_length=MAX_QUESTIONS_PER_RESPONSE
    )
    categorization: CategorizationOutput | None


class CategorizationProposalOutput(_StrictOutputModel):
    """Closed provider-facing contract for categorization-only proposals."""

    schema_version: Literal[CATEGORIZATION_PROPOSAL_SCHEMA_VERSION]
    categorization: CategorizationOutput | None


class SectionPatchChanges(_StrictOutputModel):
    """Allowed fields for a section patch; the domain parser enforces target use."""

    content: str | None = Field(
        description=(
            "Required native-output field. Use null when the section content is "
            "unchanged; use an empty string to explicitly clear it."
        ),
        max_length=MAX_CONTENT_LENGTH,
    )


class ControlPatchChanges(_StrictOutputModel):
    """Allowed fields for a control patch; the domain parser enforces target use."""

    implementation_statement: str | None = Field(
        description=(
            "Required native-output field. Use null when the statement is "
            "unchanged; use an empty string to explicitly clear it."
        ),
        max_length=MAX_CONTENT_LENGTH,
    )
    implementation_status: ImplementationStatus | None = Field(
        description=(
            "Required native-output field. Use null when the status is "
            "unchanged; do not use unknown unless the patch explicitly changes "
            "the status to unknown."
        ),
    )
    responsibility: Responsibility | None = Field(
        description=(
            "Required native-output field. Use null when responsibility is "
            "unchanged; do not use unknown unless the patch explicitly changes "
            "responsibility to unknown."
        ),
    )


class SectionPatchOutput(_StrictOutputModel):
    """A patch variant targeting an SSP section."""

    target_type: Literal["ssp_section"]
    target_id: Annotated[str, Field(min_length=1, max_length=500)]
    expected_revision: int = Field(ge=1)
    changes: SectionPatchChanges
    supporting_fact_ids: list[FactId] = Field(max_length=5_000)


class ControlPatchOutput(_StrictOutputModel):
    """A patch variant targeting a control."""

    target_type: Literal["control"]
    target_id: Annotated[str, Field(min_length=1, max_length=500)]
    expected_revision: int = Field(ge=1)
    changes: ControlPatchChanges
    supporting_fact_ids: list[FactId] = Field(max_length=5_000)


PatchEntry: TypeAlias = Annotated[
    SectionPatchOutput | ControlPatchOutput,
    Field(discriminator="target_type"),
]


class PatchOutput(_StrictOutputModel):
    """Closed provider-facing contract for contextual edit proposals."""

    schema_version: Literal[PATCH_SCHEMA_VERSION]
    patches: list[PatchEntry] = Field(max_length=MAX_PATCHES_PER_RESPONSE)
    questions_to_add: list[GenerationQuestionOutput] = Field(
        max_length=MAX_QUESTIONS_PER_RESPONSE
    )
    question_ids_to_resolve: list[FactId] = Field(
        max_length=MAX_QUESTIONS_PER_RESPONSE
    )
    change_summary: str = Field(max_length=4_000)


class AgencyDocxTextPlacementOutput(_StrictOutputModel):
    """One bounded placement from a canonical SSP source to a template locator."""

    target_locator: Annotated[str, Field(min_length=1, max_length=128)]
    source_ref: Annotated[str, Field(min_length=1, max_length=256)]
    mode: Literal["replace", "append"]


class AgencyDocxColumnMapOutput(_StrictOutputModel):
    """Closed six-column mapping for an agency control table."""

    control_id: int = Field(ge=0)
    title: int = Field(ge=0)
    implementation_status: int = Field(ge=0)
    responsibility: int = Field(ge=0)
    implementation_statement: int = Field(ge=0)
    evidence_links: int = Field(ge=0)


class AgencyDocxControlTableOutput(_StrictOutputModel):
    """Control table selection and closed six-column mapping."""

    table_index: int | None = Field(ge=0)
    column_map: AgencyDocxColumnMapOutput


class AgencyDocxExceptionOutput(_StrictOutputModel):
    """One bounded mapping exception."""

    severity: Literal["blocker", "warning"]
    code: Annotated[str, Field(min_length=1, max_length=MAX_CODE_LENGTH)]
    message: Annotated[str, Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)]


class AgencyDocxMappingOutput(_StrictOutputModel):
    """Closed provider-facing contract for agency DOCX mapping."""

    schema_version: Literal[AGENCY_DOCX_SCHEMA_VERSION]
    text_placements: list[AgencyDocxTextPlacementOutput] = Field(
        max_length=MAX_PLACEMENTS
    )
    control_table: AgencyDocxControlTableOutput
    exceptions: list[AgencyDocxExceptionOutput] = Field(max_length=MAX_EXCEPTIONS)
    summary: str = Field(max_length=MAX_SUMMARY_LENGTH)


class AgencyDocxReviewIssueOutput(_StrictOutputModel):
    """One structured issue reported during rendered agency DOCX review."""

    severity: Literal["blocker", "warning"]
    code: Annotated[str, Field(min_length=1, max_length=MAX_CODE_LENGTH)]
    message: Annotated[str, Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)]
    locator: Annotated[str, Field(min_length=1, max_length=128)] | None


class AgencyDocxReviewOutput(_StrictOutputModel):
    """Closed provider-facing contract for rendered agency DOCX review."""

    schema_version: Literal[AGENCY_DOCX_SCHEMA_VERSION]
    summary: str = Field(max_length=MAX_SUMMARY_LENGTH)
    issues: list[AgencyDocxReviewIssueOutput] = Field(max_length=MAX_ISSUES)


class VisionLocatorOutput(_StrictOutputModel):
    """Normalized image locator used by screenshot fact extraction."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def stays_within_image(self) -> VisionLocatorOutput:
        """Preserve the manual parser's normalized-box invariant."""

        if not all(
            math.isfinite(value)
            for value in (self.x, self.y, self.width, self.height)
        ):
            raise ValueError("locator values must be finite")
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("locator must stay within image bounds")
        return self


class VisionObservationOutput(_StrictOutputModel):
    """One direct visual observation before application-owned fact identity."""

    text: Annotated[str, Field(min_length=1, max_length=MAX_FACT_TEXT_CHARACTERS)]
    excerpt: Annotated[
        str,
        Field(min_length=1, max_length=MAX_EXCERPT_CHARACTERS),
    ]
    locator: VisionLocatorOutput


class VisionFactsOutput(_StrictOutputModel):
    """Closed provider-facing contract for screenshot fact extraction."""

    schema_version: Literal[VISION_SCHEMA_VERSION]
    observations: list[VisionObservationOutput] = Field(max_length=MAX_VISION_FACTS)


class DiagramComponentOutput(_StrictOutputModel):
    """One diagram component proposal."""

    component_id: str = ""
    name: Annotated[str, Field(min_length=1, max_length=255)]
    purpose: Annotated[str, Field(min_length=1, max_length=4_000)]
    placement: Literal["inside", "outside", "crossing"]
    source: Literal["diagram", "text", "both"] = "diagram"
    confidence: Literal["high", "medium", "low"] = "medium"


class DiagramInterconnectionOutput(_StrictOutputModel):
    """One inter-system connection proposal."""

    interconnection_id: str = ""
    connected_organization: Annotated[str, Field(min_length=1, max_length=255)]
    connected_system: Annotated[str, Field(min_length=1, max_length=255)]
    direction: Literal["inbound", "outbound", "bidirectional"]
    data_types: list[Annotated[str, Field(min_length=1, max_length=255)]] = Field(
        min_length=1
    )
    interface_protocol: Annotated[str, Field(min_length=1, max_length=255)]
    source: Literal["diagram", "text", "both"] = "diagram"
    confidence: Literal["high", "medium", "low"] = "medium"


class DiagramConflictOutput(_StrictOutputModel):
    """One diagram-versus-text conflict for ISSO review."""

    field: Annotated[str, Field(min_length=1, max_length=255)]
    diagram_value: Annotated[str, Field(min_length=1, max_length=4_000)]
    text_value: Annotated[str, Field(min_length=1, max_length=4_000)]
    note: Annotated[str, Field(min_length=1, max_length=4_000)]


class DiagramProposalOutput(_StrictOutputModel):
    """Closed provider-facing contract for architecture diagram proposals."""

    schema_version: Literal[DIAGRAM_ANALYSIS_SCHEMA_VERSION]
    boundary_narrative: Annotated[str, Field(min_length=20, max_length=100_000)]
    components: list[DiagramComponentOutput] = Field(max_length=MAX_COMPONENTS)
    interconnections: list[DiagramInterconnectionOutput] = Field(
        max_length=MAX_INTERCONNECTIONS
    )
    conflicts: list[DiagramConflictOutput] = Field(max_length=MAX_CONFLICTS)


_SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    INITIAL_GENERATION_SCHEMA_NAME: InitialGenerationOutput,
    CATEGORIZATION_SCHEMA_NAME: CategorizationProposalOutput,
    PATCH_SCHEMA_NAME: PatchOutput,
    AGENCY_DOCX_MAPPING_SCHEMA_NAME: AgencyDocxMappingOutput,
    AGENCY_DOCX_REVIEW_SCHEMA_NAME: AgencyDocxReviewOutput,
    VISION_FACTS_SCHEMA_NAME: VisionFactsOutput,
    DIAGRAM_PROPOSAL_SCHEMA_NAME: DiagramProposalOutput,
}


def output_schema_for(model_name: str) -> Schema:
    """Return a fresh closed JSON Schema for an active SSP model output."""

    try:
        model_type = _SCHEMA_MODELS[model_name]
    except KeyError as exc:
        supported = ", ".join(sorted(_SCHEMA_MODELS))
        raise ValueError(
            f"unknown SSP model output {model_name!r}; expected one of: {supported}"
        ) from exc
    return cast(Schema, model_type.model_json_schema())


def normalize_native_output(schema: Schema, payload: object) -> object:
    """Remove nullable sparse-patch sentinels before the domain parser runs.

    OpenAI strict output requires every object property to be present. Patch
    change fields therefore use ``null`` for "unchanged". This helper removes
    only those null-valued fields, preserving explicit empty strings and other
    values for the existing patch parser. The runtime should call it after
    validating the native response against ``schema``.
    """

    properties = schema.get("properties")
    if not isinstance(properties, dict) or "patches" not in properties:
        return payload
    if not isinstance(payload, dict):
        return payload
    patches = payload.get("patches")
    if not isinstance(patches, list):
        return payload

    normalized_patches: list[object] = []
    changed = False
    for patch in patches:
        if not isinstance(patch, dict):
            normalized_patches.append(patch)
            continue
        changes = patch.get("changes")
        if not isinstance(changes, dict):
            normalized_patches.append(patch)
            continue
        normalized_changes = {
            key: value for key, value in changes.items() if value is not None
        }
        if normalized_changes != changes:
            changed = True
            normalized_patch = dict(patch)
            normalized_patch["changes"] = normalized_changes
            normalized_patches.append(normalized_patch)
        else:
            normalized_patches.append(patch)
    if not changed:
        return payload
    normalized_payload = dict(payload)
    normalized_payload["patches"] = normalized_patches
    return normalized_payload


__all__ = [
    "AGENCY_DOCX_MAPPING_SCHEMA_NAME",
    "AGENCY_DOCX_REVIEW_SCHEMA_NAME",
    "CATEGORIZATION_SCHEMA_NAME",
    "DIAGRAM_PROPOSAL_SCHEMA_NAME",
    "INITIAL_GENERATION_SCHEMA_NAME",
    "PATCH_SCHEMA_NAME",
    "VISION_FACTS_SCHEMA_NAME",
    "AgencyDocxMappingOutput",
    "AgencyDocxColumnMapOutput",
    "AgencyDocxControlTableOutput",
    "AgencyDocxExceptionOutput",
    "AgencyDocxReviewOutput",
    "AgencyDocxReviewIssueOutput",
    "AgencyDocxTextPlacementOutput",
    "CategorizationOutput",
    "CategorizationProposalOutput",
    "ControlPatchChanges",
    "DiagramComponentOutput",
    "DiagramConflictOutput",
    "DiagramInterconnectionOutput",
    "DiagramProposalOutput",
    "GenerationControlOutput",
    "GenerationQuestionOutput",
    "GenerationSectionOutput",
    "InitialGenerationOutput",
    "PatchOutput",
    "Schema",
    "SectionPatchChanges",
    "VisionFactsOutput",
    "normalize_native_output",
    "output_schema_for",
]
