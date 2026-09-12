"""Add private, revision-aware SSP chat persistence."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260911_0017"
down_revision = "20260728_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ssp_chat_conversations",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("rate_window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rate_window_count", sa.Integer(), nullable=False),
        sa.Column("daily_token_count", sa.Integer(), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["ssp_workspaces.workspace_id"],
            ondelete="RESTRICT",
            name="fk_ssp_chat_conversations_workspace_id_workspaces",
        ),
        sa.PrimaryKeyConstraint(
            "conversation_id",
            name="pk_ssp_chat_conversations",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "actor_id",
            name="uq_ssp_chat_conversations_workspace_actor",
        ),
        sa.CheckConstraint(
            "last_sequence >= 0",
            name="ck_ssp_chat_conversations_last_sequence_nonnegative",
        ),
        sa.CheckConstraint(
            "rate_window_count >= 0",
            name="ck_ssp_chat_conversations_rate_window_count_nonnegative",
        ),
        sa.CheckConstraint(
            "daily_token_count >= 0",
            name="ck_ssp_chat_conversations_daily_token_count_nonnegative",
        ),
        sa.CheckConstraint(
            "char_length(actor_id) >= 1",
            name="ck_ssp_chat_conversations_actor_id_min_length",
        ),
    )
    op.create_index(
        "ix_ssp_chat_conversations_workspace_id",
        "ssp_chat_conversations",
        ["workspace_id"],
    )
    op.create_index(
        "ix_ssp_chat_conversations_workspace_actor",
        "ssp_chat_conversations",
        ["workspace_id", "actor_id"],
    )

    op.create_table(
        "ssp_chat_turns",
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("expected_revision_id", sa.Uuid(), nullable=False),
        sa.Column("context_fingerprint", sa.String(64), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["ssp_chat_conversations.conversation_id"],
            ondelete="RESTRICT",
            name="fk_ssp_chat_turns_conversation_id_conversations",
        ),
        sa.ForeignKeyConstraint(
            ["expected_revision_id"],
            ["ssp_workspace_revisions.revision_id"],
            ondelete="RESTRICT",
            name="fk_ssp_chat_turns_expected_revision_id_revisions",
        ),
        sa.PrimaryKeyConstraint("turn_id", name="pk_ssp_chat_turns"),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_ssp_chat_turns_conversation_sequence",
        ),
        sa.UniqueConstraint(
            "turn_id",
            "conversation_id",
            "sequence",
            name="uq_ssp_chat_turns_turn_conversation_sequence",
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "request_id",
            name="uq_ssp_chat_turns_conversation_request_id",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'completed')",
            name="ck_ssp_chat_turns_status",
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[a-f0-9]{64}$'",
            name="ck_ssp_chat_turns_request_fingerprint",
        ),
        sa.CheckConstraint(
            "context_fingerprint ~ '^[a-f0-9]{64}$'",
            name="ck_ssp_chat_turns_context_fingerprint",
        ),
        sa.CheckConstraint(
            "sequence >= 1",
            name="ck_ssp_chat_turns_sequence_positive",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND completed_at IS NULL AND expires_at IS NULL "
            "AND lease_expires_at IS NOT NULL AND lease_expires_at > created_at) OR "
            "(status = 'completed' AND completed_at IS NOT NULL "
            "AND expires_at IS NOT NULL AND expires_at > completed_at "
            "AND lease_expires_at IS NULL)",
            name="ck_ssp_chat_turns_status_fields",
        ),
    )
    op.create_index(
        "ix_ssp_chat_turns_conversation_sequence",
        "ssp_chat_turns",
        ["conversation_id", "sequence"],
    )
    op.create_index(
        "uq_ssp_chat_turns_one_pending_conversation",
        "ssp_chat_turns",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_ssp_chat_turns_expires_at",
        "ssp_chat_turns",
        ["expires_at"],
    )

    op.create_table(
        "ssp_chat_messages",
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.String(12_000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "sources",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("context_fingerprint", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["ssp_chat_conversations.conversation_id"],
            ondelete="RESTRICT",
            name="fk_ssp_chat_messages_conversation_id_conversations",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id", "conversation_id", "sequence"],
            [
                "ssp_chat_turns.turn_id",
                "ssp_chat_turns.conversation_id",
                "ssp_chat_turns.sequence",
            ],
            ondelete="RESTRICT",
            name="fk_ssp_chat_messages_turn_conversation_sequence",
        ),
        sa.PrimaryKeyConstraint("message_id", name="pk_ssp_chat_messages"),
        sa.UniqueConstraint(
            "turn_id",
            "role",
            name="uq_ssp_chat_messages_turn_role",
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence",
            "role",
            name="uq_ssp_chat_messages_conversation_sequence_role",
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_ssp_chat_messages_role",
        ),
        sa.CheckConstraint(
            "context_fingerprint ~ '^[a-f0-9]{64}$'",
            name="ck_ssp_chat_messages_context_fingerprint",
        ),
        sa.CheckConstraint(
            "sequence >= 1",
            name="ck_ssp_chat_messages_sequence_positive",
        ),
        sa.CheckConstraint(
            "char_length(trim(content)) >= 1",
            name="ck_ssp_chat_messages_content_min_length",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(sources) = 'array'",
            name="ck_ssp_chat_messages_sources_array",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_ssp_chat_messages_expiry_after_creation",
        ),
    )
    op.create_index(
        "ix_ssp_chat_messages_conversation_sequence",
        "ssp_chat_messages",
        ["conversation_id", "sequence"],
    )
    op.create_index(
        "ix_ssp_chat_messages_expires_at",
        "ssp_chat_messages",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ssp_chat_messages_expires_at",
        table_name="ssp_chat_messages",
    )
    op.drop_index(
        "ix_ssp_chat_messages_conversation_sequence",
        table_name="ssp_chat_messages",
    )
    op.drop_table("ssp_chat_messages")
    op.drop_index("ix_ssp_chat_turns_expires_at", table_name="ssp_chat_turns")
    op.drop_index(
        "uq_ssp_chat_turns_one_pending_conversation",
        table_name="ssp_chat_turns",
    )
    op.drop_index(
        "ix_ssp_chat_turns_conversation_sequence",
        table_name="ssp_chat_turns",
    )
    op.drop_table("ssp_chat_turns")
    op.drop_index(
        "ix_ssp_chat_conversations_workspace_actor",
        table_name="ssp_chat_conversations",
    )
    op.drop_index(
        "ix_ssp_chat_conversations_workspace_id",
        table_name="ssp_chat_conversations",
    )
    op.drop_table("ssp_chat_conversations")
