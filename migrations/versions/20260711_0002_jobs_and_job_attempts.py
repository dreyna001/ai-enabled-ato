"""Add durable analyzer job queue and attempt tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260711_0002"
down_revision = "20260710_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("step_key", sa.String(64), nullable=False),
        sa.Column("step_idempotent", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(255)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(128)),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_jobs_attempt_count_non_negative",
        ),
        sa.CheckConstraint(
            "last_error_code IS NULL OR last_error_code ~ '^[a-z][a-z0-9_]{2,127}$'",
            name="ck_jobs_last_error_code",
        ),
        sa.CheckConstraint(
            "("
            "(status = 'leased' AND lease_owner IS NOT NULL "
            "AND char_length(lease_owner) >= 1 "
            "AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL) "
            "OR (status IN ('available', 'completed', 'failed', 'reconciliation_required') "
            "AND lease_owner IS NULL AND lease_expires_at IS NULL AND heartbeat_at IS NULL)"
            ")",
            name="ck_jobs_lease_fields_match_status",
        ),
        sa.CheckConstraint(
            "status IN ('available', 'leased', 'completed', 'failed', 'reconciliation_required')",
            name="ck_jobs_status",
        ),
        sa.CheckConstraint(
            "step_key ~ '^[a-z][a-z0-9_]{1,63}$'",
            name="ck_jobs_step_key",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.run_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("job_id", "run_id", "step_key", name="uq_jobs_job_id_run_id_step_key"),
        sa.UniqueConstraint("run_id", "step_key", name="uq_jobs_run_id_step_key"),
    )
    op.create_index("ix_jobs_run_id", "jobs", ["run_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_status_available_at", "jobs", ["status", "available_at"])
    op.create_index("ix_jobs_lease_expires_at", "jobs", ["lease_expires_at"])

    op.create_table(
        "job_attempts",
        sa.Column("attempt_id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("step_key", sa.String(64), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("lease_owner", sa.String(255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(128)),
        sa.Column("error_retryable", sa.Boolean()),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name="ck_job_attempts_attempt_number_positive",
        ),
        sa.CheckConstraint(
            "char_length(lease_owner) >= 1",
            name="ck_job_attempts_lease_owner_min_length",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_]{2,127}$'",
            name="ck_job_attempts_error_code",
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
            name="ck_job_attempts_status_fields",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'succeeded', 'failed')",
            name="ck_job_attempts_status",
        ),
        sa.CheckConstraint(
            "step_key ~ '^[a-z][a-z0-9_]{1,63}$'",
            name="ck_job_attempts_step_key",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "run_id", "step_key"],
            ["jobs.job_id", "jobs.run_id", "jobs.step_key"],
            ondelete="RESTRICT",
            name="fk_job_attempts_jobs_job_id_run_id_step_key",
        ),
        sa.UniqueConstraint(
            "job_id",
            "attempt_number",
            name="uq_job_attempts_job_id_attempt_number",
        ),
    )
    op.create_index("ix_job_attempts_job_id", "job_attempts", ["job_id"])
    op.create_index(
        "uq_job_attempts_one_active_per_job",
        "job_attempts",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_job_attempts_one_active_per_job", table_name="job_attempts")
    op.drop_index("ix_job_attempts_job_id", table_name="job_attempts")
    op.drop_table("job_attempts")

    op.drop_index("ix_jobs_lease_expires_at", table_name="jobs")
    op.drop_index("ix_jobs_status_available_at", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_run_id", table_name="jobs")
    op.drop_table("jobs")
