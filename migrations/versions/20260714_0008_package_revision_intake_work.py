"""Add durable package revision intake work and attempt lease tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260714_0008"
down_revision = "20260713_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "package_revision_intake_work",
        sa.Column("package_revision_id", sa.Uuid(), nullable=False),
        sa.Column("work_phase", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(255)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("fence_token", sa.Uuid()),
        sa.Column("expected_revision_version", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(128)),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_package_revision_intake_work_attempt_count_non_negative",
        ),
        sa.CheckConstraint(
            "expected_revision_version >= 1",
            name="ck_pr_intake_work_exp_rev_version_positive",
        ),
        sa.CheckConstraint(
            "last_error_code IS NULL OR last_error_code ~ '^[a-z][a-z0-9_]{2,127}$'",
            name="ck_package_revision_intake_work_last_error_code",
        ),
        sa.CheckConstraint(
            "work_phase IN ('malware_scan', 'deterministic_extract')",
            name="ck_package_revision_intake_work_work_phase",
        ),
        sa.CheckConstraint(
            "status IN ('available', 'leased', 'completed', 'failed', 'reconciliation_required')",
            name="ck_package_revision_intake_work_status",
        ),
        sa.CheckConstraint(
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
        sa.ForeignKeyConstraint(
            ["package_revision_id"],
            ["package_revisions.package_revision_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "package_revision_id",
            "work_phase",
            name="pk_package_revision_intake_work",
        ),
    )
    op.create_index(
        "ix_package_revision_intake_work_status_available_at",
        "package_revision_intake_work",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_package_revision_intake_work_work_phase_status",
        "package_revision_intake_work",
        ["work_phase", "status"],
    )
    op.create_index(
        "ix_package_revision_intake_work_lease_expires_at",
        "package_revision_intake_work",
        ["lease_expires_at"],
    )

    op.create_table(
        "package_revision_intake_attempts",
        sa.Column("attempt_id", sa.Uuid(), primary_key=True),
        sa.Column("package_revision_id", sa.Uuid(), nullable=False),
        sa.Column("work_phase", sa.String(64), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("lease_owner", sa.String(255), nullable=False),
        sa.Column("fence_token", sa.Uuid(), nullable=False),
        sa.Column("expected_revision_version", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(128)),
        sa.Column("error_retryable", sa.Boolean()),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name="ck_package_revision_intake_attempts_attempt_number_positive",
        ),
        sa.CheckConstraint(
            "expected_revision_version >= 1",
            name="ck_pr_intake_attempt_exp_rev_version_positive",
        ),
        sa.CheckConstraint(
            "char_length(lease_owner) >= 1",
            name="ck_package_revision_intake_attempts_lease_owner_min_length",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_]{2,127}$'",
            name="ck_package_revision_intake_attempts_error_code",
        ),
        sa.CheckConstraint(
            "work_phase IN ('malware_scan', 'deterministic_extract')",
            name="ck_package_revision_intake_attempts_work_phase",
        ),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
            "status IN ('active', 'succeeded', 'failed')",
            name="ck_package_revision_intake_attempts_status",
        ),
        sa.ForeignKeyConstraint(
            ["package_revision_id", "work_phase"],
            [
                "package_revision_intake_work.package_revision_id",
                "package_revision_intake_work.work_phase",
            ],
            ondelete="RESTRICT",
            name="fk_package_revision_intake_attempts_work",
        ),
        sa.UniqueConstraint(
            "package_revision_id",
            "work_phase",
            "attempt_number",
            name="uq_pr_intake_attempt_revision_phase_number",
        ),
    )
    op.create_index(
        "ix_package_revision_intake_attempts_revision_phase",
        "package_revision_intake_attempts",
        ["package_revision_id", "work_phase"],
    )
    op.create_index(
        "uq_package_revision_intake_attempts_one_active_per_work",
        "package_revision_intake_attempts",
        ["package_revision_id", "work_phase"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_package_revision_intake_attempts_one_active_per_work",
        table_name="package_revision_intake_attempts",
    )
    op.drop_index(
        "ix_package_revision_intake_attempts_revision_phase",
        table_name="package_revision_intake_attempts",
    )
    op.drop_table("package_revision_intake_attempts")

    op.drop_index(
        "ix_package_revision_intake_work_lease_expires_at",
        table_name="package_revision_intake_work",
    )
    op.drop_index(
        "ix_package_revision_intake_work_work_phase_status",
        table_name="package_revision_intake_work",
    )
    op.drop_index(
        "ix_package_revision_intake_work_status_available_at",
        table_name="package_revision_intake_work",
    )
    op.drop_table("package_revision_intake_work")
