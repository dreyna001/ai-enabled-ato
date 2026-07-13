"""Add review, export, and authorization decision persistence."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260715_0010"
down_revision = "20260714_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "authorization_decision_records",
        sa.Column("authorization_decision_id", sa.Uuid(), nullable=False),
        sa.Column("system_id", sa.Uuid(), nullable=False),
        sa.Column("package_revision_id", sa.Uuid(), nullable=True),
        sa.Column("decision_type", sa.String(length=64), nullable=False),
        sa.Column("decision_date", sa.String(length=32), nullable=False),
        sa.Column("issuing_authority", sa.String(length=255), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=True),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("attached_by", sa.String(length=255), nullable=False),
        sa.Column("attached_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["system_id"],
            ["systems.system_id"],
            ondelete="RESTRICT",
            name="fk_auth_decision_records_system_id_systems",
        ),
        sa.ForeignKeyConstraint(
            ["package_revision_id"],
            ["package_revisions.package_revision_id"],
            ondelete="RESTRICT",
            name="fk_auth_decision_records_package_revision_id_package_revisions",
        ),
        sa.PrimaryKeyConstraint(
            "authorization_decision_id",
            name="pk_authorization_decision_records",
        ),
    )
    op.create_index(
        "ix_auth_decision_records_system_id",
        "authorization_decision_records",
        ["system_id"],
    )

    op.create_table(
        "review_revisions",
        sa.Column("review_revision_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs.run_id"],
            ondelete="RESTRICT",
            name="fk_review_revisions_run_id_analysis_runs",
        ),
        sa.PrimaryKeyConstraint("review_revision_id", name="pk_review_revisions"),
        sa.UniqueConstraint("run_id", "version", name="uq_review_revisions_run_id_version"),
        sa.CheckConstraint("version >= 1", name="ck_review_revisions_version_positive"),
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'superseded')",
            name="ck_review_revisions_status",
        ),
    )
    op.create_index("ix_review_revisions_run_id", "review_revisions", ["run_id"])

    op.create_table(
        "dispositions",
        sa.Column("disposition_id", sa.Uuid(), nullable=False),
        sa.Column("review_revision_id", sa.Uuid(), nullable=False),
        sa.Column("matrix_row_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("edited_summary", sa.String(length=4000), nullable=True),
        sa.Column("notes", sa.String(length=4000), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("decided_by", sa.String(length=255), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["review_revision_id"],
            ["review_revisions.review_revision_id"],
            ondelete="RESTRICT",
            name="fk_dispositions_review_revision_id_review_revisions",
        ),
        sa.ForeignKeyConstraint(
            ["matrix_row_id"],
            ["matrix_rows.matrix_row_id"],
            ondelete="RESTRICT",
            name="fk_dispositions_matrix_row_id_matrix_rows",
        ),
        sa.PrimaryKeyConstraint("disposition_id", name="pk_dispositions"),
        sa.UniqueConstraint(
            "review_revision_id",
            "matrix_row_id",
            name="uq_dispositions_review_revision_id_matrix_row_id",
        ),
        sa.CheckConstraint("version >= 1", name="ck_dispositions_version_positive"),
        sa.CheckConstraint(
            "decision IN ('pending', 'accepted', 'edited', 'rejected', "
            "'evidence_requested', 'weakness_confirmed')",
            name="ck_dispositions_decision",
        ),
    )
    op.create_index(
        "ix_dispositions_review_revision_id",
        "dispositions",
        ["review_revision_id"],
    )

    op.create_table(
        "review_comments",
        sa.Column("review_comment_id", sa.Uuid(), nullable=False),
        sa.Column("review_revision_id", sa.Uuid(), nullable=False),
        sa.Column("matrix_row_id", sa.Uuid(), nullable=True),
        sa.Column("body", sa.String(length=4000), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["review_revision_id"],
            ["review_revisions.review_revision_id"],
            ondelete="RESTRICT",
            name="fk_review_comments_review_revision_id_review_revisions",
        ),
        sa.PrimaryKeyConstraint("review_comment_id", name="pk_review_comments"),
        sa.CheckConstraint(
            "char_length(body) >= 1",
            name="ck_review_comments_body_min_length",
        ),
    )
    op.create_index(
        "ix_review_comments_review_revision_id",
        "review_comments",
        ["review_revision_id"],
    )

    op.create_table(
        "export_drafts",
        sa.Column("export_draft_id", sa.Uuid(), nullable=False),
        sa.Column("review_revision_id", sa.Uuid(), nullable=False),
        sa.Column("payload_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("destination_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["review_revision_id"],
            ["review_revisions.review_revision_id"],
            ondelete="RESTRICT",
            name="fk_export_drafts_review_revision_id_review_revisions",
        ),
        sa.PrimaryKeyConstraint("export_draft_id", name="pk_export_drafts"),
        sa.CheckConstraint(
            "payload_manifest_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_export_drafts_payload_manifest_sha256",
        ),
        sa.CheckConstraint(
            "destination_type = 'download'",
            name="ck_export_drafts_destination_type_download",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_approval', 'approved', 'rejected', "
            "'expired', 'superseded', 'exported')",
            name="ck_export_drafts_status",
        ),
    )
    op.create_index(
        "ix_export_drafts_review_revision_id",
        "export_drafts",
        ["review_revision_id"],
    )

    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("export_draft_id", sa.Uuid(), nullable=False),
        sa.Column("payload_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("submitted_by", sa.String(length=255), nullable=False),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=2000), nullable=True),
        sa.ForeignKeyConstraint(
            ["export_draft_id"],
            ["export_drafts.export_draft_id"],
            ondelete="RESTRICT",
            name="fk_approvals_export_draft_id_export_drafts",
        ),
        sa.PrimaryKeyConstraint("approval_id", name="pk_approvals"),
        sa.UniqueConstraint("export_draft_id", name="uq_approvals_export_draft_id"),
        sa.CheckConstraint(
            "payload_manifest_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_approvals_payload_manifest_sha256",
        ),
        sa.CheckConstraint(
            "decision IN ('pending', 'approved', 'rejected')",
            name="ck_approvals_decision",
        ),
    )
    op.create_index("ix_approvals_export_draft_id", "approvals", ["export_draft_id"])

    op.create_table(
        "exports",
        sa.Column("export_id", sa.Uuid(), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("system_id", sa.Uuid(), nullable=False),
        sa.Column("package_revision_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("review_revision_id", sa.Uuid(), nullable=False),
        sa.Column("payload_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["approvals.approval_id"],
            ondelete="RESTRICT",
            name="fk_exports_approval_id_approvals",
        ),
        sa.PrimaryKeyConstraint("export_id", name="pk_exports"),
        sa.UniqueConstraint("approval_id", name="uq_exports_approval_id"),
        sa.CheckConstraint(
            "payload_manifest_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_exports_payload_manifest_sha256",
        ),
    )
    op.create_index("ix_exports_approval_id", "exports", ["approval_id"])

    op.execute(
        "ALTER TABLE source_artifacts DROP CONSTRAINT IF EXISTS ck_source_artifacts_artifact_kind"
    )
    op.create_check_constraint(
        "ck_source_artifacts_artifact_kind",
        "source_artifacts",
        "artifact_kind IN ('manifest', 'fedramp_cpo', 'fedramp_sdr', 'fedramp_ocr', "
        "'fedramp_scg', 'oscal', 'evidence_document', 'scanner_export', 'architecture', "
        "'attestation', 'reference_catalog', 'privacy_artifact')",
    )


def downgrade() -> None:
    op.drop_table("exports")
    op.drop_table("approvals")
    op.drop_table("export_drafts")
    op.drop_table("review_comments")
    op.drop_table("dispositions")
    op.drop_table("review_revisions")
    op.drop_table("authorization_decision_records")
    op.execute(
        "ALTER TABLE source_artifacts DROP CONSTRAINT IF EXISTS ck_source_artifacts_artifact_kind"
    )
    op.create_check_constraint(
        "ck_source_artifacts_artifact_kind",
        "source_artifacts",
        "artifact_kind IN ('manifest', 'fedramp_cpo', 'fedramp_sdr', 'fedramp_ocr', "
        "'fedramp_scg', 'oscal', 'evidence_document', 'scanner_export', 'architecture', "
        "'attestation', 'reference_catalog')",
    )
