"""Add OIDC-backed auth session and login-state tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260711_0005"
down_revision = "20260711_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("groups", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("csrf_token", sa.String(length=512), nullable=False),
        sa.Column("portal_origin", sa.String(length=2048), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "absolute_expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("session_id", name="pk_auth_sessions"),
        sa.CheckConstraint(
            "char_length(actor_id) >= 1",
            name="ck_auth_sessions_actor_id_min_length",
        ),
        sa.CheckConstraint(
            "char_length(csrf_token) >= 32",
            name="ck_auth_sessions_csrf_token_min_length",
        ),
        sa.CheckConstraint(
            "char_length(portal_origin) >= 1",
            name="ck_auth_sessions_portal_origin_min_length",
        ),
    )
    op.create_index(
        "ix_auth_sessions_absolute_expires_at",
        "auth_sessions",
        ["absolute_expires_at"],
    )
    op.create_index(
        "ix_auth_sessions_last_seen_at",
        "auth_sessions",
        ["last_seen_at"],
    )

    op.create_table(
        "oidc_login_states",
        sa.Column("state_token", sa.String(length=128), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("nonce", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("state_token", name="pk_oidc_login_states"),
        sa.CheckConstraint(
            "char_length(state_token) >= 16",
            name="ck_oidc_login_states_state_token_min_length",
        ),
    )
    op.create_index(
        "ix_oidc_login_states_expires_at",
        "oidc_login_states",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_oidc_login_states_expires_at", table_name="oidc_login_states")
    op.drop_table("oidc_login_states")
    op.drop_index("ix_auth_sessions_last_seen_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_absolute_expires_at", table_name="auth_sessions")
    op.drop_table("auth_sessions")
