"""subscribers: the "Lumawen updates" mailing list (+ an outbox index for its email cap)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subscribers",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("confirm_token_hash", sa.String(64), unique=True),
        sa.Column("confirm_expires_at", sa.DateTime(timezone=True)),
        sa.Column("unsubscribe_token", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("email = lower(email)", name="subscribers_email_lowercase"),
        sa.CheckConstraint(
            "status IN ('pending', 'subscribed', 'unsubscribed')", name="subscribers_status_valid"
        ),
    )
    op.create_index("email_outbox_kind_created", "email_outbox", ["kind", "created_at"])


def downgrade() -> None:
    op.drop_index("email_outbox_kind_created", table_name="email_outbox")
    op.drop_table("subscribers")
