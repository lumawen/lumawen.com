"""app_settings table with the community defaults

Revision ID: 0001
Revises:
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    settings = op.create_table(
        "app_settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", JSONB, nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.bulk_insert(
        settings,
        [
            # off | subreddit | discord | both | comments | forum
            {"key": "community.mode", "value": "both"},
            {"key": "community.subreddit_url", "value": "https://www.reddit.com/r/lumawen"},
            {"key": "community.discord_url", "value": ""},
        ],
    )


def downgrade() -> None:
    op.drop_table("app_settings")
