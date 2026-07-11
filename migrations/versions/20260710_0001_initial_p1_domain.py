"""Initial P1 PostgreSQL domain foundation.

The ``jobs`` table is intentionally omitted: persistent job status and attempt
semantics remain unresolved in the technical specification (Section 20).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260710_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "systems",
        sa.Column("system_id", sa.Uuid(), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("external_system_id", sa.String(255)),
        sa.Column("owner_group", sa.String(255), nullable=False),
        sa.Column("viewer_groups", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "char_length(owner_group) >= 1",
            name="ck_systems_owner_group_min_length",
        ),
        sa.CheckConstraint(
            "char_length(display_name) >= 1",
            name="ck_systems_display_name_min_length",
        ),
    )
    op.create_index("ix_systems_owner_group", "systems", ["owner_group"])

    op.create_table(
        "package_revisions",
        sa.Column("package_revision_id", sa.Uuid(), primary_key=True),
        sa.Column("system_id", sa.Uuid(), nullable=False),
        sa.Column("parent_revision_id", sa.Uuid()),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("certification_class", sa.String(1)),
        sa.Column("impact_level", sa.String(16)),
        sa.Column("data_origin", sa.String(64), nullable=False),
        sa.Column("sensitivity", sa.String(64), nullable=False),
        sa.Column("effective_data_labels", postgresql.JSONB(), nullable=False),
        sa.Column("authority_manifest_id", sa.String(128), nullable=False),
        sa.Column("content_manifest_sha256", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "certification_class IS NULL OR certification_class IN ('B', 'C')",
            name="ck_package_revisions_certification_class",
        ),
        sa.CheckConstraint(
            "data_origin IN ('synthetic', 'redacted_nonproduction', 'customer_production')",
            name="ck_package_revisions_data_origin",
        ),
        sa.CheckConstraint(
            "content_manifest_sha256 IS NULL OR content_manifest_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_package_revisions_content_manifest_sha256",
        ),
        sa.CheckConstraint(
            "sensitivity IN ('public', 'internal_unclassified', 'customer_sensitive', 'cui', 'classified', 'unknown')",
            name="ck_package_revisions_sensitivity",
        ),
        sa.CheckConstraint(
            "authority_manifest_id ~ '^[a-z0-9][a-z0-9._-]{2,127}$'",
            name="ck_package_revisions_authority_manifest_id",
        ),
        sa.ForeignKeyConstraint(
            ["parent_revision_id"],
            ["package_revisions.package_revision_id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "impact_level IS NULL OR impact_level IN ('low', 'moderate', 'high')",
            name="ck_package_revisions_impact_level",
        ),
        sa.CheckConstraint(
            "status <> 'ready' OR content_manifest_sha256 IS NOT NULL",
            name="ck_package_revisions_ready_requires_content_manifest_sha256",
        ),
        sa.CheckConstraint(
            "status IN ('uploading', 'scanning', 'extracting', 'awaiting_confirmation', 'ready', 'invalid', 'quarantined', 'archived')",
            name="ck_package_revisions_status",
        ),
        sa.ForeignKeyConstraint(["system_id"], ["systems.system_id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "char_length(created_by) >= 1",
            name="ck_package_revisions_created_by_min_length",
        ),
        sa.CheckConstraint(
            "profile_id IN ('fedramp_20x_program', 'fedramp_rev5_transition', 'fisma_agency_security')",
            name="ck_package_revisions_profile_id",
        ),
    )
    op.create_index(
        "ix_package_revisions_parent_revision_id",
        "package_revisions",
        ["parent_revision_id"],
    )
    op.create_index("ix_package_revisions_status", "package_revisions", ["status"])
    op.create_index("ix_package_revisions_system_id", "package_revisions", ["system_id"])

    op.create_table(
        "source_artifacts",
        sa.Column("artifact_id", sa.Uuid(), primary_key=True),
        sa.Column("package_revision_id", sa.Uuid(), nullable=False),
        sa.Column("display_filename", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(67), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("declared_media_type", sa.String(255), nullable=False),
        sa.Column("detected_media_type", sa.String(255), nullable=False),
        sa.Column("artifact_kind", sa.String(32), nullable=False),
        sa.Column("malware_scan_status", sa.String(16), nullable=False),
        sa.Column("extraction_status", sa.String(16), nullable=False),
        sa.Column("source_date", sa.Date()),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "size_bytes >= 1 AND size_bytes <= 104857600",
            name="ck_source_artifacts_size_bytes_range",
        ),
        sa.CheckConstraint(
            "storage_key ~ '^[a-f0-9]{2}/[a-f0-9]{64}$'",
            name="ck_source_artifacts_storage_key",
        ),
        sa.CheckConstraint(
            "char_length(display_filename) >= 1",
            name="ck_source_artifacts_display_filename_min_length",
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_source_artifacts_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["package_revision_id"],
            ["package_revisions.package_revision_id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "artifact_kind IN ('manifest', 'fedramp_cpo', 'fedramp_sdr', 'fedramp_ocr', 'fedramp_scg', 'oscal', 'evidence_document', 'scanner_export', 'architecture', 'attestation', 'reference_catalog')",
            name="ck_source_artifacts_artifact_kind",
        ),
        sa.CheckConstraint(
            "malware_scan_status IN ('pending', 'clean', 'infected', 'error')",
            name="ck_source_artifacts_malware_scan_status",
        ),
        sa.CheckConstraint(
            "extraction_status IN ('pending', 'succeeded', 'failed', 'not_applicable')",
            name="ck_source_artifacts_extraction_status",
        ),
    )
    op.create_index(
        "ix_source_artifacts_package_revision_id",
        "source_artifacts",
        ["package_revision_id"],
    )
    op.create_index("ix_source_artifacts_sha256", "source_artifacts", ["sha256"])

    op.create_table(
        "analysis_runs",
        sa.Column("run_id", sa.Uuid(), primary_key=True),
        sa.Column("package_revision_id", sa.Uuid(), nullable=False),
        sa.Column("parent_run_id", sa.Uuid()),
        sa.Column("run_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("requested_by", sa.String(255), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("authority_manifest_id", sa.String(128), nullable=False),
        sa.Column("analysis_profile_sha256", sa.String(64), nullable=False),
        sa.Column("config_fingerprint", sa.String(64), nullable=False),
        sa.Column("prompt_bundle_sha256", sa.String(64), nullable=False),
        sa.Column("model_profile", sa.String(255), nullable=False),
        sa.Column("artifact_manifest_sha256", sa.String(64)),
        sa.Column("llm_call_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.Column("error_retryable", sa.Boolean()),
        sa.ForeignKeyConstraint(
            ["parent_run_id"],
            ["analysis_runs.run_id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'policy_blocked')",
            name="ck_analysis_runs_status",
        ),
        sa.CheckConstraint(
            "authority_manifest_id ~ '^[a-z0-9][a-z0-9._-]{2,127}$'",
            name="ck_analysis_runs_authority_manifest_id",
        ),
        sa.CheckConstraint(
            "prompt_bundle_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_analysis_runs_prompt_bundle_sha256",
        ),
        sa.CheckConstraint(
            "char_length(model_profile) >= 1",
            name="ck_analysis_runs_model_profile_min_length",
        ),
        sa.ForeignKeyConstraint(
            ["package_revision_id"],
            ["package_revisions.package_revision_id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "analysis_profile_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_analysis_runs_analysis_profile_sha256",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_]{2,127}$'",
            name="ck_analysis_runs_error_code",
        ),
        sa.CheckConstraint(
            "char_length(requested_by) >= 1",
            name="ck_analysis_runs_requested_by_min_length",
        ),
        sa.CheckConstraint(
            "artifact_manifest_sha256 IS NULL OR artifact_manifest_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_analysis_runs_artifact_manifest_sha256",
        ),
        sa.CheckConstraint(
            "llm_call_count >= 0 AND llm_call_count <= 120",
            name="ck_analysis_runs_llm_call_count_range",
        ),
        sa.CheckConstraint(
            "status <> 'policy_blocked' OR llm_call_count = 0",
            name="ck_analysis_runs_policy_blocked_requires_zero_llm_calls",
        ),
        sa.CheckConstraint(
            "run_type IN ('full', 'targeted', 'deterministic_only')",
            name="ck_analysis_runs_run_type",
        ),
        sa.CheckConstraint(
            "config_fingerprint ~ '^[a-f0-9]{64}$'",
            name="ck_analysis_runs_config_fingerprint",
        ),
    )
    op.create_index(
        "ix_analysis_runs_package_revision_id",
        "analysis_runs",
        ["package_revision_id"],
    )
    op.create_index("ix_analysis_runs_parent_run_id", "analysis_runs", ["parent_run_id"])
    op.create_index("ix_analysis_runs_status", "analysis_runs", ["status"])

    op.create_table(
        "run_steps",
        sa.Column("step_id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("step_key", sa.String(64), nullable=False),
        sa.Column("step_type", sa.String(32), nullable=False),
        sa.Column("schema_id", sa.String(255), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("prompt_sha256", sa.String(64), nullable=False),
        sa.Column("fact_bundle_sha256", sa.String(64), nullable=False),
        sa.Column("endpoint_profile", sa.String(32), nullable=False),
        sa.Column("endpoint_host", sa.String(253), nullable=False),
        sa.Column("model_requested", sa.String(255), nullable=False),
        sa.Column("model_reported", sa.String(255), nullable=False),
        sa.Column("temperature", sa.Numeric(8, 4), nullable=False),
        sa.Column("input_limit", sa.Integer(), nullable=False),
        sa.Column("output_limit", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Numeric(10, 3), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("provider_request_id", sa.String(255), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("response_sha256", sa.String(64), nullable=False),
        sa.Column("validation_outcome", sa.String(64), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "char_length(model_reported) >= 1",
            name="ck_run_steps_model_reported_min_length",
        ),
        sa.CheckConstraint(
            "step_type IN ('normalize_proposal', 'sufficiency_matrix', 'consistency_brief', 'narrative_flags', 'provider_draft', 'ksi_summary', 'ocr_summary', 'package_chat')",
            name="ck_run_steps_step_type",
        ),
        sa.CheckConstraint(
            "char_length(provider_request_id) >= 1",
            name="ck_run_steps_provider_request_id_min_length",
        ),
        sa.CheckConstraint(
            "step_key ~ '^[a-z][a-z0-9_]{1,63}$'",
            name="ck_run_steps_step_key",
        ),
        sa.UniqueConstraint("run_id", "step_key", name="uq_run_steps_run_id_step_key"),
        sa.CheckConstraint("temperature >= 0", name="ck_run_steps_temperature_non_negative"),
        sa.CheckConstraint(
            "validation_outcome ~ '^[a-z][a-z0-9_]{1,63}$'",
            name="ck_run_steps_validation_outcome",
        ),
        sa.CheckConstraint("input_limit >= 1", name="ck_run_steps_input_limit_positive"),
        sa.CheckConstraint(
            "prompt_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_run_steps_prompt_sha256",
        ),
        sa.CheckConstraint("output_limit >= 1", name="ck_run_steps_output_limit_positive"),
        sa.CheckConstraint(
            "fact_bundle_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_run_steps_fact_bundle_sha256",
        ),
        sa.CheckConstraint(
            "timeout_seconds > 0",
            name="ck_run_steps_timeout_seconds_positive",
        ),
        sa.CheckConstraint(
            "response_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_run_steps_response_sha256",
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_run_steps_attempt_positive"),
        sa.CheckConstraint(
            "char_length(schema_id) >= 1",
            name="ck_run_steps_schema_id_min_length",
        ),
        sa.CheckConstraint(
            "input_tokens >= 0",
            name="ck_run_steps_input_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "char_length(prompt_version) >= 1",
            name="ck_run_steps_prompt_version_min_length",
        ),
        sa.CheckConstraint(
            "output_tokens >= 0",
            name="ck_run_steps_output_tokens_non_negative",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.run_id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "char_length(endpoint_host) >= 1",
            name="ck_run_steps_endpoint_host_min_length",
        ),
        sa.CheckConstraint("latency_ms >= 0", name="ck_run_steps_latency_ms_non_negative"),
        sa.CheckConstraint(
            "char_length(model_requested) >= 1",
            name="ck_run_steps_model_requested_min_length",
        ),
        sa.CheckConstraint(
            "endpoint_profile IN ('mock', 'external_openai', 'internal_openai_compatible')",
            name="ck_run_steps_endpoint_profile",
        ),
    )
    op.create_index("ix_run_steps_run_id", "run_steps", ["run_id"])

    op.create_table(
        "fact_proposals",
        sa.Column("fact_proposal_id", sa.Uuid(), primary_key=True),
        sa.Column("package_revision_id", sa.Uuid(), nullable=False),
        sa.Column("json_pointer", sa.String(2000), nullable=False),
        sa.Column("proposed_value", postgresql.JSONB(), nullable=False),
        sa.Column("source_artifact_id", sa.Uuid(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("source_locator", postgresql.JSONB(), nullable=False),
        sa.Column("extraction_method", sa.String(32), nullable=False),
        sa.Column("model_step_id", sa.Uuid()),
        sa.Column("review_status", sa.String(16), nullable=False),
        sa.Column("reviewed_by", sa.String(255)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "source_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_fact_proposals_source_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["source_artifact_id"],
            ["source_artifacts.artifact_id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "review_status IN ('pending', 'accepted', 'rejected', 'edited')",
            name="ck_fact_proposals_review_status",
        ),
        sa.CheckConstraint(
            "json_pointer ~ '^(/([^~/]|~[01])*)*$'",
            name="ck_fact_proposals_json_pointer",
        ),
        sa.ForeignKeyConstraint(
            ["model_step_id"],
            ["run_steps.step_id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "extraction_method IN ('deterministic', 'text', 'vision', 'llm_normalize')",
            name="ck_fact_proposals_extraction_method",
        ),
        sa.ForeignKeyConstraint(
            ["package_revision_id"],
            ["package_revisions.package_revision_id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_fact_proposals_package_revision_id",
        "fact_proposals",
        ["package_revision_id"],
    )
    op.create_index(
        "ix_fact_proposals_review_status",
        "fact_proposals",
        ["review_status"],
    )
    op.create_index(
        "ix_fact_proposals_source_artifact_id",
        "fact_proposals",
        ["source_artifact_id"],
    )

    op.create_table(
        "idempotency_records",
        sa.Column("idempotency_record_id", sa.Uuid(), primary_key=True),
        sa.Column("principal", sa.String(255), nullable=False),
        sa.Column("operation", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "char_length(principal) >= 1",
            name="ck_idempotency_records_principal_min_length",
        ),
        sa.CheckConstraint(
            "char_length(operation) >= 1",
            name="ck_idempotency_records_operation_min_length",
        ),
        sa.UniqueConstraint(
            "principal",
            "operation",
            "idempotency_key",
            name="uq_idempotency_records_principal_operation_key",
        ),
        sa.CheckConstraint(
            "request_digest ~ '^[a-f0-9]{64}$'",
            name="ck_idempotency_records_request_digest",
        ),
        sa.CheckConstraint(
            "idempotency_key ~ '^[A-Za-z0-9._:-]{16,128}$'",
            name="ck_idempotency_records_idempotency_key",
        ),
        sa.CheckConstraint(
            "response_status >= 100 AND response_status <= 599",
            name="ck_idempotency_records_response_status_range",
        ),
    )
    op.create_index(
        "ix_idempotency_records_expires_at",
        "idempotency_records",
        ["expires_at"],
    )

    op.create_table(
        "audit_events",
        sa.Column("audit_event_id", sa.Uuid(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("object_type", sa.String(64), nullable=False),
        sa.Column("object_id", sa.String(255), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(128)),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("previous_event_hash", sa.String(64), nullable=False),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "previous_event_hash ~ '^[a-f0-9]{64}$'",
            name="ck_audit_events_previous_event_hash",
        ),
        sa.CheckConstraint(
            "object_type ~ '^[a-z][a-z0-9_]{2,63}$'",
            name="ck_audit_events_object_type",
        ),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'denied', 'failed')",
            name="ck_audit_events_outcome",
        ),
        sa.CheckConstraint(
            "char_length(object_id) >= 1",
            name="ck_audit_events_object_id_min_length",
        ),
        sa.CheckConstraint(
            "event_hash ~ '^[a-f0-9]{64}$'",
            name="ck_audit_events_event_hash",
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR reason_code ~ '^[a-z][a-z0-9_]{2,127}$'",
            name="ck_audit_events_reason_code",
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'service')",
            name="ck_audit_events_actor_type",
        ),
        sa.CheckConstraint(
            "action ~ '^[a-z][a-z0-9_.]{2,127}$'",
            name="ck_audit_events_action",
        ),
        sa.CheckConstraint(
            "char_length(actor_id) >= 1",
            name="ck_audit_events_actor_id_min_length",
        ),
    )
    op.create_index(
        "ix_audit_events_object_type_object_id",
        "audit_events",
        ["object_type", "object_id"],
    )
    op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_occurred_at", table_name="audit_events")
    op.drop_index("ix_audit_events_object_type_object_id", table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_index("ix_idempotency_records_expires_at", table_name="idempotency_records")
    op.drop_table("idempotency_records")

    op.drop_index("ix_fact_proposals_source_artifact_id", table_name="fact_proposals")
    op.drop_index("ix_fact_proposals_review_status", table_name="fact_proposals")
    op.drop_index("ix_fact_proposals_package_revision_id", table_name="fact_proposals")
    op.drop_table("fact_proposals")

    op.drop_index("ix_run_steps_run_id", table_name="run_steps")
    op.drop_table("run_steps")

    op.drop_index("ix_analysis_runs_status", table_name="analysis_runs")
    op.drop_index("ix_analysis_runs_parent_run_id", table_name="analysis_runs")
    op.drop_index("ix_analysis_runs_package_revision_id", table_name="analysis_runs")
    op.drop_table("analysis_runs")

    op.drop_index("ix_source_artifacts_sha256", table_name="source_artifacts")
    op.drop_index("ix_source_artifacts_package_revision_id", table_name="source_artifacts")
    op.drop_table("source_artifacts")

    op.drop_index("ix_package_revisions_system_id", table_name="package_revisions")
    op.drop_index("ix_package_revisions_status", table_name="package_revisions")
    op.drop_index("ix_package_revisions_parent_revision_id", table_name="package_revisions")
    op.drop_table("package_revisions")

    op.drop_index("ix_systems_owner_group", table_name="systems")
    op.drop_table("systems")
