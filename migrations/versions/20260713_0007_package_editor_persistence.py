"""Add package editor draft, sealed content, and system context snapshots."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260713_0007"
down_revision = "20260711_0006"
branch_labels = None
depends_on = None

_FK_PACKAGE_REVISIONS_SYSTEM_CONTEXT_SNAPSHOT = (
    "fk_pkg_rev_system_context_snapshot_id"
)
_FK_SEALED_PACKAGE_CONTENTS_SYSTEM_CONTEXT_SNAPSHOT = (
    "fk_sealed_pkg_system_context_snapshot_id"
)
_FK_PACKAGE_REVISION_DRAFTS_REVISION = "fk_pkg_revision_drafts_revision_id"
_FK_SEALED_PACKAGE_CONTENTS_REVISION = "fk_sealed_pkg_contents_revision_id"


def upgrade() -> None:
    op.create_table(
        "system_context_snapshots",
        sa.Column("system_context_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("system_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "document",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["system_id"],
            ["systems.system_id"],
            ondelete="RESTRICT",
            name="fk_system_context_snapshots_system_id_systems",
        ),
        sa.PrimaryKeyConstraint(
            "system_context_snapshot_id",
            name="pk_system_context_snapshots",
        ),
        sa.UniqueConstraint(
            "system_id",
            "version",
            name="uq_system_context_snapshots_system_id_version",
        ),
        sa.CheckConstraint("version >= 1", name="ck_system_context_snapshots_version_positive"),
        sa.CheckConstraint(
            "content_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_system_context_snapshots_content_sha256",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(document) = 'object'",
            name="ck_system_context_snapshots_document_object",
        ),
        sa.CheckConstraint(
            "char_length(created_by) >= 1",
            name="ck_system_context_snapshots_created_by_min_length",
        ),
    )
    op.create_index(
        "ix_system_context_snapshots_system_id",
        "system_context_snapshots",
        ["system_id"],
    )

    op.add_column(
        "package_revisions",
        sa.Column("package_content_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "package_revisions",
        sa.Column("system_context_snapshot_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        _FK_PACKAGE_REVISIONS_SYSTEM_CONTEXT_SNAPSHOT,
        "package_revisions",
        "system_context_snapshots",
        ["system_context_snapshot_id"],
        ["system_context_snapshot_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_package_revisions_package_content_sha256",
        "package_revisions",
        (
            "package_content_sha256 IS NULL OR "
            "package_content_sha256 ~ '^[a-f0-9]{64}$'"
        ),
    )
    op.create_index(
        "ix_package_revisions_system_context_snapshot_id",
        "package_revisions",
        ["system_context_snapshot_id"],
    )

    op.create_table(
        "package_revision_drafts",
        sa.Column("package_revision_id", sa.Uuid(), nullable=False),
        sa.Column("document_schema_version", sa.String(length=32), nullable=False),
        sa.Column(
            "document",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "field_provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["package_revision_id"],
            ["package_revisions.package_revision_id"],
            ondelete="RESTRICT",
            name=_FK_PACKAGE_REVISION_DRAFTS_REVISION,
        ),
        sa.PrimaryKeyConstraint(
            "package_revision_id",
            name="pk_package_revision_drafts",
        ),
        sa.CheckConstraint(
            "document_schema_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'",
            name="ck_package_revision_drafts_document_schema_version",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(document) = 'object'",
            name="ck_package_revision_drafts_document_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(field_provenance) = 'object'",
            name="ck_package_revision_drafts_field_provenance_object",
        ),
        sa.CheckConstraint(
            "char_length(updated_by) >= 1",
            name="ck_package_revision_drafts_updated_by_min_length",
        ),
    )

    op.create_table(
        "sealed_package_contents",
        sa.Column("package_revision_id", sa.Uuid(), nullable=False),
        sa.Column("document_schema_version", sa.String(length=32), nullable=False),
        sa.Column(
            "document",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "field_provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("system_context_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("sealed_by", sa.String(length=255), nullable=False),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["package_revision_id"],
            ["package_revisions.package_revision_id"],
            ondelete="RESTRICT",
            name=_FK_SEALED_PACKAGE_CONTENTS_REVISION,
        ),
        sa.ForeignKeyConstraint(
            ["system_context_snapshot_id"],
            ["system_context_snapshots.system_context_snapshot_id"],
            ondelete="RESTRICT",
            name=_FK_SEALED_PACKAGE_CONTENTS_SYSTEM_CONTEXT_SNAPSHOT,
        ),
        sa.PrimaryKeyConstraint(
            "package_revision_id",
            name="pk_sealed_package_contents",
        ),
        sa.CheckConstraint(
            "document_schema_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'",
            name="ck_sealed_package_contents_document_schema_version",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_sealed_package_contents_content_sha256",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(document) = 'object'",
            name="ck_sealed_package_contents_document_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(field_provenance) = 'object'",
            name="ck_sealed_package_contents_field_provenance_object",
        ),
        sa.CheckConstraint(
            "char_length(sealed_by) >= 1",
            name="ck_sealed_package_contents_sealed_by_min_length",
        ),
    )


def downgrade() -> None:
    op.drop_table("sealed_package_contents")
    op.drop_table("package_revision_drafts")
    op.drop_index(
        "ix_package_revisions_system_context_snapshot_id",
        table_name="package_revisions",
    )
    op.drop_constraint(
        "ck_package_revisions_package_content_sha256",
        "package_revisions",
        type_="check",
    )
    op.drop_constraint(
        _FK_PACKAGE_REVISIONS_SYSTEM_CONTEXT_SNAPSHOT,
        "package_revisions",
        type_="foreignkey",
    )
    op.drop_column("package_revisions", "system_context_snapshot_id")
    op.drop_column("package_revisions", "package_content_sha256")
    op.drop_index(
        "ix_system_context_snapshots_system_id",
        table_name="system_context_snapshots",
    )
    op.drop_table("system_context_snapshots")
