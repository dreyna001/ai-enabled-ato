"""SQLAlchemy 2 typed models for the P1 PostgreSQL domain foundation."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ato_service.db.base import Base
from ato_service.db import constraints as ck
from ato_service.db import enums as ev


class System(Base):
    __tablename__ = "systems"

    system_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    external_system_id: Mapped[str | None] = mapped_column(String(255))
    customer_enterprise_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_group: Mapped[str] = mapped_column(String(255), nullable=False)
    viewer_groups: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    package_revisions: Mapped[list[PackageRevision]] = relationship(
        back_populates="system",
        foreign_keys="PackageRevision.system_id",
    )
    system_context_snapshots: Mapped[list[SystemContextSnapshot]] = relationship(
        back_populates="system",
        foreign_keys="SystemContextSnapshot.system_id",
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(display_name) >= 1",
            name="ck_systems_display_name_min_length",
        ),
        CheckConstraint(
            "char_length(owner_group) >= 1",
            name="ck_systems_owner_group_min_length",
        ),
        CheckConstraint(
            "char_length(customer_enterprise_id) >= 1",
            name="ck_systems_customer_enterprise_id_min_length",
        ),
        Index("ix_systems_owner_group", "owner_group"),
        Index("ix_systems_customer_enterprise_id", "customer_enterprise_id"),
    )


class PackageRevision(Base):
    __tablename__ = "package_revisions"

    package_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True
    )
    system_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("systems.system_id", ondelete="RESTRICT"),
        nullable=False,
    )
    parent_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("package_revisions.package_revision_id", ondelete="RESTRICT"),
    )
    profile_id: Mapped[str | None] = mapped_column(String(64))
    certification_class: Mapped[str | None] = mapped_column(String(1))
    impact_level: Mapped[str | None] = mapped_column(String(16))
    data_origin: Mapped[str | None] = mapped_column(String(64))
    sensitivity: Mapped[str | None] = mapped_column(String(64))
    effective_data_labels: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    authority_manifest_id: Mapped[str] = mapped_column(String(128), nullable=False)
    content_manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    package_content_sha256: Mapped[str | None] = mapped_column(String(64))
    system_context_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "system_context_snapshots.system_context_snapshot_id",
            ondelete="RESTRICT",
        ),
    )
    revision_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    system: Mapped[System] = relationship(
        back_populates="package_revisions",
        foreign_keys=[system_id],
    )
    parent_revision: Mapped[PackageRevision | None] = relationship(
        remote_side=[package_revision_id],
        foreign_keys=[parent_revision_id],
    )
    source_artifacts: Mapped[list[SourceArtifact]] = relationship(
        back_populates="package_revision"
    )
    fact_proposals: Mapped[list[FactProposal]] = relationship(
        back_populates="package_revision"
    )
    analysis_runs: Mapped[list[AnalysisRun]] = relationship(
        back_populates="package_revision"
    )
    draft: Mapped[PackageRevisionDraft | None] = relationship(
        back_populates="package_revision",
        uselist=False,
    )
    sealed_content: Mapped[SealedPackageContent | None] = relationship(
        back_populates="package_revision",
        uselist=False,
    )
    system_context_snapshot: Mapped[SystemContextSnapshot | None] = relationship(
        back_populates="package_revisions",
        foreign_keys=[system_context_snapshot_id],
    )
    intake_work: Mapped[list["PackageRevisionIntakeWork"]] = relationship(
        back_populates="package_revision"
    )
    normalization_steps: Mapped[list["PackageNormalizationStep"]] = relationship(
        back_populates="package_revision"
    )

    __table_args__ = (
        ck.enum_check(
            "profile_id",
            ev.PROFILE_ID_VALUES,
            constraint_name="ck_package_revisions_profile_id",
            nullable=True,
        ),
        ck.enum_check(
            "certification_class",
            ev.CERTIFICATION_CLASS_VALUES,
            constraint_name="ck_package_revisions_certification_class",
            nullable=True,
        ),
        ck.enum_check(
            "impact_level",
            ev.IMPACT_LEVEL_VALUES,
            constraint_name="ck_package_revisions_impact_level",
            nullable=True,
        ),
        ck.enum_check(
            "data_origin",
            ev.DATA_ORIGIN_VALUES,
            constraint_name="ck_package_revisions_data_origin",
            nullable=True,
        ),
        ck.enum_check(
            "sensitivity",
            ev.SENSITIVITY_VALUES,
            constraint_name="ck_package_revisions_sensitivity",
            nullable=True,
        ),
        ck.enum_check(
            "status",
            ev.PACKAGE_REVISION_STATUS_VALUES,
            constraint_name="ck_package_revisions_status",
        ),
        ck.sha256_check(
            "content_manifest_sha256",
            constraint_name="ck_package_revisions_content_manifest_sha256",
            nullable=True,
        ),
        ck.sha256_check(
            "package_content_sha256",
            constraint_name="ck_package_revisions_package_content_sha256",
            nullable=True,
        ),
        CheckConstraint(
            "status <> 'ready' OR content_manifest_sha256 IS NOT NULL",
            name="ck_package_revisions_ready_requires_content_manifest_sha256",
        ),
        CheckConstraint(
            "status <> 'ready' OR ("
            "profile_id IS NOT NULL "
            "AND data_origin IS NOT NULL "
            "AND sensitivity IS NOT NULL "
            "AND jsonb_array_length(effective_data_labels) >= 2"
            ")",
            name="ck_package_revisions_ready_requires_complete_metadata",
        ),
        CheckConstraint(
            "revision_version >= 1",
            name="ck_package_revisions_revision_version_positive",
        ),
        ck.regex_check(
            "authority_manifest_id",
            ck.AUTHORITY_MANIFEST_ID_REGEX,
            constraint_name="ck_package_revisions_authority_manifest_id",
        ),
        CheckConstraint(
            "char_length(created_by) >= 1",
            name="ck_package_revisions_created_by_min_length",
        ),
        Index("ix_package_revisions_system_id", "system_id"),
        Index("ix_package_revisions_status", "status"),
        Index("ix_package_revisions_parent_revision_id", "parent_revision_id"),
        Index(
            "ix_package_revisions_system_context_snapshot_id",
            "system_context_snapshot_id",
        ),
    )


class SystemContextSnapshot(Base):
    """Immutable versioned system-context document bound to one system."""

    __tablename__ = "system_context_snapshots"

    system_context_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    system_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("systems.system_id", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    system: Mapped[System] = relationship(
        back_populates="system_context_snapshots",
        foreign_keys=[system_id],
    )
    package_revisions: Mapped[list[PackageRevision]] = relationship(
        back_populates="system_context_snapshot",
        foreign_keys="PackageRevision.system_context_snapshot_id",
    )
    sealed_package_contents: Mapped[list[SealedPackageContent]] = relationship(
        back_populates="system_context_snapshot",
        foreign_keys="SealedPackageContent.system_context_snapshot_id",
    )

    __table_args__ = (
        UniqueConstraint(
            "system_id",
            "version",
            name="uq_system_context_snapshots_system_id_version",
        ),
        ck.sha256_check(
            "content_sha256",
            constraint_name="ck_system_context_snapshots_content_sha256",
        ),
        CheckConstraint("version >= 1", name="ck_system_context_snapshots_version_positive"),
        CheckConstraint(
            "jsonb_typeof(document) = 'object'",
            name="ck_system_context_snapshots_document_object",
        ),
        CheckConstraint(
            "char_length(created_by) >= 1",
            name="ck_system_context_snapshots_created_by_min_length",
        ),
        Index("ix_system_context_snapshots_system_id", "system_id"),
    )


class PackageRevisionDraft(Base):
    """Mutable package editor document for one awaiting_confirmation revision."""

    __tablename__ = "package_revision_drafts"

    package_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("package_revisions.package_revision_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    document_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    field_provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    package_revision: Mapped[PackageRevision] = relationship(
        back_populates="draft",
        foreign_keys=[package_revision_id],
    )

    __table_args__ = (
        ck.regex_check(
            "document_schema_version",
            ck.DOCUMENT_SCHEMA_VERSION_REGEX,
            constraint_name="ck_package_revision_drafts_document_schema_version",
        ),
        CheckConstraint(
            "jsonb_typeof(document) = 'object'",
            name="ck_package_revision_drafts_document_object",
        ),
        CheckConstraint(
            "jsonb_typeof(field_provenance) = 'object'",
            name="ck_package_revision_drafts_field_provenance_object",
        ),
        CheckConstraint(
            "char_length(updated_by) >= 1",
            name="ck_package_revision_drafts_updated_by_min_length",
        ),
    )


class SealedPackageContent(Base):
    """Immutable canonical package bytes sealed at confirm."""

    __tablename__ = "sealed_package_contents"

    package_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("package_revisions.package_revision_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    document_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    field_provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    system_context_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "system_context_snapshots.system_context_snapshot_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    sealed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    sealed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    package_revision: Mapped[PackageRevision] = relationship(
        back_populates="sealed_content",
        foreign_keys=[package_revision_id],
    )
    system_context_snapshot: Mapped[SystemContextSnapshot] = relationship(
        back_populates="sealed_package_contents",
        foreign_keys=[system_context_snapshot_id],
    )

    __table_args__ = (
        ck.regex_check(
            "document_schema_version",
            ck.DOCUMENT_SCHEMA_VERSION_REGEX,
            constraint_name="ck_sealed_package_contents_document_schema_version",
        ),
        ck.sha256_check(
            "content_sha256",
            constraint_name="ck_sealed_package_contents_content_sha256",
        ),
        CheckConstraint(
            "jsonb_typeof(document) = 'object'",
            name="ck_sealed_package_contents_document_object",
        ),
        CheckConstraint(
            "jsonb_typeof(field_provenance) = 'object'",
            name="ck_sealed_package_contents_field_provenance_object",
        ),
        CheckConstraint(
            "char_length(sealed_by) >= 1",
            name="ck_sealed_package_contents_sealed_by_min_length",
        ),
    )


class SourceArtifact(Base):
    __tablename__ = "source_artifacts"

    artifact_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    package_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("package_revisions.package_revision_id", ondelete="RESTRICT"),
        nullable=False,
    )
    display_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(67), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    declared_media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    detected_media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    malware_scan_status: Mapped[str] = mapped_column(String(16), nullable=False)
    extraction_status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_date: Mapped[date | None] = mapped_column(Date)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    package_revision: Mapped[PackageRevision] = relationship(
        back_populates="source_artifacts"
    )
    fact_proposals: Mapped[list[FactProposal]] = relationship(
        back_populates="source_artifact"
    )

    __table_args__ = (
        ck.sha256_check("sha256", constraint_name="ck_source_artifacts_sha256"),
        ck.regex_check(
            "storage_key",
            ck.STORAGE_KEY_REGEX,
            constraint_name="ck_source_artifacts_storage_key",
        ),
        ck.enum_check(
            "artifact_kind",
            ev.ARTIFACT_KIND_VALUES,
            constraint_name="ck_source_artifacts_artifact_kind",
        ),
        ck.enum_check(
            "malware_scan_status",
            ev.MALWARE_SCAN_STATUS_VALUES,
            constraint_name="ck_source_artifacts_malware_scan_status",
        ),
        ck.enum_check(
            "extraction_status",
            ev.EXTRACTION_STATUS_VALUES,
            constraint_name="ck_source_artifacts_extraction_status",
        ),
        CheckConstraint(
            "char_length(display_filename) >= 1",
            name="ck_source_artifacts_display_filename_min_length",
        ),
        CheckConstraint(
            "size_bytes >= 1 AND size_bytes <= 104857600",
            name="ck_source_artifacts_size_bytes_range",
        ),
        UniqueConstraint(
            "package_revision_id",
            "sha256",
            name="uq_source_artifacts_revision_sha256",
        ),
        Index("ix_source_artifacts_package_revision_id", "package_revision_id"),
        Index("ix_source_artifacts_sha256", "sha256"),
    )


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    package_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("package_revisions.package_revision_id", ondelete="RESTRICT"),
        nullable=False,
    )
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("analysis_runs.run_id", ondelete="RESTRICT"),
    )
    run_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    authority_manifest_id: Mapped[str] = mapped_column(String(128), nullable=False)
    analysis_profile_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_bundle_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model_profile: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    llm_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assessment_item_ids: Mapped[list[str] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_retryable: Mapped[bool | None] = mapped_column(Boolean)

    package_revision: Mapped[PackageRevision] = relationship(back_populates="analysis_runs")
    parent_run: Mapped[AnalysisRun | None] = relationship(
        remote_side=[run_id],
        foreign_keys=[parent_run_id],
    )
    run_steps: Mapped[list[RunStep]] = relationship(back_populates="analysis_run")
    jobs: Mapped[list[Job]] = relationship(back_populates="analysis_run")
    matrix_rows: Mapped[list[MatrixRow]] = relationship(
        back_populates="analysis_run",
        foreign_keys="[MatrixRow.run_id]",
    )

    __table_args__ = (
        ck.enum_check(
            "run_type",
            ev.ANALYSIS_RUN_TYPE_VALUES,
            constraint_name="ck_analysis_runs_run_type",
        ),
        ck.enum_check(
            "status",
            ev.ANALYSIS_RUN_STATUS_VALUES,
            constraint_name="ck_analysis_runs_status",
        ),
        ck.sha256_check(
            "analysis_profile_sha256",
            constraint_name="ck_analysis_runs_analysis_profile_sha256",
        ),
        ck.sha256_check(
            "config_fingerprint",
            constraint_name="ck_analysis_runs_config_fingerprint",
        ),
        ck.sha256_check(
            "prompt_bundle_sha256",
            constraint_name="ck_analysis_runs_prompt_bundle_sha256",
        ),
        ck.sha256_check(
            "artifact_manifest_sha256",
            constraint_name="ck_analysis_runs_artifact_manifest_sha256",
            nullable=True,
        ),
        ck.regex_check(
            "authority_manifest_id",
            ck.AUTHORITY_MANIFEST_ID_REGEX,
            constraint_name="ck_analysis_runs_authority_manifest_id",
        ),
        ck.regex_check(
            "error_code",
            ck.ERROR_CODE_REGEX,
            constraint_name="ck_analysis_runs_error_code",
            nullable=True,
        ),
        CheckConstraint(
            "char_length(requested_by) >= 1",
            name="ck_analysis_runs_requested_by_min_length",
        ),
        CheckConstraint(
            "char_length(model_profile) >= 1",
            name="ck_analysis_runs_model_profile_min_length",
        ),
        CheckConstraint(
            "llm_call_count >= 0 AND llm_call_count <= 120",
            name="ck_analysis_runs_llm_call_count_range",
        ),
        CheckConstraint(
            "status <> 'policy_blocked' OR llm_call_count = 0",
            name="ck_analysis_runs_policy_blocked_requires_zero_llm_calls",
        ),
        Index("ix_analysis_runs_package_revision_id", "package_revision_id"),
        Index("ix_analysis_runs_status", "status"),
        Index("ix_analysis_runs_parent_run_id", "parent_run_id"),
    )


class RunStep(Base):
    """Persisted model-step records keyed by ``(run_id, step_key)`` for completion idempotency."""

    __tablename__ = "run_steps"

    step_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("analysis_runs.run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    step_key: Mapped[str] = mapped_column(String(64), nullable=False)
    step_type: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_id: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    fact_bundle_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint_profile: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint_host: Mapped[str] = mapped_column(String(253), nullable=False)
    model_requested: Mapped[str] = mapped_column(String(255), nullable=False)
    model_reported: Mapped[str] = mapped_column(String(255), nullable=False)
    temperature: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False)
    input_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    output_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    timeout_seconds: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_request_id: Mapped[str] = mapped_column(String(255), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    response_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    analysis_run: Mapped[AnalysisRun] = relationship(back_populates="run_steps")
    fact_proposals: Mapped[list[FactProposal]] = relationship(
        back_populates="model_step"
    )

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "step_key",
            name="uq_run_steps_run_id_step_key",
        ),
        ck.enum_check(
            "step_type",
            ev.RUN_STEP_TYPE_VALUES,
            constraint_name="ck_run_steps_step_type",
        ),
        ck.enum_check(
            "endpoint_profile",
            ev.ENDPOINT_PROFILE_VALUES,
            constraint_name="ck_run_steps_endpoint_profile",
        ),
        ck.regex_check(
            "step_key",
            ck.STEP_KEY_REGEX,
            constraint_name="ck_run_steps_step_key",
        ),
        ck.regex_check(
            "validation_outcome",
            ck.VALIDATION_OUTCOME_REGEX,
            constraint_name="ck_run_steps_validation_outcome",
        ),
        ck.sha256_check("prompt_sha256", constraint_name="ck_run_steps_prompt_sha256"),
        ck.sha256_check(
            "fact_bundle_sha256",
            constraint_name="ck_run_steps_fact_bundle_sha256",
        ),
        ck.sha256_check(
            "response_sha256",
            constraint_name="ck_run_steps_response_sha256",
        ),
        CheckConstraint("char_length(schema_id) >= 1", name="ck_run_steps_schema_id_min_length"),
        CheckConstraint(
            "char_length(prompt_version) >= 1",
            name="ck_run_steps_prompt_version_min_length",
        ),
        CheckConstraint(
            "char_length(endpoint_host) >= 1",
            name="ck_run_steps_endpoint_host_min_length",
        ),
        CheckConstraint(
            "char_length(model_requested) >= 1",
            name="ck_run_steps_model_requested_min_length",
        ),
        CheckConstraint(
            "char_length(model_reported) >= 1",
            name="ck_run_steps_model_reported_min_length",
        ),
        CheckConstraint(
            "char_length(provider_request_id) >= 1",
            name="ck_run_steps_provider_request_id_min_length",
        ),
        CheckConstraint("temperature >= 0", name="ck_run_steps_temperature_non_negative"),
        CheckConstraint("input_limit >= 1", name="ck_run_steps_input_limit_positive"),
        CheckConstraint("output_limit >= 1", name="ck_run_steps_output_limit_positive"),
        CheckConstraint("timeout_seconds > 0", name="ck_run_steps_timeout_seconds_positive"),
        CheckConstraint("attempt >= 1", name="ck_run_steps_attempt_positive"),
        CheckConstraint("input_tokens >= 0", name="ck_run_steps_input_tokens_non_negative"),
        CheckConstraint("output_tokens >= 0", name="ck_run_steps_output_tokens_non_negative"),
        CheckConstraint("latency_ms >= 0", name="ck_run_steps_latency_ms_non_negative"),
        Index("ix_run_steps_run_id", "run_id"),
    )


class FactProposal(Base):
    __tablename__ = "fact_proposals"

    fact_proposal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True
    )
    package_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("package_revisions.package_revision_id", ondelete="RESTRICT"),
        nullable=False,
    )
    json_pointer: Mapped[str] = mapped_column(String(2000), nullable=False)
    proposed_value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    source_artifact_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("source_artifacts.artifact_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(32), nullable=False)
    model_step_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("run_steps.step_id", ondelete="RESTRICT"),
    )
    review_status: Mapped[str] = mapped_column(String(16), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    package_revision: Mapped[PackageRevision] = relationship(
        back_populates="fact_proposals"
    )
    source_artifact: Mapped[SourceArtifact] = relationship(
        back_populates="fact_proposals"
    )
    model_step: Mapped[RunStep | None] = relationship(back_populates="fact_proposals")

    __table_args__ = (
        ck.sha256_check("source_sha256", constraint_name="ck_fact_proposals_source_sha256"),
        ck.regex_check(
            "json_pointer",
            ck.JSON_POINTER_REGEX,
            constraint_name="ck_fact_proposals_json_pointer",
        ),
        ck.enum_check(
            "extraction_method",
            ev.EXTRACTION_METHOD_VALUES,
            constraint_name="ck_fact_proposals_extraction_method",
        ),
        ck.enum_check(
            "review_status",
            ev.FACT_PROPOSAL_REVIEW_STATUS_VALUES,
            constraint_name="ck_fact_proposals_review_status",
        ),
        Index("ix_fact_proposals_package_revision_id", "package_revision_id"),
        Index("ix_fact_proposals_source_artifact_id", "source_artifact_id"),
        Index("ix_fact_proposals_review_status", "review_status"),
    )


class IdempotencyRecord(Base):
    """Replay-safe API outcomes without storing raw authentication material."""

    __tablename__ = "idempotency_records"

    idempotency_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True
    )
    principal: Mapped[str] = mapped_column(String(255), nullable=False)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    response_headers: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "principal",
            "operation",
            "idempotency_key",
            name="uq_idempotency_records_principal_operation_key",
        ),
        ck.sha256_check(
            "request_digest",
            constraint_name="ck_idempotency_records_request_digest",
        ),
        ck.regex_check(
            "idempotency_key",
            ck.IDEMPOTENCY_KEY_REGEX,
            constraint_name="ck_idempotency_records_idempotency_key",
        ),
        CheckConstraint(
            "char_length(principal) >= 1",
            name="ck_idempotency_records_principal_min_length",
        ),
        CheckConstraint(
            "char_length(operation) >= 1",
            name="ck_idempotency_records_operation_min_length",
        ),
        CheckConstraint(
            "response_status >= 100 AND response_status <= 599",
            name="ck_idempotency_records_response_status_range",
        ),
        Index("ix_idempotency_records_expires_at", "expires_at"),
    )


class AuditEvent(Base):
    """Insert-only audit trail with hash-chain fields; HMAC key remains outside the database."""

    __tablename__ = "audit_events"

    audit_event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(255), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
    )
    previous_event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        ck.enum_check(
            "actor_type",
            ev.AUDIT_ACTOR_TYPE_VALUES,
            constraint_name="ck_audit_events_actor_type",
        ),
        ck.enum_check(
            "outcome",
            ev.AUDIT_OUTCOME_VALUES,
            constraint_name="ck_audit_events_outcome",
        ),
        ck.sha256_check(
            "previous_event_hash",
            constraint_name="ck_audit_events_previous_event_hash",
        ),
        ck.sha256_check("event_hash", constraint_name="ck_audit_events_event_hash"),
        ck.regex_check(
            "action",
            ck.AUDIT_ACTION_REGEX,
            constraint_name="ck_audit_events_action",
        ),
        ck.regex_check(
            "object_type",
            ck.AUDIT_OBJECT_TYPE_REGEX,
            constraint_name="ck_audit_events_object_type",
        ),
        ck.regex_check(
            "reason_code",
            ck.ERROR_CODE_REGEX,
            constraint_name="ck_audit_events_reason_code",
            nullable=True,
        ),
        CheckConstraint(
            "char_length(actor_id) >= 1",
            name="ck_audit_events_actor_id_min_length",
        ),
        CheckConstraint(
            "char_length(object_id) >= 1",
            name="ck_audit_events_object_id_min_length",
        ),
        Index("ix_audit_events_occurred_at", "occurred_at"),
        Index("ix_audit_events_object_type_object_id", "object_type", "object_id"),
    )


class Job(Base):
    """Durable Postgres queue row for one ``(run_id, step_key)`` execution unit."""

    __tablename__ = "jobs"

    job_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("analysis_runs.run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    step_key: Mapped[str] = mapped_column(String(64), nullable=False)
    step_idempotent: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(128))

    analysis_run: Mapped[AnalysisRun] = relationship(back_populates="jobs")
    attempts: Mapped[list[JobAttempt]] = relationship(
        back_populates="job",
        foreign_keys="[JobAttempt.job_id, JobAttempt.run_id, JobAttempt.step_key]",
    )

    __table_args__ = (
        UniqueConstraint("run_id", "step_key", name="uq_jobs_run_id_step_key"),
        UniqueConstraint(
            "job_id",
            "run_id",
            "step_key",
            name="uq_jobs_job_id_run_id_step_key",
        ),
        ck.enum_check(
            "status",
            ev.JOB_STATUS_VALUES,
            constraint_name="ck_jobs_status",
        ),
        ck.regex_check(
            "step_key",
            ck.STEP_KEY_REGEX,
            constraint_name="ck_jobs_step_key",
        ),
        ck.regex_check(
            "last_error_code",
            ck.ERROR_CODE_REGEX,
            constraint_name="ck_jobs_last_error_code",
            nullable=True,
        ),
        CheckConstraint("attempt_count >= 0", name="ck_jobs_attempt_count_non_negative"),
        CheckConstraint(
            "("
            "(status = 'leased' AND lease_owner IS NOT NULL "
            "AND char_length(lease_owner) >= 1 "
            "AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL) "
            "OR (status IN ('available', 'completed', 'failed', 'reconciliation_required') "
            "AND lease_owner IS NULL AND lease_expires_at IS NULL AND heartbeat_at IS NULL)"
            ")",
            name="ck_jobs_lease_fields_match_status",
        ),
        Index("ix_jobs_run_id", "run_id"),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_status_available_at", "status", "available_at"),
        Index("ix_jobs_lease_expires_at", "lease_expires_at"),
    )


class JobAttempt(Base):
    """Child record of a claimed or completed transport attempt for a job."""

    __tablename__ = "job_attempts"

    attempt_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    step_key: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    lease_owner: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_retryable: Mapped[bool | None] = mapped_column(Boolean)

    job: Mapped[Job] = relationship(
        back_populates="attempts",
        foreign_keys=[job_id, run_id, step_key],
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "run_id", "step_key"],
            ["jobs.job_id", "jobs.run_id", "jobs.step_key"],
            ondelete="RESTRICT",
            name="fk_job_attempts_jobs_job_id_run_id_step_key",
        ),
        UniqueConstraint(
            "job_id",
            "attempt_number",
            name="uq_job_attempts_job_id_attempt_number",
        ),
        ck.enum_check(
            "status",
            ev.JOB_ATTEMPT_STATUS_VALUES,
            constraint_name="ck_job_attempts_status",
        ),
        ck.regex_check(
            "step_key",
            ck.STEP_KEY_REGEX,
            constraint_name="ck_job_attempts_step_key",
        ),
        ck.regex_check(
            "error_code",
            ck.ERROR_CODE_REGEX,
            constraint_name="ck_job_attempts_error_code",
            nullable=True,
        ),
        CheckConstraint("attempt_number >= 1", name="ck_job_attempts_attempt_number_positive"),
        CheckConstraint(
            "char_length(lease_owner) >= 1",
            name="ck_job_attempts_lease_owner_min_length",
        ),
        CheckConstraint(
            "("
            "(status = 'active' AND completed_at IS NULL "
            "AND error_code IS NULL AND error_retryable IS NULL) "
            "OR (status = 'succeeded' AND completed_at IS NOT NULL "
            "AND error_code IS NULL AND error_retryable IS NULL) "
            "OR (status = 'failed' AND completed_at IS NOT NULL "
            "AND error_code IS NOT NULL AND error_retryable IS NOT NULL)"
            ")",
            name="ck_job_attempts_status_fields",
        ),
        Index("ix_job_attempts_job_id", "job_id"),
        Index(
            "uq_job_attempts_one_active_per_job",
            "job_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )


class AuthSession(Base):
    """Server-side OIDC session bound to the portal session cookie."""

    __tablename__ = "auth_sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    groups: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(512), nullable=False)
    portal_origin: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(actor_id) >= 1",
            name="ck_auth_sessions_actor_id_min_length",
        ),
        CheckConstraint(
            "char_length(csrf_token) >= 32",
            name="ck_auth_sessions_csrf_token_min_length",
        ),
        CheckConstraint(
            "char_length(portal_origin) >= 1",
            name="ck_auth_sessions_portal_origin_min_length",
        ),
        Index("ix_auth_sessions_absolute_expires_at", "absolute_expires_at"),
        Index("ix_auth_sessions_last_seen_at", "last_seen_at"),
    )


class MatrixRow(Base):
    """Immutable matrix output row for one assessment item within an analysis run."""

    __tablename__ = "matrix_rows"

    matrix_row_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("analysis_runs.run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    assessment_item_type: Mapped[str] = mapped_column(String(32), nullable=False)
    assessment_item_id: Mapped[str] = mapped_column(String(128), nullable=False)
    model_proposed_status: Mapped[str] = mapped_column(String(32), nullable=False)
    system_status: Mapped[str] = mapped_column(String(32), nullable=False)
    finding_summary: Mapped[str] = mapped_column(String(4000), nullable=False)
    gaps: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    assessor_questions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    context_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    producing_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("analysis_runs.run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("analysis_runs.run_id", ondelete="RESTRICT"),
        nullable=False,
    )

    analysis_run: Mapped[AnalysisRun] = relationship(
        back_populates="matrix_rows",
        foreign_keys=[run_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "assessment_item_id",
            name="uq_matrix_rows_run_id_assessment_item_id",
        ),
        ck.enum_check(
            "assessment_item_type",
            ev.ASSESSMENT_ITEM_TYPE_VALUES,
            constraint_name="ck_matrix_rows_assessment_item_type",
        ),
        ck.regex_check(
            "assessment_item_id",
            r"^[A-Za-z0-9][A-Za-z0-9()._-]{1,127}$",
            constraint_name="ck_matrix_rows_assessment_item_id",
        ),
        ck.enum_check(
            "model_proposed_status",
            ev.MATRIX_ROW_STATUS_VALUES,
            constraint_name="ck_matrix_rows_model_proposed_status",
        ),
        ck.enum_check(
            "system_status",
            ev.MATRIX_ROW_STATUS_VALUES,
            constraint_name="ck_matrix_rows_system_status",
        ),
        CheckConstraint(
            "char_length(finding_summary) >= 1",
            name="ck_matrix_rows_finding_summary_min_length",
        ),
        Index("ix_matrix_rows_run_id", "run_id"),
        Index("ix_matrix_rows_run_id_assessment_item_id", "run_id", "assessment_item_id"),
        Index("ix_matrix_rows_model_proposed_status", "model_proposed_status"),
    )


class OidcLoginState(Base):
    """Short-lived PKCE login state persisted until the OIDC callback completes."""

    __tablename__ = "oidc_login_states"

    state_token: Mapped[str] = mapped_column(String(128), primary_key=True)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "char_length(state_token) >= 16",
            name="ck_oidc_login_states_state_token_min_length",
        ),
        Index("ix_oidc_login_states_expires_at", "expires_at"),
    )


class PackageRevisionIntakeWork(Base):
    """Durable Postgres queue row for one package-revision intake phase."""

    __tablename__ = "package_revision_intake_work"

    package_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("package_revisions.package_revision_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    work_phase: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fence_token: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    expected_revision_version: Mapped[int] = mapped_column(Integer, nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(128))

    package_revision: Mapped[PackageRevision] = relationship(back_populates="intake_work")
    attempts: Mapped[list[PackageRevisionIntakeAttempt]] = relationship(
        back_populates="work",
        foreign_keys="[PackageRevisionIntakeAttempt.package_revision_id, PackageRevisionIntakeAttempt.work_phase]",
    )

    __table_args__ = (
        ck.enum_check(
            "work_phase",
            ev.INTAKE_WORK_PHASE_VALUES,
            constraint_name="ck_package_revision_intake_work_work_phase",
        ),
        ck.enum_check(
            "status",
            ev.INTAKE_WORK_STATUS_VALUES,
            constraint_name="ck_package_revision_intake_work_status",
        ),
        ck.regex_check(
            "last_error_code",
            ck.ERROR_CODE_REGEX,
            constraint_name="ck_package_revision_intake_work_last_error_code",
            nullable=True,
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_package_revision_intake_work_attempt_count_non_negative",
        ),
        CheckConstraint(
            "expected_revision_version >= 1",
            name="ck_pr_intake_work_exp_rev_version_positive",
        ),
        CheckConstraint(
            "("
            "(status = 'leased' AND lease_owner IS NOT NULL "
            "AND char_length(lease_owner) >= 1 "
            "AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL "
            "AND fence_token IS NOT NULL) "
            "OR (status IN ('available', 'completed', 'failed', 'reconciliation_required') "
            "AND lease_owner IS NULL AND lease_expires_at IS NULL AND heartbeat_at IS NULL "
            "AND fence_token IS NULL)"
            ")",
            name="ck_package_revision_intake_work_lease_fields_match_status",
        ),
        Index(
            "ix_package_revision_intake_work_status_available_at",
            "status",
            "available_at",
        ),
        Index(
            "ix_package_revision_intake_work_work_phase_status",
            "work_phase",
            "status",
        ),
        Index("ix_package_revision_intake_work_lease_expires_at", "lease_expires_at"),
    )


class PackageRevisionIntakeAttempt(Base):
    """Child record of a claimed or completed intake work attempt."""

    __tablename__ = "package_revision_intake_attempts"

    attempt_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    package_revision_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    work_phase: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    lease_owner: Mapped[str] = mapped_column(String(255), nullable=False)
    fence_token: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    expected_revision_version: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_retryable: Mapped[bool | None] = mapped_column(Boolean)

    work: Mapped[PackageRevisionIntakeWork] = relationship(
        back_populates="attempts",
        foreign_keys=[package_revision_id, work_phase],
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["package_revision_id", "work_phase"],
            [
                "package_revision_intake_work.package_revision_id",
                "package_revision_intake_work.work_phase",
            ],
            ondelete="RESTRICT",
            name="fk_package_revision_intake_attempts_work",
        ),
        UniqueConstraint(
            "package_revision_id",
            "work_phase",
            "attempt_number",
            name="uq_pr_intake_attempt_revision_phase_number",
        ),
        ck.enum_check(
            "work_phase",
            ev.INTAKE_WORK_PHASE_VALUES,
            constraint_name="ck_package_revision_intake_attempts_work_phase",
        ),
        ck.enum_check(
            "status",
            ev.INTAKE_ATTEMPT_STATUS_VALUES,
            constraint_name="ck_package_revision_intake_attempts_status",
        ),
        ck.regex_check(
            "error_code",
            ck.ERROR_CODE_REGEX,
            constraint_name="ck_package_revision_intake_attempts_error_code",
            nullable=True,
        ),
        CheckConstraint(
            "attempt_number >= 1",
            name="ck_package_revision_intake_attempts_attempt_number_positive",
        ),
        CheckConstraint(
            "expected_revision_version >= 1",
            name="ck_pr_intake_attempt_exp_rev_version_positive",
        ),
        CheckConstraint(
            "char_length(lease_owner) >= 1",
            name="ck_package_revision_intake_attempts_lease_owner_min_length",
        ),
        CheckConstraint(
            "("
            "(status = 'active' AND completed_at IS NULL "
            "AND error_code IS NULL AND error_retryable IS NULL) "
            "OR (status = 'succeeded' AND completed_at IS NOT NULL "
            "AND error_code IS NULL AND error_retryable IS NULL) "
            "OR (status = 'failed' AND completed_at IS NOT NULL "
            "AND error_code IS NOT NULL AND error_retryable IS NOT NULL)"
            ")",
            name="ck_package_revision_intake_attempts_status_fields",
        ),
        Index(
            "ix_package_revision_intake_attempts_revision_phase",
            "package_revision_id",
            "work_phase",
        ),
        Index(
            "uq_package_revision_intake_attempts_one_active_per_work",
            "package_revision_id",
            "work_phase",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )


class PackageNormalizationStep(Base):
    """Revision-scoped durable record for one ``normalize_proposal`` model step."""

    __tablename__ = "package_normalization_steps"

    step_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    package_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("package_revisions.package_revision_id", ondelete="RESTRICT"),
        nullable=False,
    )
    step_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    fact_bundle_sha256: Mapped[str | None] = mapped_column(String(64))
    schema_id: Mapped[str | None] = mapped_column(String(255))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    prompt_sha256: Mapped[str | None] = mapped_column(String(64))
    prompt_storage_key: Mapped[str | None] = mapped_column(String(512))
    fact_bundle_storage_key: Mapped[str | None] = mapped_column(String(512))
    response_storage_key: Mapped[str | None] = mapped_column(String(512))
    endpoint_profile: Mapped[str | None] = mapped_column(String(32))
    endpoint_host: Mapped[str | None] = mapped_column(String(253))
    model_requested: Mapped[str | None] = mapped_column(String(255))
    model_reported: Mapped[str | None] = mapped_column(String(255))
    temperature: Mapped[float | None] = mapped_column(Numeric(8, 4))
    input_limit: Mapped[int | None] = mapped_column(Integer)
    output_limit: Mapped[int | None] = mapped_column(Integer)
    timeout_seconds: Mapped[float | None] = mapped_column(Numeric(10, 3))
    attempt: Mapped[int | None] = mapped_column(Integer)
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    response_sha256: Mapped[str | None] = mapped_column(String(64))
    validation_outcome: Mapped[str | None] = mapped_column(String(64))
    llm_call_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    repair_attempted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_retryable: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    package_revision: Mapped[PackageRevision] = relationship(
        back_populates="normalization_steps"
    )

    __table_args__ = (
        UniqueConstraint(
            "package_revision_id",
            "step_key",
            name="uq_pkg_norm_steps_revision_step_key",
        ),
        ck.enum_check(
            "status",
            ev.NORMALIZATION_STEP_STATUS_VALUES,
            constraint_name="ck_pkg_norm_steps_status",
        ),
        ck.regex_check(
            "step_key",
            ck.STEP_KEY_REGEX,
            constraint_name="ck_pkg_norm_steps_step_key",
        ),
        ck.sha256_check(
            "input_digest",
            constraint_name="ck_pkg_norm_steps_input_digest",
        ),
        ck.sha256_check(
            "fact_bundle_sha256",
            constraint_name="ck_pkg_norm_steps_fact_bundle_sha256",
            nullable=True,
        ),
        ck.sha256_check(
            "prompt_sha256",
            constraint_name="ck_pkg_norm_steps_prompt_sha256",
            nullable=True,
        ),
        ck.sha256_check(
            "response_sha256",
            constraint_name="ck_pkg_norm_steps_response_sha256",
            nullable=True,
        ),
        ck.regex_check(
            "prompt_storage_key",
            ck.NORMALIZATION_PROTECTED_KEY_REGEX,
            constraint_name="ck_pkg_norm_steps_prompt_storage_key",
            nullable=True,
        ),
        ck.regex_check(
            "fact_bundle_storage_key",
            ck.NORMALIZATION_PROTECTED_KEY_REGEX,
            constraint_name="ck_pkg_norm_steps_fact_bundle_storage_key",
            nullable=True,
        ),
        ck.regex_check(
            "response_storage_key",
            ck.NORMALIZATION_PROTECTED_KEY_REGEX,
            constraint_name="ck_pkg_norm_steps_response_storage_key",
            nullable=True,
        ),
        ck.enum_check(
            "endpoint_profile",
            ev.ENDPOINT_PROFILE_VALUES,
            constraint_name="ck_pkg_norm_steps_endpoint_profile",
            nullable=True,
        ),
        ck.regex_check(
            "validation_outcome",
            ck.VALIDATION_OUTCOME_REGEX,
            constraint_name="ck_pkg_norm_steps_validation_outcome",
            nullable=True,
        ),
        ck.regex_check(
            "error_code",
            ck.ERROR_CODE_REGEX,
            constraint_name="ck_pkg_norm_steps_error_code",
            nullable=True,
        ),
        CheckConstraint(
            "llm_call_count >= 0 AND llm_call_count <= 2",
            name="ck_pkg_norm_steps_llm_call_count_range",
        ),
        CheckConstraint(
            "char_length(schema_id) >= 1 OR schema_id IS NULL",
            name="ck_pkg_norm_steps_schema_id_min_length",
        ),
        CheckConstraint(
            "char_length(prompt_version) >= 1 OR prompt_version IS NULL",
            name="ck_pkg_norm_steps_prompt_version_min_length",
        ),
        CheckConstraint(
            "char_length(endpoint_host) >= 1 OR endpoint_host IS NULL",
            name="ck_pkg_norm_steps_endpoint_host_min_length",
        ),
        CheckConstraint(
            "char_length(model_requested) >= 1 OR model_requested IS NULL",
            name="ck_pkg_norm_steps_model_requested_min_length",
        ),
        CheckConstraint(
            "char_length(model_reported) >= 1 OR model_reported IS NULL",
            name="ck_pkg_norm_steps_model_reported_min_length",
        ),
        CheckConstraint(
            "char_length(provider_request_id) >= 1 OR provider_request_id IS NULL",
            name="ck_pkg_norm_steps_provider_request_id_min_length",
        ),
        CheckConstraint(
            "temperature IS NULL OR temperature >= 0",
            name="ck_pkg_norm_steps_temperature_non_negative",
        ),
        CheckConstraint(
            "input_limit IS NULL OR input_limit >= 1",
            name="ck_pkg_norm_steps_input_limit_positive",
        ),
        CheckConstraint(
            "output_limit IS NULL OR output_limit >= 1",
            name="ck_pkg_norm_steps_output_limit_positive",
        ),
        CheckConstraint(
            "timeout_seconds IS NULL OR timeout_seconds > 0",
            name="ck_pkg_norm_steps_timeout_seconds_positive",
        ),
        CheckConstraint(
            "attempt IS NULL OR attempt >= 1",
            name="ck_pkg_norm_steps_attempt_positive",
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_pkg_norm_steps_input_tokens_non_negative",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_pkg_norm_steps_output_tokens_non_negative",
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_pkg_norm_steps_latency_ms_non_negative",
        ),
        CheckConstraint(
            ck.PACKAGE_NORMALIZATION_STEP_STATUS_FIELDS_SQL,
            name="ck_pkg_norm_steps_status_fields",
        ),
        CheckConstraint(
            "(repair_attempted = false OR llm_call_count = 2)",
            name="ck_pkg_norm_steps_repair_requires_two_calls",
        ),
        Index("ix_pkg_norm_steps_package_revision_id", "package_revision_id"),
        Index("ix_pkg_norm_steps_status", "status"),
    )


class AuthorizationDecisionRecord(Base):
    """Externally issued authorization decision metadata attached post-export."""

    __tablename__ = "authorization_decision_records"

    authorization_decision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True
    )
    system_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("systems.system_id", ondelete="RESTRICT"),
        nullable=False,
    )
    package_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("package_revisions.package_revision_id", ondelete="RESTRICT"),
    )
    decision_type: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_date: Mapped[str] = mapped_column(String(32), nullable=False)
    issuing_authority: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    notes: Mapped[str | None] = mapped_column(String(2000))
    attached_by: Mapped[str] = mapped_column(String(255), nullable=False)
    attached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "char_length(decision_type) >= 1",
            name="ck_auth_decision_records_decision_type_min_length",
        ),
        CheckConstraint(
            "char_length(issuing_authority) >= 1",
            name="ck_auth_decision_records_issuing_authority_min_length",
        ),
        Index("ix_auth_decision_records_system_id", "system_id"),
    )


class ReviewRevision(Base):
    """Versioned human review revision for one analysis run."""

    __tablename__ = "review_revisions"

    review_revision_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("analysis_runs.run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    dispositions: Mapped[list["Disposition"]] = relationship(back_populates="review_revision")
    comments: Mapped[list["ReviewComment"]] = relationship(back_populates="review_revision")

    __table_args__ = (
        UniqueConstraint("run_id", "version", name="uq_review_revisions_run_id_version"),
        ck.enum_check(
            "status",
            ev.REVIEW_REVISION_STATUS_VALUES,
            constraint_name="ck_review_revisions_status",
        ),
        CheckConstraint("version >= 1", name="ck_review_revisions_version_positive"),
        Index("ix_review_revisions_run_id", "run_id"),
    )


class Disposition(Base):
    """Human disposition for one matrix row within a review revision."""

    __tablename__ = "dispositions"

    disposition_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    review_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_revisions.review_revision_id", ondelete="RESTRICT"),
        nullable=False,
    )
    matrix_row_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("matrix_rows.matrix_row_id", ondelete="RESTRICT"),
        nullable=False,
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    edited_summary: Mapped[str | None] = mapped_column(String(4000))
    notes: Mapped[str | None] = mapped_column(String(4000))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    decided_by: Mapped[str] = mapped_column(String(255), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    review_revision: Mapped[ReviewRevision] = relationship(back_populates="dispositions")

    __table_args__ = (
        UniqueConstraint(
            "review_revision_id",
            "matrix_row_id",
            name="uq_dispositions_review_revision_id_matrix_row_id",
        ),
        ck.enum_check(
            "decision",
            ev.DISPOSITION_DECISION_VALUES,
            constraint_name="ck_dispositions_decision",
        ),
        CheckConstraint("version >= 1", name="ck_dispositions_version_positive"),
        Index("ix_dispositions_review_revision_id", "review_revision_id"),
    )


class EvidenceRequest(Base):
    """Evidence request routed from an insufficient_evidence disposition."""

    __tablename__ = "evidence_requests"

    evidence_request_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    review_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_revisions.review_revision_id", ondelete="RESTRICT"),
        nullable=False,
    )
    disposition_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("dispositions.disposition_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    matrix_row_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("matrix_rows.matrix_row_id", ondelete="RESTRICT"),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("analysis_runs.run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    assessment_item_id: Mapped[str] = mapped_column(String(128), nullable=False)
    assessment_item_type: Mapped[str] = mapped_column(String(32), nullable=False)
    system_status: Mapped[str] = mapped_column(String(32), nullable=False)
    finding_summary: Mapped[str] = mapped_column(String(4000), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "review_revision_id",
            "matrix_row_id",
            name="uq_evidence_requests_review_revision_id_matrix_row_id",
        ),
        ck.enum_check(
            "system_status",
            ev.MATRIX_STATUS_VALUES,
            constraint_name="ck_evidence_requests_system_status",
        ),
        ck.enum_check(
            "assessment_item_type",
            ev.ASSESSMENT_ITEM_TYPE_VALUES,
            constraint_name="ck_evidence_requests_assessment_item_type",
        ),
        Index("ix_evidence_requests_review_revision_id", "review_revision_id"),
    )


class PoamCandidate(Base):
    """Human-confirmed POA&M draft candidate linked to one matrix row."""

    __tablename__ = "poam_candidates"

    poam_candidate_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    review_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_revisions.review_revision_id", ondelete="RESTRICT"),
        nullable=False,
    )
    disposition_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("dispositions.disposition_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    matrix_row_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("matrix_rows.matrix_row_id", ondelete="RESTRICT"),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("analysis_runs.run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    assessment_item_id: Mapped[str] = mapped_column(String(128), nullable=False)
    assessment_item_type: Mapped[str] = mapped_column(String(32), nullable=False)
    system_status: Mapped[str] = mapped_column(String(32), nullable=False)
    weakness_summary: Mapped[str] = mapped_column(String(4000), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "review_revision_id",
            "matrix_row_id",
            name="uq_poam_candidates_review_revision_id_matrix_row_id",
        ),
        ck.enum_check(
            "system_status",
            ev.MATRIX_STATUS_VALUES,
            constraint_name="ck_poam_candidates_system_status",
        ),
        ck.enum_check(
            "assessment_item_type",
            ev.ASSESSMENT_ITEM_TYPE_VALUES,
            constraint_name="ck_poam_candidates_assessment_item_type",
        ),
        Index("ix_poam_candidates_review_revision_id", "review_revision_id"),
    )


class ReviewComment(Base):
    """Comment attached to a review revision."""

    __tablename__ = "review_comments"

    review_comment_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    review_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_revisions.review_revision_id", ondelete="RESTRICT"),
        nullable=False,
    )
    matrix_row_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    body: Mapped[str] = mapped_column(String(4000), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    review_revision: Mapped[ReviewRevision] = relationship(back_populates="comments")

    __table_args__ = (
        CheckConstraint("char_length(body) >= 1", name="ck_review_comments_body_min_length"),
        Index("ix_review_comments_review_revision_id", "review_revision_id"),
    )


class ExportDraft(Base):
    """Export draft bound to a submitted review revision."""

    __tablename__ = "export_drafts"

    export_draft_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    review_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_revisions.review_revision_id", ondelete="RESTRICT"),
        nullable=False,
    )
    payload_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    destination_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    approval: Mapped["Approval | None"] = relationship(back_populates="export_draft", uselist=False)

    __table_args__ = (
        ck.enum_check(
            "status",
            ev.EXPORT_DRAFT_STATUS_VALUES,
            constraint_name="ck_export_drafts_status",
        ),
        ck.regex_check(
            "payload_manifest_sha256",
            r"^[a-f0-9]{64}$",
            constraint_name="ck_export_drafts_payload_manifest_sha256",
        ),
        CheckConstraint(
            "destination_type = 'download'",
            name="ck_export_drafts_destination_type_download",
        ),
        Index("ix_export_drafts_review_revision_id", "review_revision_id"),
    )


class Approval(Base):
    """Export approval bound to exact payload manifest hash."""

    __tablename__ = "approvals"

    approval_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    export_draft_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("export_drafts.export_draft_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    payload_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    submitted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(255))
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(2000))

    export_draft: Mapped[ExportDraft] = relationship(back_populates="approval")
    export: Mapped["ExportRecord | None"] = relationship(back_populates="approval", uselist=False)

    __table_args__ = (
        ck.enum_check(
            "decision",
            ev.APPROVAL_DECISION_VALUES,
            constraint_name="ck_approvals_decision",
        ),
        ck.regex_check(
            "payload_manifest_sha256",
            r"^[a-f0-9]{64}$",
            constraint_name="ck_approvals_payload_manifest_sha256",
        ),
        Index("ix_approvals_export_draft_id", "export_draft_id"),
    )


class ExportRecord(Base):
    """Immutable export record for one approved download."""

    __tablename__ = "exports"

    export_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    approval_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("approvals.approval_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    profile_id: Mapped[str] = mapped_column(String(64), nullable=False)
    system_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    package_revision_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    review_revision_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    payload_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    approval: Mapped[Approval] = relationship(back_populates="export")

    __table_args__ = (
        ck.regex_check(
            "payload_manifest_sha256",
            r"^[a-f0-9]{64}$",
            constraint_name="ck_exports_payload_manifest_sha256",
        ),
        Index("ix_exports_approval_id", "approval_id"),
    )


class PackageRevisionSearchChunk(Base):
    """Immutable searchable evidence chunk for one ready package revision."""

    __tablename__ = "package_revision_search_chunks"

    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    package_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("package_revisions.package_revision_id", ondelete="RESTRICT"),
        nullable=False,
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("source_artifacts.artifact_id", ondelete="RESTRICT"),
        nullable=False,
    )
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_start: Mapped[int] = mapped_column(Integer, nullable=False)
    normalized_end: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String(6000), nullable=False)
    search_vector: Mapped[Any] = mapped_column(TSVECTOR, nullable=False)

    __table_args__ = (
        ck.sha256_check("chunk_id", constraint_name="ck_package_revision_search_chunks_chunk_id"),
        ck.sha256_check(
            "artifact_sha256",
            constraint_name="ck_package_revision_search_chunks_artifact_sha256",
        ),
        CheckConstraint(
            "normalized_start >= 0",
            name="ck_package_revision_search_chunks_normalized_start_nonnegative",
        ),
        CheckConstraint(
            "normalized_end > normalized_start",
            name="ck_package_revision_search_chunks_normalized_end_positive",
        ),
        CheckConstraint(
            "char_length(text) >= 1 AND char_length(text) <= 6000",
            name="ck_package_revision_search_chunks_text_length",
        ),
        Index(
            "ix_package_revision_search_chunks_revision_rank",
            "package_revision_id",
            "chunk_id",
        ),
        Index(
            "ix_package_revision_search_chunks_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
    )


class PackageRevisionSearchIndex(Base):
    """Search index readiness marker for one sealed package revision."""

    __tablename__ = "package_revision_search_indexes"

    package_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("package_revisions.package_revision_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ck.sha256_check(
            "content_sha256",
            constraint_name="ck_package_revision_search_indexes_content_sha256",
        ),
        CheckConstraint(
            "chunk_count >= 0",
            name="ck_package_revision_search_indexes_chunk_count_nonnegative",
        ),
    )


class PackageRevisionChatUsage(Base):
    """Bounded per-user chat usage counters for one package revision."""

    __tablename__ = "package_revision_chat_usage"

    package_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("package_revisions.package_revision_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    actor_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    rate_window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    rate_window_count: Mapped[int] = mapped_column(Integer, nullable=False)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "char_length(actor_id) >= 1",
            name="ck_package_revision_chat_usage_actor_id_min_length",
        ),
        CheckConstraint(
            "rate_window_count >= 0",
            name="ck_package_revision_chat_usage_rate_window_count_nonnegative",
        ),
        CheckConstraint(
            "turn_count >= 0",
            name="ck_package_revision_chat_usage_turn_count_nonnegative",
        ),
        CheckConstraint(
            "daily_token_count >= 0",
            name="ck_package_revision_chat_usage_daily_token_count_nonnegative",
        ),
        Index(
            "ix_package_revision_chat_usage_revision_actor",
            "package_revision_id",
            "actor_id",
        ),
    )
