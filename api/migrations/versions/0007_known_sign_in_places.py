"""known sign-in places: networks a user signed in from, exempt from the per-email pause

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "known_sign_in_places",
        sa.Column(
            "user_id",
            sa.Uuid,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ip", sa.String(64), primary_key=True),
        sa.Column("last_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("known_sign_in_places")
