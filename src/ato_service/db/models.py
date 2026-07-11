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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ato_service.db.base import Base
from ato_service.db import constraints as ck
from ato_service.db import enums as ev


class System(Base):
    __tablename__ = "systems"

    system_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    external_system_id: Mapped[str | None] = mapped_column(String(255))
    owner_group: Mapped[str] = mapped_column(String(255), nullable=False)
    viewer_groups: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    package_revisions: Mapped[list[PackageRevision]] = relationship(
        back_populates="system",
        foreign_keys="PackageRevision.system_id",
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
        Index("ix_systems_owner_group", "owner_group"),
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
    profile_id: Mapped[str] = mapped_column(String(64), nullable=False)
    certification_class: Mapped[str | None] = mapped_column(String(1))
    impact_level: Mapped[str | None] = mapped_column(String(16))
    data_origin: Mapped[str] = mapped_column(String(64), nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_data_labels: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    authority_manifest_id: Mapped[str] = mapped_column(String(128), nullable=False)
    content_manifest_sha256: Mapped[str | None] = mapped_column(String(64))
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

    __table_args__ = (
        ck.enum_check(
            "profile_id",
            ev.PROFILE_ID_VALUES,
            constraint_name="ck_package_revisions_profile_id",
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
        ),
        ck.enum_check(
            "sensitivity",
            ev.SENSITIVITY_VALUES,
            constraint_name="ck_package_revisions_sensitivity",
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
        CheckConstraint(
            "status <> 'ready' OR content_manifest_sha256 IS NOT NULL",
            name="ck_package_revisions_ready_requires_content_manifest_sha256",
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
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_retryable: Mapped[bool | None] = mapped_column(Boolean)

    package_revision: Mapped[PackageRevision] = relationship(back_populates="analysis_runs")
    parent_run: Mapped[AnalysisRun | None] = relationship(
        remote_side=[run_id],
        foreign_keys=[parent_run_id],
    )
    run_steps: Mapped[list[RunStep]] = relationship(back_populates="analysis_run")
    jobs: Mapped[list[Job]] = relationship(back_populates="analysis_run")

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
