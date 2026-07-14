"""Add revision-scoped PostgreSQL full-text search and chat usage persistence."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260717_0012"
down_revision = "20260716_0011"
branch_labels = None
depends_on = None

_SHA256_REGEX = "^[a-f0-9]{64}$"
_FK_PACKAGE_REVISION_SEARCH_INDEXES_REVISION = (
    "fk_pkg_search_indexes_revision_id"
)


def upgrade() -> None:
    op.create_table(
        "package_revision_search_chunks",
        sa.Column("chunk_id", sa.String(length=64), nullable=False),
        sa.Column("package_revision_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("normalized_start", sa.Integer(), nullable=False),
        sa.Column("normalized_end", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(length=6000), nullable=False),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=False),
        sa.CheckConstraint(
            f"chunk_id ~ '{_SHA256_REGEX}'",
            name="ck_package_revision_search_chunks_chunk_id",
        ),
        sa.CheckConstraint(
            f"artifact_sha256 ~ '{_SHA256_REGEX}'",
            name="ck_package_revision_search_chunks_artifact_sha256",
        ),
        sa.CheckConstraint(
            "normalized_start >= 0",
            name="ck_package_revision_search_chunks_normalized_start_nonnegative",
        ),
        sa.CheckConstraint(
            "normalized_end > normalized_start",
            name="ck_package_revision_search_chunks_normalized_end_positive",
        ),
        sa.CheckConstraint(
            "char_length(text) >= 1 AND char_length(text) <= 6000",
            name="ck_package_revision_search_chunks_text_length",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["source_artifacts.artifact_id"],
            name="fk_package_revision_search_chunks_artifact_id_source_artifacts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["package_revision_id"],
            ["package_revisions.package_revision_id"],
            name="fk_package_revision_search_chunks_revision_id_package_revisions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("chunk_id", name="pk_package_revision_search_chunks"),
    )
    op.create_index(
        "ix_package_revision_search_chunks_revision_rank",
        "package_revision_search_chunks",
        ["package_revision_id", "chunk_id"],
    )
    op.create_index(
        "ix_package_revision_search_chunks_search_vector",
        "package_revision_search_chunks",
        ["search_vector"],
        postgresql_using="gin",
    )

    op.create_table(
        "package_revision_search_indexes",
        sa.Column("package_revision_id", sa.Uuid(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"content_sha256 ~ '{_SHA256_REGEX}'",
            name="ck_package_revision_search_indexes_content_sha256",
        ),
        sa.CheckConstraint(
            "chunk_count >= 0",
            name="ck_package_revision_search_indexes_chunk_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["package_revision_id"],
            ["package_revisions.package_revision_id"],
            name=_FK_PACKAGE_REVISION_SEARCH_INDEXES_REVISION,
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "package_revision_id",
            name="pk_package_revision_search_indexes",
        ),
    )

    op.create_table(
        "package_revision_chat_usage",
        sa.Column("package_revision_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("rate_window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rate_window_count", sa.Integer(), nullable=False),
        sa.Column("turn_count", sa.Integer(), nullable=False),
        sa.Column("daily_token_count", sa.Integer(), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "char_length(actor_id) >= 1",
            name="ck_package_revision_chat_usage_actor_id_min_length",
        ),
        sa.CheckConstraint(
            "rate_window_count >= 0",
            name="ck_package_revision_chat_usage_rate_window_count_nonnegative",
        ),
        sa.CheckConstraint(
            "turn_count >= 0",
            name="ck_package_revision_chat_usage_turn_count_nonnegative",
        ),
        sa.CheckConstraint(
            "daily_token_count >= 0",
            name="ck_package_revision_chat_usage_daily_token_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["package_revision_id"],
            ["package_revisions.package_revision_id"],
            name="fk_package_revision_chat_usage_revision_id_package_revisions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "package_revision_id",
            "actor_id",
            name="pk_package_revision_chat_usage",
        ),
    )
    op.create_index(
        "ix_package_revision_chat_usage_revision_actor",
        "package_revision_chat_usage",
        ["package_revision_id", "actor_id"],
    )


def downgrade() -> None:
    op.drop_table("package_revision_chat_usage")
    op.drop_table("package_revision_search_indexes")
    op.drop_index(
        "ix_package_revision_search_chunks_search_vector",
        table_name="package_revision_search_chunks",
        postgresql_using="gin",
    )
    op.drop_index(
        "ix_package_revision_search_chunks_revision_rank",
        table_name="package_revision_search_chunks",
    )
    op.drop_table("package_revision_search_chunks")
