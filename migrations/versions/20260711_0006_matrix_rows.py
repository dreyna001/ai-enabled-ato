"""Add matrix row persistence and requested assessment item ids on analysis runs."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260711_0006"
down_revision = "20260711_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column(
            "assessment_item_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_analysis_runs_assessment_item_ids_array",
        "analysis_runs",
        (
            "assessment_item_ids IS NULL OR "
            "(jsonb_typeof(assessment_item_ids) = 'array' AND "
            "jsonb_array_length(assessment_item_ids) <= 500)"
        ),
    )

    op.create_table(
        "matrix_rows",
        sa.Column("matrix_row_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("assessment_item_type", sa.String(length=32), nullable=False),
        sa.Column("assessment_item_id", sa.String(length=128), nullable=False),
        sa.Column("model_proposed_status", sa.String(length=32), nullable=False),
        sa.Column("system_status", sa.String(length=32), nullable=False),
        sa.Column("finding_summary", sa.String(length=4000), nullable=False),
        sa.Column(
            "gaps",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "assessor_questions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "citations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("context_complete", sa.Boolean(), nullable=False),
        sa.Column("producing_run_id", sa.Uuid(), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs.run_id"],
            ondelete="RESTRICT",
            name="fk_matrix_rows_run_id_analysis_runs",
        ),
        sa.ForeignKeyConstraint(
            ["producing_run_id"],
            ["analysis_runs.run_id"],
            ondelete="RESTRICT",
            name="fk_matrix_rows_producing_run_id_analysis_runs",
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["analysis_runs.run_id"],
            ondelete="RESTRICT",
            name="fk_matrix_rows_source_run_id_analysis_runs",
        ),
        sa.PrimaryKeyConstraint("matrix_row_id", name="pk_matrix_rows"),
        sa.UniqueConstraint(
            "run_id",
            "assessment_item_id",
            name="uq_matrix_rows_run_id_assessment_item_id",
        ),
        sa.CheckConstraint(
            "assessment_item_type IN ('nist_control', 'fedramp_rule', 'fedramp_ksi')",
            name="ck_matrix_rows_assessment_item_type",
        ),
        sa.CheckConstraint(
            "assessment_item_id ~ '^[A-Za-z0-9][A-Za-z0-9()._-]{1,127}$'",
            name="ck_matrix_rows_assessment_item_id",
        ),
        sa.CheckConstraint(
            "model_proposed_status IN "
            "('supported', 'partial', 'unsupported', 'insufficient_evidence')",
            name="ck_matrix_rows_model_proposed_status",
        ),
        sa.CheckConstraint(
            "system_status IN "
            "('supported', 'partial', 'unsupported', 'insufficient_evidence')",
            name="ck_matrix_rows_system_status",
        ),
        sa.CheckConstraint(
            "char_length(finding_summary) >= 1",
            name="ck_matrix_rows_finding_summary_min_length",
        ),
    )
    op.create_index("ix_matrix_rows_run_id", "matrix_rows", ["run_id"])
    op.create_index(
        "ix_matrix_rows_run_id_assessment_item_id",
        "matrix_rows",
        ["run_id", "assessment_item_id"],
    )
    op.create_index(
        "ix_matrix_rows_model_proposed_status",
        "matrix_rows",
        ["model_proposed_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_matrix_rows_model_proposed_status", table_name="matrix_rows")
    op.drop_index("ix_matrix_rows_run_id_assessment_item_id", table_name="matrix_rows")
    op.drop_index("ix_matrix_rows_run_id", table_name="matrix_rows")
    op.drop_table("matrix_rows")
    op.drop_constraint(
        "ck_analysis_runs_assessment_item_ids_array",
        "analysis_runs",
        type_="check",
    )
    op.drop_column("analysis_runs", "assessment_item_ids")
