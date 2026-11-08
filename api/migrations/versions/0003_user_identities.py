"""user_identities: Google sign-in linked to users

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_identities",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id",
            sa.Uuid,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("provider", "subject", name="user_identities_provider_subject"),
        sa.CheckConstraint("provider IN ('google')", name="user_identities_provider_valid"),
    )


def downgrade() -> None:
    op.drop_table("user_identities")
