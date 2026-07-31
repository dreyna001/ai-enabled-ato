"""Add workspace-scoped agency DOCX render persistence."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260728_0016"
down_revision = "20260728_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ssp_agency_docx_renders",
        sa.Column("render_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("profile_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_revision_id", sa.Uuid(), nullable=False),
        sa.Column("source_revision_sha256", sa.String(64), nullable=False),
        sa.Column("template_storage_key", sa.String(67), nullable=False),
        sa.Column("template_sha256", sa.String(64), nullable=False),
        sa.Column("template_filename", sa.String(255), nullable=False),
        sa.Column(
            "mapping_plan",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "review_result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("output_storage_key", sa.String(67), nullable=False),
        sa.Column("output_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_by", sa.String(255), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["ssp_workspaces.workspace_id"],
            ondelete="RESTRICT",
            name="fk_ssp_agency_docx_renders_workspace_id_workspaces",
        ),
        sa.ForeignKeyConstraint(
            ["profile_version_id"],
            ["ssp_profile_versions.profile_version_id"],
            ondelete="RESTRICT",
            name="fk_ssp_agency_docx_renders_profile_version_id_profiles",
        ),
        sa.ForeignKeyConstraint(
            ["source_revision_id"],
            ["ssp_workspace_revisions.revision_id"],
            ondelete="RESTRICT",
            name="fk_ssp_agency_docx_renders_source_revision_id_revisions",
        ),
        sa.ForeignKeyConstraint(
            ["source_revision_id", "workspace_id"],
            [
                "ssp_workspace_revisions.revision_id",
                "ssp_workspace_revisions.workspace_id",
            ],
            ondelete="RESTRICT",
            name="fk_ssp_agency_docx_renders_source_revision_workspace",
        ),
        sa.PrimaryKeyConstraint("render_id", name="pk_ssp_agency_docx_renders"),
        sa.UniqueConstraint(
            "workspace_id",
            "profile_version_id",
            "source_revision_id",
            "template_sha256",
            name="uq_ssp_agency_docx_renders_workspace_profile_revision_template",
        ),
        sa.CheckConstraint(
            "status IN ('awaiting_approval', 'review_failed', 'approved', 'rejected')",
            name="ck_ssp_agency_docx_renders_status",
        ),
        sa.CheckConstraint(
            "source_revision_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_ssp_agency_docx_renders_source_revision_sha256",
        ),
        sa.CheckConstraint(
            "template_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_ssp_agency_docx_renders_template_sha256",
        ),
        sa.CheckConstraint(
            "output_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_ssp_agency_docx_renders_output_sha256",
        ),
        sa.CheckConstraint(
            "template_storage_key ~ '^[a-f0-9]{2}/[a-f0-9]{64}$'",
            name="ck_ssp_agency_docx_renders_template_storage_key",
        ),
        sa.CheckConstraint(
            "output_storage_key ~ '^[a-f0-9]{2}/[a-f0-9]{64}$'",
            name="ck_ssp_agency_docx_renders_output_storage_key",
        ),
        sa.CheckConstraint(
            "split_part(template_storage_key, '/', 2) = template_sha256",
            name="ck_ssp_agency_docx_renders_template_storage_key_matches_sha256",
        ),
        sa.CheckConstraint(
            "split_part(output_storage_key, '/', 2) = output_sha256",
            name="ck_ssp_agency_docx_renders_output_storage_key_matches_sha256",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(mapping_plan) = 'object'",
            name="ck_ssp_agency_docx_renders_mapping_plan_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(review_result) = 'object'",
            name="ck_ssp_agency_docx_renders_review_result_object",
        ),
        sa.CheckConstraint(
            "(status IN ('awaiting_approval', 'review_failed') "
            "AND resolved_by IS NULL AND resolved_at IS NULL) OR "
            "(status IN ('approved', 'rejected') "
            "AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_ssp_agency_docx_renders_status_fields",
        ),
        sa.CheckConstraint(
            "char_length(template_filename) >= 1",
            name="ck_ssp_agency_docx_renders_template_filename_min_length",
        ),
        sa.CheckConstraint(
            "char_length(created_by) >= 1",
            name="ck_ssp_agency_docx_renders_created_by_min_length",
        ),
        sa.CheckConstraint(
            "(resolved_by IS NULL AND resolved_at IS NULL) OR "
            "(resolved_by IS NOT NULL AND resolved_at IS NOT NULL "
            "AND char_length(resolved_by) >= 1)",
            name="ck_ssp_agency_docx_renders_resolution_fields",
        ),
    )
    op.create_index(
        "ix_ssp_agency_docx_renders_workspace_id",
        "ssp_agency_docx_renders",
        ["workspace_id"],
    )
    op.create_index(
        "ix_ssp_agency_docx_renders_workspace_status_created",
        "ssp_agency_docx_renders",
        ["workspace_id", "status", "created_at"],
    )
    op.create_index(
        "ix_ssp_agency_docx_renders_workspace_template_profile",
        "ssp_agency_docx_renders",
        ["workspace_id", "template_sha256", "profile_version_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ssp_agency_docx_renders_workspace_template_profile",
        table_name="ssp_agency_docx_renders",
    )
    op.drop_index(
        "ix_ssp_agency_docx_renders_workspace_status_created",
        table_name="ssp_agency_docx_renders",
    )
    op.drop_index(
        "ix_ssp_agency_docx_renders_workspace_id",
        table_name="ssp_agency_docx_renders",
    )
    op.drop_table("ssp_agency_docx_renders")
