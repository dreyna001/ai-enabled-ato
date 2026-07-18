"""Defer package revision metadata until post-upload human PATCH."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260717_0013"
down_revision = "20260717_0012"
branch_labels = None
depends_on = None

_PROFILE_ID_VALUES = (
    "fedramp_20x_program",
    "fedramp_rev5_transition",
    "fisma_agency_security",
)
_DATA_ORIGIN_VALUES = (
    "synthetic",
    "redacted_nonproduction",
    "customer_production",
)
_SENSITIVITY_VALUES = (
    "public",
    "internal_unclassified",
    "customer_sensitive",
    "cui",
    "classified",
    "unknown",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.drop_constraint(
        "ck_package_revisions_profile_id",
        "package_revisions",
        type_="check",
    )
    op.drop_constraint(
        "ck_package_revisions_data_origin",
        "package_revisions",
        type_="check",
    )
    op.drop_constraint(
        "ck_package_revisions_sensitivity",
        "package_revisions",
        type_="check",
    )

    op.alter_column("package_revisions", "profile_id", existing_type=sa.String(64), nullable=True)
    op.alter_column(
        "package_revisions",
        "data_origin",
        existing_type=sa.String(64),
        nullable=True,
    )
    op.alter_column(
        "package_revisions",
        "sensitivity",
        existing_type=sa.String(64),
        nullable=True,
    )

    op.create_check_constraint(
        "ck_package_revisions_profile_id",
        "package_revisions",
        f"profile_id IS NULL OR profile_id IN ({_quoted(_PROFILE_ID_VALUES)})",
    )
    op.create_check_constraint(
        "ck_package_revisions_data_origin",
        "package_revisions",
        f"data_origin IS NULL OR data_origin IN ({_quoted(_DATA_ORIGIN_VALUES)})",
    )
    op.create_check_constraint(
        "ck_package_revisions_sensitivity",
        "package_revisions",
        f"sensitivity IS NULL OR sensitivity IN ({_quoted(_SENSITIVITY_VALUES)})",
    )
    op.create_check_constraint(
        "ck_package_revisions_ready_requires_complete_metadata",
        "package_revisions",
        "status <> 'ready' OR ("
        "profile_id IS NOT NULL "
        "AND data_origin IS NOT NULL "
        "AND sensitivity IS NOT NULL "
        "AND jsonb_array_length(effective_data_labels) >= 2"
        ")",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_package_revisions_ready_requires_complete_metadata",
        "package_revisions",
        type_="check",
    )
    op.drop_constraint(
        "ck_package_revisions_sensitivity",
        "package_revisions",
        type_="check",
    )
    op.drop_constraint(
        "ck_package_revisions_data_origin",
        "package_revisions",
        type_="check",
    )
    op.drop_constraint(
        "ck_package_revisions_profile_id",
        "package_revisions",
        type_="check",
    )

    op.execute(
        sa.text(
            "UPDATE package_revisions SET "
            "profile_id = COALESCE(profile_id, 'fisma_agency_security'), "
            "data_origin = COALESCE(data_origin, 'synthetic'), "
            "sensitivity = COALESCE(sensitivity, 'internal_unclassified'), "
            "effective_data_labels = CASE "
            "WHEN jsonb_array_length(effective_data_labels) >= 2 "
            "THEN effective_data_labels "
            "ELSE to_jsonb(ARRAY['internal_unclassified', 'synthetic']::text[]) "
            "END"
        )
    )

    op.alter_column(
        "package_revisions",
        "sensitivity",
        existing_type=sa.String(64),
        nullable=False,
    )
    op.alter_column(
        "package_revisions",
        "data_origin",
        existing_type=sa.String(64),
        nullable=False,
    )
    op.alter_column("package_revisions", "profile_id", existing_type=sa.String(64), nullable=False)

    op.create_check_constraint(
        "ck_package_revisions_profile_id",
        "package_revisions",
        f"profile_id IN ({_quoted(_PROFILE_ID_VALUES)})",
    )
    op.create_check_constraint(
        "ck_package_revisions_data_origin",
        "package_revisions",
        f"data_origin IN ({_quoted(_DATA_ORIGIN_VALUES)})",
    )
    op.create_check_constraint(
        "ck_package_revisions_sensitivity",
        "package_revisions",
        f"sensitivity IN ({_quoted(_SENSITIVITY_VALUES)})",
    )
