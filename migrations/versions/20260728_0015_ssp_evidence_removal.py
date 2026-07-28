"""Add reversible pre-analysis SSP evidence removal metadata."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260728_0015"
down_revision = "20260727_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ssp_evidence_artifacts",
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ssp_evidence_artifacts",
        sa.Column("removed_by", sa.String(255), nullable=True),
    )
    op.create_check_constraint(
        "ck_ssp_evidence_artifacts_removal_fields",
        "ssp_evidence_artifacts",
        "(removed_at IS NULL AND removed_by IS NULL) OR "
        "(removed_at IS NOT NULL AND removed_by IS NOT NULL "
        "AND char_length(removed_by) >= 1)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_ssp_evidence_artifacts_removal_fields",
        "ssp_evidence_artifacts",
        type_="check",
    )
    op.drop_column("ssp_evidence_artifacts", "removed_by")
    op.drop_column("ssp_evidence_artifacts", "removed_at")
