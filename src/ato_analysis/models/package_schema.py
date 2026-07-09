"""Pydantic models for canonical evidence package schema."""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONTROL_ID_PATTERN = re.compile(r"^[A-Z]{2,3}-\d+(\(\d+\))?$")

AuthorizationPath = Literal["fisma_agency"]
Baseline = Literal["NIST-SP-800-53-R5"]


class ControlModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    control_id: str
    control_title: str
    control_requirement: str
    implementation_statement: str
    linked_evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("control_id")
    @classmethod
    def validate_control_id(cls, value: str) -> str:
        if not CONTROL_ID_PATTERN.match(value):
            raise ValueError(
                "control_id must match pattern ^[A-Z]{2,3}-\\d+(\\(\\d+\\))?$ "
                f"(e.g. AC-2, AC-2(1)); got {value!r}"
            )
        return value


class EvidenceModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    evidence_id: str
    title: str
    source_type: str
    source_owner: str
    collected_at: date
    text: str


class PackageModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    package_id: str
    authorization_path: AuthorizationPath
    baseline: Baseline
    impact_level: str
    data_classification: str
    system_name: str
    authorization_boundary: str
    assessment_date: date
    controls: list[ControlModel]
    evidence_items: list[EvidenceModel]
    freshness_threshold_days: int = Field(default=365, ge=1)
