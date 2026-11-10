"""google confirm links: email tokens that link a Google account once the inbox is proven

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_tokens", sa.Column("google_subject", sa.String(255)))
    op.drop_constraint("email_tokens_purpose_valid", "email_tokens", type_="check")
    op.create_check_constraint(
        "email_tokens_purpose_valid", "email_tokens", "purpose IN ('verify', 'reset', 'google')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM email_tokens WHERE purpose = 'google'")
    op.drop_constraint("email_tokens_purpose_valid", "email_tokens", type_="check")
    op.create_check_constraint(
        "email_tokens_purpose_valid", "email_tokens", "purpose IN ('verify', 'reset')"
    )
    op.drop_column("email_tokens", "google_subject")
