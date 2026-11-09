"""newsletters: emails to the whole mailing list, sent through the outbox

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "newsletters",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body_text", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("send_at", sa.DateTime(timezone=True)),
        sa.Column("queued_at", sa.DateTime(timezone=True)),
        sa.Column("recipients", sa.Integer),
        sa.Column("created_by", sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'scheduled', 'sent')", name="newsletters_status_valid"
        ),
    )
    op.create_index(
        "newsletters_due",
        "newsletters",
        ["send_at"],
        postgresql_where=sa.text("status = 'scheduled'"),
    )
    op.add_column(
        "email_outbox",
        sa.Column("newsletter_id", sa.Uuid, sa.ForeignKey("newsletters.id", ondelete="SET NULL")),
    )
    op.add_column("email_outbox", sa.Column("list_unsubscribe", sa.String(500)))
    op.create_index("ix_email_outbox_newsletter_id", "email_outbox", ["newsletter_id"])
    op.create_index(
        "email_outbox_newsletter_once",
        "email_outbox",
        ["newsletter_id", "to_address"],
        unique=True,
        postgresql_where=sa.text("kind = 'newsletter'"),
    )


def downgrade() -> None:
    op.drop_index("email_outbox_newsletter_once", table_name="email_outbox")
    op.drop_index("ix_email_outbox_newsletter_id", table_name="email_outbox")
    op.drop_column("email_outbox", "list_unsubscribe")
    op.drop_column("email_outbox", "newsletter_id")
    op.drop_table("newsletters")
