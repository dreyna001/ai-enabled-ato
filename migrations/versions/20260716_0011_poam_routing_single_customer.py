"""Add customer enterprise boundary and POA&M routing persistence."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260716_0011"
down_revision = "20260715_0010"
branch_labels = None
depends_on = None

_DEFAULT_ENTERPRISE_ID = "dev-local-enterprise"


def upgrade() -> None:
    op.add_column(
        "systems",
        sa.Column(
            "customer_enterprise_id",
            sa.String(length=128),
            nullable=False,
            server_default=_DEFAULT_ENTERPRISE_ID,
        ),
    )
    op.create_check_constraint(
        "ck_systems_customer_enterprise_id_min_length",
        "systems",
        "char_length(customer_enterprise_id) >= 1",
    )
    op.create_index(
        "ix_systems_customer_enterprise_id",
        "systems",
        ["customer_enterprise_id"],
    )
    op.alter_column("systems", "customer_enterprise_id", server_default=None)

    op.create_table(
        "evidence_requests",
        sa.Column("evidence_request_id", sa.Uuid(), nullable=False),
        sa.Column("review_revision_id", sa.Uuid(), nullable=False),
        sa.Column("disposition_id", sa.Uuid(), nullable=False),
        sa.Column("matrix_row_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("assessment_item_id", sa.String(length=128), nullable=False),
        sa.Column("assessment_item_type", sa.String(length=32), nullable=False),
        sa.Column("system_status", sa.String(length=32), nullable=False),
        sa.Column("finding_summary", sa.String(length=4000), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["review_revision_id"],
            ["review_revisions.review_revision_id"],
            ondelete="RESTRICT",
            name="fk_evidence_requests_review_revision_id_review_revisions",
        ),
        sa.ForeignKeyConstraint(
            ["disposition_id"],
            ["dispositions.disposition_id"],
            ondelete="RESTRICT",
            name="fk_evidence_requests_disposition_id_dispositions",
        ),
        sa.ForeignKeyConstraint(
            ["matrix_row_id"],
            ["matrix_rows.matrix_row_id"],
            ondelete="RESTRICT",
            name="fk_evidence_requests_matrix_row_id_matrix_rows",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs.run_id"],
            ondelete="RESTRICT",
            name="fk_evidence_requests_run_id_analysis_runs",
        ),
        sa.PrimaryKeyConstraint("evidence_request_id", name="pk_evidence_requests"),
        sa.UniqueConstraint("disposition_id", name="uq_evidence_requests_disposition_id"),
        sa.UniqueConstraint(
            "review_revision_id",
            "matrix_row_id",
            name="uq_evidence_requests_review_revision_id_matrix_row_id",
        ),
        sa.CheckConstraint(
            "system_status IN ('supported', 'partial', 'unsupported', "
            "'insufficient_evidence')",
            name="ck_evidence_requests_system_status",
        ),
        sa.CheckConstraint(
            "assessment_item_type IN ('nist_control', 'fedramp_rule', 'fedramp_ksi')",
            name="ck_evidence_requests_assessment_item_type",
        ),
    )
    op.create_index(
        "ix_evidence_requests_review_revision_id",
        "evidence_requests",
        ["review_revision_id"],
    )

    op.create_table(
        "poam_candidates",
        sa.Column("poam_candidate_id", sa.Uuid(), nullable=False),
        sa.Column("review_revision_id", sa.Uuid(), nullable=False),
        sa.Column("disposition_id", sa.Uuid(), nullable=False),
        sa.Column("matrix_row_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("assessment_item_id", sa.String(length=128), nullable=False),
        sa.Column("assessment_item_type", sa.String(length=32), nullable=False),
        sa.Column("system_status", sa.String(length=32), nullable=False),
        sa.Column("weakness_summary", sa.String(length=4000), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["review_revision_id"],
            ["review_revisions.review_revision_id"],
            ondelete="RESTRICT",
            name="fk_poam_candidates_review_revision_id_review_revisions",
        ),
        sa.ForeignKeyConstraint(
            ["disposition_id"],
            ["dispositions.disposition_id"],
            ondelete="RESTRICT",
            name="fk_poam_candidates_disposition_id_dispositions",
        ),
        sa.ForeignKeyConstraint(
            ["matrix_row_id"],
            ["matrix_rows.matrix_row_id"],
            ondelete="RESTRICT",
            name="fk_poam_candidates_matrix_row_id_matrix_rows",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs.run_id"],
            ondelete="RESTRICT",
            name="fk_poam_candidates_run_id_analysis_runs",
        ),
        sa.PrimaryKeyConstraint("poam_candidate_id", name="pk_poam_candidates"),
        sa.UniqueConstraint("disposition_id", name="uq_poam_candidates_disposition_id"),
        sa.UniqueConstraint(
            "review_revision_id",
            "matrix_row_id",
            name="uq_poam_candidates_review_revision_id_matrix_row_id",
        ),
        sa.CheckConstraint(
            "system_status IN ('supported', 'partial', 'unsupported', "
            "'insufficient_evidence')",
            name="ck_poam_candidates_system_status",
        ),
        sa.CheckConstraint(
            "assessment_item_type IN ('nist_control', 'fedramp_rule', 'fedramp_ksi')",
            name="ck_poam_candidates_assessment_item_type",
        ),
    )
    op.create_index(
        "ix_poam_candidates_review_revision_id",
        "poam_candidates",
        ["review_revision_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_poam_candidates_review_revision_id", table_name="poam_candidates")
    op.drop_table("poam_candidates")
    op.drop_index(
        "ix_evidence_requests_review_revision_id",
        table_name="evidence_requests",
    )
    op.drop_table("evidence_requests")
    op.drop_index("ix_systems_customer_enterprise_id", table_name="systems")
    op.drop_constraint(
        "ck_systems_customer_enterprise_id_min_length",
        "systems",
        type_="check",
    )
    op.drop_column("systems", "customer_enterprise_id")
