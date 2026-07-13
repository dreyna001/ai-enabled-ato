"""Add revision-scoped package normalization step persistence."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260714_0009"
down_revision = "20260714_0008"
branch_labels = None
depends_on = None

_STATUS_VALUES = (
    "reserved",
    "running",
    "completed",
    "policy_blocked",
    "failed",
    "reconciliation_required",
)

_SHA256_REGEX = "^[a-f0-9]{64}$"
_STEP_KEY_REGEX = "^[a-z][a-z0-9_]{1,63}$"
_ERROR_CODE_REGEX = "^[a-z][a-z0-9_]{2,127}$"
_VALIDATION_OUTCOME_REGEX = "^[a-z][a-z0-9_]{1,63}$"
_PROTECTED_KEY_REGEX = (
    "^revisions/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
    "[89ab][0-9a-f]{3}-[0-9a-f]{12}/normalization/"
    "[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/"
    "(prompt|fact-bundle|response)\\.json$"
)
_ENDPOINT_PROFILE_VALUES = (
    "mock",
    "external_openai",
    "internal_openai_compatible",
)
_STATUS_FIELDS_SQL = (
    "("
    "(status = 'reserved' AND started_at IS NULL AND completed_at IS NULL "
    "AND llm_call_count = 0 AND repair_attempted = false "
    "AND response_sha256 IS NULL AND validation_outcome IS NULL "
    "AND response_storage_key IS NULL "
    "AND error_code IS NULL AND error_retryable IS NULL) "
    "OR (status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL "
    "AND llm_call_count >= 1 AND llm_call_count <= 2 "
    "AND schema_id IS NOT NULL AND prompt_version IS NOT NULL "
    "AND prompt_sha256 IS NOT NULL AND fact_bundle_sha256 IS NOT NULL "
    "AND prompt_storage_key IS NOT NULL AND fact_bundle_storage_key IS NOT NULL "
    "AND response_sha256 IS NULL AND validation_outcome IS NULL "
    "AND response_storage_key IS NULL "
    "AND error_code IS NULL AND error_retryable IS NULL) "
    "OR (status = 'completed' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
    "AND schema_id IS NOT NULL AND prompt_version IS NOT NULL "
    "AND prompt_sha256 IS NOT NULL AND fact_bundle_sha256 IS NOT NULL "
    "AND prompt_storage_key IS NOT NULL AND fact_bundle_storage_key IS NOT NULL "
    "AND response_storage_key IS NOT NULL AND response_sha256 IS NOT NULL "
    "AND endpoint_profile IS NOT NULL AND endpoint_host IS NOT NULL "
    "AND model_requested IS NOT NULL "
    "AND temperature IS NOT NULL AND input_limit IS NOT NULL "
    "AND output_limit IS NOT NULL AND timeout_seconds IS NOT NULL "
    "AND latency_ms IS NOT NULL AND validation_outcome IS NOT NULL "
    "AND llm_call_count >= 1 AND llm_call_count <= 2 "
    "AND error_code IS NULL AND error_retryable IS NULL) "
    "OR (status = 'policy_blocked' AND started_at IS NULL AND completed_at IS NOT NULL "
    "AND llm_call_count = 0 AND repair_attempted = false "
    "AND response_sha256 IS NULL AND response_storage_key IS NULL "
    "AND validation_outcome IS NOT NULL "
    "AND error_code IS NOT NULL AND error_retryable = false) "
    "OR (status = 'failed' AND completed_at IS NOT NULL "
    "AND error_code IS NOT NULL AND error_retryable IS NOT NULL "
    "AND validation_outcome IS NOT NULL) "
    "OR (status = 'reconciliation_required' AND completed_at IS NOT NULL "
    "AND error_code IS NOT NULL AND error_retryable = false)"
    ")"
)


def upgrade() -> None:
    op.create_table(
        "package_normalization_steps",
        sa.Column("step_id", sa.Uuid(), primary_key=True),
        sa.Column("package_revision_id", sa.Uuid(), nullable=False),
        sa.Column("step_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("fact_bundle_sha256", sa.String(64)),
        sa.Column("schema_id", sa.String(255)),
        sa.Column("prompt_version", sa.String(64)),
        sa.Column("prompt_sha256", sa.String(64)),
        sa.Column("prompt_storage_key", sa.String(512)),
        sa.Column("fact_bundle_storage_key", sa.String(512)),
        sa.Column("response_storage_key", sa.String(512)),
        sa.Column("endpoint_profile", sa.String(32)),
        sa.Column("endpoint_host", sa.String(253)),
        sa.Column("model_requested", sa.String(255)),
        sa.Column("model_reported", sa.String(255)),
        sa.Column("temperature", sa.Numeric(8, 4)),
        sa.Column("input_limit", sa.Integer()),
        sa.Column("output_limit", sa.Integer()),
        sa.Column("timeout_seconds", sa.Numeric(10, 3)),
        sa.Column("attempt", sa.Integer()),
        sa.Column("provider_request_id", sa.String(255)),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("response_sha256", sa.String(64)),
        sa.Column("validation_outcome", sa.String(64)),
        sa.Column(
            "llm_call_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "repair_attempted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("error_code", sa.String(128)),
        sa.Column("error_retryable", sa.Boolean()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            f"status IN ({', '.join(repr(value) for value in _STATUS_VALUES)})",
            name="ck_pkg_norm_steps_status",
        ),
        sa.CheckConstraint(
            f"step_key ~ '{_STEP_KEY_REGEX}'",
            name="ck_pkg_norm_steps_step_key",
        ),
        sa.CheckConstraint(
            f"input_digest ~ '{_SHA256_REGEX}'",
            name="ck_pkg_norm_steps_input_digest",
        ),
        sa.CheckConstraint(
            f"fact_bundle_sha256 IS NULL OR fact_bundle_sha256 ~ '{_SHA256_REGEX}'",
            name="ck_pkg_norm_steps_fact_bundle_sha256",
        ),
        sa.CheckConstraint(
            f"prompt_sha256 IS NULL OR prompt_sha256 ~ '{_SHA256_REGEX}'",
            name="ck_pkg_norm_steps_prompt_sha256",
        ),
        sa.CheckConstraint(
            f"response_sha256 IS NULL OR response_sha256 ~ '{_SHA256_REGEX}'",
            name="ck_pkg_norm_steps_response_sha256",
        ),
        sa.CheckConstraint(
            f"prompt_storage_key IS NULL OR prompt_storage_key ~ '{_PROTECTED_KEY_REGEX}'",
            name="ck_pkg_norm_steps_prompt_storage_key",
        ),
        sa.CheckConstraint(
            f"fact_bundle_storage_key IS NULL OR fact_bundle_storage_key ~ '{_PROTECTED_KEY_REGEX}'",
            name="ck_pkg_norm_steps_fact_bundle_storage_key",
        ),
        sa.CheckConstraint(
            f"response_storage_key IS NULL OR response_storage_key ~ '{_PROTECTED_KEY_REGEX}'",
            name="ck_pkg_norm_steps_response_storage_key",
        ),
        sa.CheckConstraint(
            f"endpoint_profile IS NULL OR endpoint_profile IN ({', '.join(repr(value) for value in _ENDPOINT_PROFILE_VALUES)})",
            name="ck_pkg_norm_steps_endpoint_profile",
        ),
        sa.CheckConstraint(
            f"validation_outcome IS NULL OR validation_outcome ~ '{_VALIDATION_OUTCOME_REGEX}'",
            name="ck_pkg_norm_steps_validation_outcome",
        ),
        sa.CheckConstraint(
            f"error_code IS NULL OR error_code ~ '{_ERROR_CODE_REGEX}'",
            name="ck_pkg_norm_steps_error_code",
        ),
        sa.CheckConstraint(
            "llm_call_count >= 0 AND llm_call_count <= 2",
            name="ck_pkg_norm_steps_llm_call_count_range",
        ),
        sa.CheckConstraint(
            "char_length(schema_id) >= 1 OR schema_id IS NULL",
            name="ck_pkg_norm_steps_schema_id_min_length",
        ),
        sa.CheckConstraint(
            "char_length(prompt_version) >= 1 OR prompt_version IS NULL",
            name="ck_pkg_norm_steps_prompt_version_min_length",
        ),
        sa.CheckConstraint(
            "char_length(endpoint_host) >= 1 OR endpoint_host IS NULL",
            name="ck_pkg_norm_steps_endpoint_host_min_length",
        ),
        sa.CheckConstraint(
            "char_length(model_requested) >= 1 OR model_requested IS NULL",
            name="ck_pkg_norm_steps_model_requested_min_length",
        ),
        sa.CheckConstraint(
            "char_length(model_reported) >= 1 OR model_reported IS NULL",
            name="ck_pkg_norm_steps_model_reported_min_length",
        ),
        sa.CheckConstraint(
            "char_length(provider_request_id) >= 1 OR provider_request_id IS NULL",
            name="ck_pkg_norm_steps_provider_request_id_min_length",
        ),
        sa.CheckConstraint(
            "temperature IS NULL OR temperature >= 0",
            name="ck_pkg_norm_steps_temperature_non_negative",
        ),
        sa.CheckConstraint(
            "input_limit IS NULL OR input_limit >= 1",
            name="ck_pkg_norm_steps_input_limit_positive",
        ),
        sa.CheckConstraint(
            "output_limit IS NULL OR output_limit >= 1",
            name="ck_pkg_norm_steps_output_limit_positive",
        ),
        sa.CheckConstraint(
            "timeout_seconds IS NULL OR timeout_seconds > 0",
            name="ck_pkg_norm_steps_timeout_seconds_positive",
        ),
        sa.CheckConstraint(
            "attempt IS NULL OR attempt >= 1",
            name="ck_pkg_norm_steps_attempt_positive",
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_pkg_norm_steps_input_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_pkg_norm_steps_output_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_pkg_norm_steps_latency_ms_non_negative",
        ),
        sa.CheckConstraint(
            _STATUS_FIELDS_SQL,
            name="ck_pkg_norm_steps_status_fields",
        ),
        sa.CheckConstraint(
            "(repair_attempted = false OR llm_call_count = 2)",
            name="ck_pkg_norm_steps_repair_requires_two_calls",
        ),
        sa.ForeignKeyConstraint(
            ["package_revision_id"],
            ["package_revisions.package_revision_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "package_revision_id",
            "step_key",
            name="uq_pkg_norm_steps_revision_step_key",
        ),
    )
    op.create_index(
        "ix_pkg_norm_steps_package_revision_id",
        "package_normalization_steps",
        ["package_revision_id"],
    )
    op.create_index(
        "ix_pkg_norm_steps_status",
        "package_normalization_steps",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pkg_norm_steps_status",
        table_name="package_normalization_steps",
    )
    op.drop_index(
        "ix_pkg_norm_steps_package_revision_id",
        table_name="package_normalization_steps",
    )
    op.drop_table("package_normalization_steps")
