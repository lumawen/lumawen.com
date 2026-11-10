"""email outbox priority: password resets go before sign-up floods, newsletters last

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "email_outbox",
        sa.Column("priority", sa.SmallInteger, nullable=False, server_default=sa.text("5")),
    )
    op.execute("UPDATE email_outbox SET priority = 0 WHERE kind = 'reset'")
    op.execute("UPDATE email_outbox SET priority = 9 WHERE kind = 'newsletter'")
    op.create_index(
        "email_outbox_next",
        "email_outbox",
        ["priority", "id"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("email_outbox_next", table_name="email_outbox")
    op.drop_column("email_outbox", "priority")
