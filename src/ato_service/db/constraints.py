"""Reusable PostgreSQL check-constraint helpers."""

from __future__ import annotations

from sqlalchemy import CheckConstraint


SHA256_REGEX = "^[a-f0-9]{64}$"
STORAGE_KEY_REGEX = "^[a-f0-9]{2}/[a-f0-9]{64}$"
AUTHORITY_MANIFEST_ID_REGEX = "^[a-z0-9][a-z0-9._-]{2,127}$"
ERROR_CODE_REGEX = "^[a-z][a-z0-9_]{2,127}$"
STEP_KEY_REGEX = "^[a-z][a-z0-9_]{1,63}$"
VALIDATION_OUTCOME_REGEX = "^[a-z][a-z0-9_]{1,63}$"
AUDIT_ACTION_REGEX = "^[a-z][a-z0-9_.]{2,127}$"
AUDIT_OBJECT_TYPE_REGEX = "^[a-z][a-z0-9_]{2,63}$"
IDEMPOTENCY_KEY_REGEX = "^[A-Za-z0-9._:-]{16,128}$"
JSON_POINTER_REGEX = "^(/([^~/]|~[01])*)*$"
DOCUMENT_SCHEMA_VERSION_REGEX = "^[0-9]+\\.[0-9]+\\.[0-9]+$"
NORMALIZATION_PROTECTED_KEY_REGEX = (
    "^revisions/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
    "[89ab][0-9a-f]{3}-[0-9a-f]{12}/normalization/"
    "[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/"
    "(prompt|fact-bundle|response)\\.json$"
)

PACKAGE_NORMALIZATION_STEP_STATUS_FIELDS_SQL = (
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


def sha256_check(
    column_name: str,
    *,
    constraint_name: str,
    nullable: bool = False,
) -> CheckConstraint:
    if nullable:
        expression = (
            f"{column_name} IS NULL OR {column_name} ~ '{SHA256_REGEX}'"
        )
    else:
        expression = f"{column_name} ~ '{SHA256_REGEX}'"
    return CheckConstraint(expression, name=constraint_name)


def enum_check(
    column_name: str,
    allowed_values: tuple[str, ...],
    *,
    constraint_name: str,
    nullable: bool = False,
) -> CheckConstraint:
    quoted = ", ".join(f"'{value}'" for value in allowed_values)
    if nullable:
        expression = f"{column_name} IS NULL OR {column_name} IN ({quoted})"
    else:
        expression = f"{column_name} IN ({quoted})"
    return CheckConstraint(expression, name=constraint_name)


def regex_check(
    column_name: str,
    pattern: str,
    *,
    constraint_name: str,
    nullable: bool = False,
) -> CheckConstraint:
    if nullable:
        expression = f"{column_name} IS NULL OR {column_name} ~ '{pattern}'"
    else:
        expression = f"{column_name} ~ '{pattern}'"
    return CheckConstraint(expression, name=constraint_name)
