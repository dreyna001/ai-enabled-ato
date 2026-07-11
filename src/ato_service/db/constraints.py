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
