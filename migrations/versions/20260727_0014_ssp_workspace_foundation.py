"""Add the internal SSP workspace persistence foundation."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260727_0014"
down_revision = "20260717_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ssp_profile_versions",
        sa.Column("profile_version_id", sa.Uuid(), nullable=False),
        sa.Column("profile_key", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("bundle_sha256", sa.String(64), nullable=False),
        sa.Column("bundle", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("imported_by", sa.String(255), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("profile_version_id", name="pk_ssp_profile_versions"),
        sa.UniqueConstraint(
            "profile_key",
            "version",
            name="uq_ssp_profile_versions_profile_key_version",
        ),
        sa.CheckConstraint(
            "status IN ('inactive', 'active', 'archived')",
            name="ck_ssp_profile_versions_status",
        ),
        sa.CheckConstraint(
            "bundle_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_ssp_profile_versions_bundle_sha256",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(bundle) = 'object'",
            name="ck_ssp_profile_versions_bundle_object",
        ),
        sa.CheckConstraint(
            "char_length(profile_key) >= 1",
            name="ck_ssp_profile_versions_profile_key_min_length",
        ),
        sa.CheckConstraint(
            "char_length(version) >= 1",
            name="ck_ssp_profile_versions_version_min_length",
        ),
        sa.CheckConstraint(
            "char_length(imported_by) >= 1",
            name="ck_ssp_profile_versions_imported_by_min_length",
        ),
    )
    op.create_index(
        "ix_ssp_profile_versions_status", "ssp_profile_versions", ["status"]
    )
    op.create_index(
        "uq_ssp_profile_versions_one_active_profile_key",
        "ssp_profile_versions",
        ["profile_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "ssp_workspaces",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("system_id", sa.Uuid(), nullable=False),
        sa.Column("profile_version_id", sa.Uuid(), nullable=False),
        sa.Column("current_revision_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["system_id"],
            ["systems.system_id"],
            ondelete="RESTRICT",
            name="fk_ssp_workspaces_system_id_systems",
        ),
        sa.ForeignKeyConstraint(
            ["profile_version_id"],
            ["ssp_profile_versions.profile_version_id"],
            ondelete="RESTRICT",
            name="fk_ssp_workspaces_profile_version_id_profile",
        ),
        sa.PrimaryKeyConstraint("workspace_id", name="pk_ssp_workspaces"),
        sa.UniqueConstraint("system_id", name="uq_ssp_workspaces_system_id"),
        sa.CheckConstraint(
            "status IN ('working', 'archived')",
            name="ck_ssp_workspaces_status",
        ),
        sa.CheckConstraint(
            "(status = 'working' AND archived_at IS NULL) OR "
            "(status = 'archived' AND archived_at IS NOT NULL)",
            name="ck_ssp_workspaces_archive_fields",
        ),
        sa.CheckConstraint(
            "char_length(created_by) >= 1",
            name="ck_ssp_workspaces_created_by_min_length",
        ),
    )
    op.create_index("ix_ssp_workspaces_system_id", "ssp_workspaces", ["system_id"])
    op.create_index(
        "ix_ssp_workspaces_profile_version_id",
        "ssp_workspaces",
        ["profile_version_id"],
    )

    op.create_table(
        "ssp_workspace_revisions",
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("parent_revision_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["ssp_workspaces.workspace_id"],
            ondelete="RESTRICT",
            name="fk_ssp_workspace_revisions_workspace_id_workspaces",
        ),
        sa.ForeignKeyConstraint(
            ["parent_revision_id"],
            ["ssp_workspace_revisions.revision_id"],
            ondelete="RESTRICT",
            name="fk_ssp_workspace_revisions_parent_revision_id_revisions",
        ),
        sa.PrimaryKeyConstraint(
            "revision_id", name="pk_ssp_workspace_revisions"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "version",
            name="uq_ssp_workspace_revisions_workspace_version",
        ),
        sa.UniqueConstraint(
            "revision_id",
            "workspace_id",
            name="uq_ssp_workspace_revisions_revision_workspace",
        ),
        sa.CheckConstraint(
            "status IN ('working', 'approved', 'superseded')",
            name="ck_ssp_workspace_revisions_status",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_ssp_workspace_revisions_content_sha256",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(content) = 'object'",
            name="ck_ssp_workspace_revisions_content_object",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_ssp_workspace_revisions_version_positive",
        ),
        sa.CheckConstraint(
            "char_length(created_by) >= 1",
            name="ck_ssp_workspace_revisions_created_by_min_length",
        ),
    )
    op.create_index(
        "ix_ssp_workspace_revisions_workspace_id",
        "ssp_workspace_revisions",
        ["workspace_id"],
    )
    op.create_index(
        "uq_ssp_workspace_revisions_one_working",
        "ssp_workspace_revisions",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("status = 'working'"),
    )
    op.create_foreign_key(
        "fk_ssp_workspaces_current_revision_workspace",
        "ssp_workspaces",
        "ssp_workspace_revisions",
        ["current_revision_id", "workspace_id"],
        ["revision_id", "workspace_id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "ssp_evidence_artifacts",
        sa.Column("evidence_artifact_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("source_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("storage_key", sa.String(67), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("display_filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("detected_format", sa.String(32), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "extracted_segments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("uploaded_by", sa.String(255), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(128), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["ssp_workspaces.workspace_id"],
            ondelete="RESTRICT",
            name="fk_ssp_evidence_artifacts_workspace_id_workspaces",
        ),
        sa.ForeignKeyConstraint(
            ["source_artifact_id"],
            ["source_artifacts.artifact_id"],
            ondelete="RESTRICT",
            name="fk_ssp_evidence_artifacts_source_artifact_id_artifacts",
        ),
        sa.PrimaryKeyConstraint(
            "evidence_artifact_id", name="pk_ssp_evidence_artifacts"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "sha256",
            name="uq_ssp_evidence_artifacts_workspace_sha256",
        ),
        sa.CheckConstraint(
            "status IN ('uploaded', 'processing', 'processed', 'failed')",
            name="ck_ssp_evidence_artifacts_status",
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_ssp_evidence_artifacts_sha256",
        ),
        sa.CheckConstraint(
            "storage_key ~ '^[a-f0-9]{2}/[a-f0-9]{64}$'",
            name="ck_ssp_evidence_artifacts_storage_key",
        ),
        sa.CheckConstraint(
            "split_part(storage_key, '/', 2) = sha256",
            name="ck_ssp_evidence_artifacts_storage_key_matches_sha256",
        ),
        sa.CheckConstraint(
            "size_bytes >= 1",
            name="ck_ssp_evidence_artifacts_size_bytes_positive",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(extracted_segments) = 'array'",
            name="ck_ssp_evidence_artifacts_segments_array",
        ),
        sa.CheckConstraint(
            "(status = 'processed' AND processed_at IS NOT NULL "
            "AND failure_code IS NULL) OR "
            "(status = 'failed' AND processed_at IS NOT NULL "
            "AND failure_code IS NOT NULL) OR "
            "(status IN ('uploaded', 'processing') AND processed_at IS NULL "
            "AND failure_code IS NULL)",
            name="ck_ssp_evidence_artifacts_status_fields",
        ),
        sa.CheckConstraint(
            "char_length(display_filename) >= 1",
            name="ck_ssp_evidence_artifacts_filename_min_length",
        ),
        sa.CheckConstraint(
            "char_length(media_type) >= 1",
            name="ck_ssp_evidence_artifacts_media_type_min_length",
        ),
        sa.CheckConstraint(
            "char_length(uploaded_by) >= 1",
            name="ck_ssp_evidence_artifacts_uploaded_by_min_length",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_]{2,127}$'",
            name="ck_ssp_evidence_artifacts_failure_code",
        ),
    )
    op.create_index(
        "ix_ssp_evidence_artifacts_workspace_id",
        "ssp_evidence_artifacts",
        ["workspace_id"],
    )
    op.create_index(
        "ix_ssp_evidence_artifacts_status",
        "ssp_evidence_artifacts",
        ["status"],
    )

    _create_revision_content_tables()

    op.create_table(
        "ssp_agent_patches",
        sa.Column("patch_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("base_revision_id", sa.Uuid(), nullable=False),
        sa.Column("applied_revision_id", sa.Uuid(), nullable=True),
        sa.Column(
            "operations", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("summary", sa.String(2000), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_by", sa.String(255), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["ssp_workspaces.workspace_id"],
            ondelete="RESTRICT",
            name="fk_ssp_agent_patches_workspace_id_workspaces",
        ),
        sa.ForeignKeyConstraint(
            ["base_revision_id"],
            ["ssp_workspace_revisions.revision_id"],
            ondelete="RESTRICT",
            name="fk_ssp_agent_patches_base_revision_id_revisions",
        ),
        sa.ForeignKeyConstraint(
            ["applied_revision_id"],
            ["ssp_workspace_revisions.revision_id"],
            ondelete="RESTRICT",
            name="fk_ssp_agent_patches_applied_revision_id_revisions",
        ),
        sa.PrimaryKeyConstraint("patch_id", name="pk_ssp_agent_patches"),
        sa.CheckConstraint(
            "status IN ('proposed', 'applied', 'rejected', 'stale')",
            name="ck_ssp_agent_patches_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(operations) = 'array' "
            "AND jsonb_array_length(operations) >= 1",
            name="ck_ssp_agent_patches_operations_nonempty_array",
        ),
        sa.CheckConstraint(
            "(status = 'proposed' AND applied_revision_id IS NULL "
            "AND resolved_by IS NULL AND resolved_at IS NULL) OR "
            "(status = 'applied' AND applied_revision_id IS NOT NULL "
            "AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL) OR "
            "(status IN ('rejected', 'stale') AND applied_revision_id IS NULL "
            "AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_ssp_agent_patches_status_fields",
        ),
        sa.CheckConstraint(
            "char_length(summary) >= 1",
            name="ck_ssp_agent_patches_summary_min_length",
        ),
    )
    op.create_index(
        "ix_ssp_agent_patches_workspace_id", "ssp_agent_patches", ["workspace_id"]
    )
    op.create_index(
        "ix_ssp_agent_patches_base_revision_id",
        "ssp_agent_patches",
        ["base_revision_id"],
    )

    op.create_table(
        "ssp_approval_snapshots",
        sa.Column("approval_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("revision_sha256", sa.String(64), nullable=False),
        sa.Column("approved_by", sa.String(255), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["ssp_workspaces.workspace_id"],
            ondelete="RESTRICT",
            name="fk_ssp_approval_snapshots_workspace_id_workspaces",
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["ssp_workspace_revisions.revision_id"],
            ondelete="RESTRICT",
            name="fk_ssp_approval_snapshots_revision_id_revisions",
        ),
        sa.PrimaryKeyConstraint(
            "approval_snapshot_id", name="pk_ssp_approval_snapshots"
        ),
        sa.UniqueConstraint(
            "revision_id", name="uq_ssp_approval_snapshots_revision_id"
        ),
        sa.CheckConstraint(
            "revision_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_ssp_approval_snapshots_revision_sha256",
        ),
        sa.CheckConstraint(
            "char_length(approved_by) >= 1",
            name="ck_ssp_approval_snapshots_approved_by_min_length",
        ),
    )
    op.create_index(
        "ix_ssp_approval_snapshots_workspace_id",
        "ssp_approval_snapshots",
        ["workspace_id"],
    )


def _create_revision_content_tables() -> None:
    op.create_table(
        "ssp_system_facts",
        sa.Column("fact_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("fact_key", sa.String(255), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provenance", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["ssp_workspace_revisions.revision_id"],
            ondelete="RESTRICT",
            name="fk_ssp_system_facts_revision_id_revisions",
        ),
        sa.PrimaryKeyConstraint("fact_id", name="pk_ssp_system_facts"),
        sa.UniqueConstraint(
            "revision_id", "fact_key", name="uq_ssp_system_facts_revision_fact_key"
        ),
        sa.CheckConstraint(
            "provenance IN ('extracted', 'agent_generated', 'isso_entered')",
            name="ck_ssp_system_facts_provenance",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded')",
            name="ck_ssp_system_facts_status",
        ),
        sa.CheckConstraint(
            "char_length(fact_key) >= 1",
            name="ck_ssp_system_facts_fact_key_min_length",
        ),
    )
    op.create_index(
        "ix_ssp_system_facts_revision_id", "ssp_system_facts", ["revision_id"]
    )

    op.create_table(
        "ssp_sections",
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("section_key", sa.String(255), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.String(100000), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["ssp_workspace_revisions.revision_id"],
            ondelete="RESTRICT",
            name="fk_ssp_sections_revision_id_revisions",
        ),
        sa.PrimaryKeyConstraint("section_id", name="pk_ssp_sections"),
        sa.UniqueConstraint(
            "revision_id", "section_key", name="uq_ssp_sections_revision_section_key"
        ),
        sa.CheckConstraint(
            "status IN ('empty', 'generated', 'edited', 'reviewed')",
            name="ck_ssp_sections_status",
        ),
        sa.CheckConstraint(
            "(status = 'empty' AND char_length(trim(content)) = 0) OR "
            "(status <> 'empty' AND char_length(trim(content)) >= 1)",
            name="ck_ssp_sections_status_content",
        ),
        sa.CheckConstraint(
            "char_length(section_key) >= 1",
            name="ck_ssp_sections_section_key_min_length",
        ),
        sa.CheckConstraint(
            "char_length(title) >= 1",
            name="ck_ssp_sections_title_min_length",
        ),
    )
    op.create_index("ix_ssp_sections_revision_id", "ssp_sections", ["revision_id"])

    op.create_table(
        "ssp_control_statements",
        sa.Column("control_statement_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("control_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("implementation_status", sa.String(64), nullable=True),
        sa.Column("implementation_statement", sa.String(100000), nullable=False),
        sa.Column("responsibility", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("unresolved_reason", sa.String(4000), nullable=True),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["ssp_workspace_revisions.revision_id"],
            ondelete="RESTRICT",
            name="fk_ssp_control_statements_revision_id_revisions",
        ),
        sa.PrimaryKeyConstraint(
            "control_statement_id", name="pk_ssp_control_statements"
        ),
        sa.UniqueConstraint(
            "revision_id",
            "control_id",
            name="uq_ssp_control_statements_revision_control_id",
        ),
        sa.CheckConstraint(
            "status IN ('empty', 'generated', 'partial', 'reviewed')",
            name="ck_ssp_control_statements_status",
        ),
        sa.CheckConstraint(
            "(status = 'empty' AND char_length(trim(implementation_statement)) = 0) "
            "OR (status IN ('generated', 'reviewed') "
            "AND char_length(trim(implementation_statement)) >= 1) "
            "OR (status = 'partial' "
            "AND (char_length(trim(implementation_statement)) >= 1 "
            "OR char_length(trim(COALESCE(unresolved_reason, ''))) >= 1))",
            name="ck_ssp_control_statements_status_content",
        ),
        sa.CheckConstraint(
            "char_length(control_id) >= 1",
            name="ck_ssp_control_statements_control_id_min_length",
        ),
        sa.CheckConstraint(
            "char_length(title) >= 1",
            name="ck_ssp_control_statements_title_min_length",
        ),
    )
    op.create_index(
        "ix_ssp_control_statements_revision_id",
        "ssp_control_statements",
        ["revision_id"],
    )
    op.create_index(
        "ix_ssp_control_statements_control_id",
        "ssp_control_statements",
        ["control_id"],
    )

    op.create_table(
        "ssp_questions",
        sa.Column("question_record_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.String(4000), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_key", sa.String(255), nullable=False),
        sa.Column("owner_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("answer", sa.String(20000), nullable=True),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["ssp_workspace_revisions.revision_id"],
            ondelete="RESTRICT",
            name="fk_ssp_questions_revision_id_revisions",
        ),
        sa.PrimaryKeyConstraint("question_record_id", name="pk_ssp_questions"),
        sa.UniqueConstraint(
            "revision_id",
            "question_id",
            name="uq_ssp_questions_revision_question_id",
        ),
        sa.CheckConstraint(
            "target_type IN ('fact', 'ssp_section', 'control')",
            name="ck_ssp_questions_target_type",
        ),
        sa.CheckConstraint(
            "owner_type IN ('agency', 'technical', 'isso', 'system_owner', 'other')",
            name="ck_ssp_questions_owner_type",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'answered', 'dismissed')",
            name="ck_ssp_questions_status",
        ),
        sa.CheckConstraint(
            "(status = 'answered' AND char_length(trim(answer)) >= 1) OR "
            "(status = 'open' AND answer IS NULL) OR status = 'dismissed'",
            name="ck_ssp_questions_status_answer",
        ),
        sa.CheckConstraint(
            "char_length(question) >= 1",
            name="ck_ssp_questions_question_min_length",
        ),
        sa.CheckConstraint(
            "char_length(target_key) >= 1",
            name="ck_ssp_questions_target_key_min_length",
        ),
    )
    op.create_index("ix_ssp_questions_revision_id", "ssp_questions", ["revision_id"])
    op.create_index("ix_ssp_questions_status", "ssp_questions", ["status"])

    op.create_table(
        "ssp_evidence_links",
        sa.Column("evidence_link_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_artifact_id", sa.Uuid(), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_key", sa.String(255), nullable=False),
        sa.Column("locator", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["ssp_workspace_revisions.revision_id"],
            ondelete="RESTRICT",
            name="fk_ssp_evidence_links_revision_id_revisions",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_artifact_id"],
            ["ssp_evidence_artifacts.evidence_artifact_id"],
            ondelete="RESTRICT",
            name="fk_ssp_evidence_links_evidence_artifact_id_artifacts",
        ),
        sa.PrimaryKeyConstraint("evidence_link_id", name="pk_ssp_evidence_links"),
        sa.CheckConstraint(
            "target_type IN ('fact', 'ssp_section', 'control')",
            name="ck_ssp_evidence_links_target_type",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(locator) = 'object' AND locator <> '{}'::jsonb",
            name="ck_ssp_evidence_links_locator_nonempty_object",
        ),
        sa.CheckConstraint(
            "char_length(target_key) >= 1",
            name="ck_ssp_evidence_links_target_key_min_length",
        ),
    )
    op.create_index(
        "ix_ssp_evidence_links_revision_id", "ssp_evidence_links", ["revision_id"]
    )
    op.create_index(
        "ix_ssp_evidence_links_artifact_id",
        "ssp_evidence_links",
        ["evidence_artifact_id"],
    )


def downgrade() -> None:
    op.drop_table("ssp_approval_snapshots")
    op.drop_table("ssp_agent_patches")
    op.drop_table("ssp_evidence_links")
    op.drop_table("ssp_questions")
    op.drop_table("ssp_control_statements")
    op.drop_table("ssp_sections")
    op.drop_table("ssp_system_facts")
    op.drop_table("ssp_evidence_artifacts")
    op.drop_constraint(
        "fk_ssp_workspaces_current_revision_workspace",
        "ssp_workspaces",
        type_="foreignkey",
    )
    op.drop_table("ssp_workspace_revisions")
    op.drop_table("ssp_workspaces")
    op.drop_table("ssp_profile_versions")
