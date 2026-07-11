"""Add optimistic-concurrency revision_version to package_revisions."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260711_0003"
down_revision = "20260711_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "package_revisions",
        sa.Column(
            "revision_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.create_check_constraint(
        "ck_package_revisions_revision_version_positive",
        "package_revisions",
        "revision_version >= 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_package_revisions_revision_version_positive",
        "package_revisions",
        type_="check",
    )
    op.drop_column("package_revisions", "revision_version")
