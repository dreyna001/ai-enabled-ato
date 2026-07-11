"""Add idempotency response headers and source artifact revision sha256 uniqueness."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260711_0004"
down_revision = "20260711_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "idempotency_records",
        sa.Column(
            "response_headers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_unique_constraint(
        "uq_source_artifacts_revision_sha256",
        "source_artifacts",
        ["package_revision_id", "sha256"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_source_artifacts_revision_sha256",
        "source_artifacts",
        type_="unique",
    )
    op.drop_column("idempotency_records", "response_headers")
