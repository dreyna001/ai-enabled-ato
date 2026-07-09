"""Pydantic models for report and audit output schema."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AI_DISCLOSURE = (
    "AI Disclosure: This report was produced with machine assistance. All findings, "
    "summaries, and status labels are draft inference bound to the evidence provided "
    "in the package. They do not constitute an official compliance determination, "
    "risk acceptance, or authorization decision. A qualified ISSO, SCA, or assessor "
    "must review and approve before use in GRC, eMASS, or authorization packages."
)

SufficiencyStatus = Literal[
    "supported",
    "partial",
    "unsupported",
    "insufficient_evidence",
]

AuditStatus = Literal["completed", "quarantined", "failed"]


class Citation(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    evidence_id: str
    excerpt: str


class EvidenceMatrixRow(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    control_id: str
    sufficiency_status: SufficiencyStatus
    finding_summary: str
    gaps: list[str] = Field(default_factory=list)
    stale_evidence_ids: list[str] = Field(default_factory=list)
    assessor_questions: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class PreflightResult(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    score: float = Field(ge=0.0, le=1.0)
    blocked: bool
    metadata_complete: bool
    controls_non_empty: bool
    all_controls_have_evidence: bool
    no_broken_links: bool
    warnings: list[str] = Field(default_factory=list)


class PackageMetadata(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    package_id: str
    authorization_path: str
    baseline: str
    impact_level: str
    data_classification: str
    system_name: str
    authorization_boundary: str
    assessment_date: str
    control_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)


class ReportModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    summary: str
    ai_disclosure: str = Field(default=AI_DISCLOSURE)
    preflight: PreflightResult
    evidence_matrix: list[EvidenceMatrixRow]
    validation_warnings: list[str] = Field(default_factory=list)
    package_metadata: PackageMetadata


class ReportPaths(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    json_path: str
    markdown_path: str


class AuditRecord(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    package_id: str
    run_id: str
    timestamp: datetime
    runtime_profile: str
    model: str
    input_hash: str
    report_paths: ReportPaths
    llm_call_count: int = Field(ge=0)
    preflight_score: float = Field(ge=0.0, le=1.0)
    status: AuditStatus
