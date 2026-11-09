"""roles (contributor, moderator, security), suspensions, archived newsletters, audit log

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

ROLES = "('user', 'contributor', 'moderator', 'security', 'admin')"


def upgrade() -> None:
    op.drop_constraint("users_role_valid", "users", type_="check")
    op.create_check_constraint("users_role_valid", "users", f"role IN {ROLES}")
    op.add_column("users", sa.Column("suspended_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("suspended_reason", sa.String(200)))
    op.add_column("users", sa.Column("suspended_by_role", sa.String(20)))
    op.add_column("newsletters", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("actor_id", sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("actor_email", sa.String(320), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("target", sa.String(400), nullable=False),
        sa.Column("detail", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("audit_log_created", "audit_log", ["created_at"])


def downgrade() -> None:
    # Note: this lifts every suspension and turns contributors, moderators and security into
    # plain users, since the old schema has no place for them.
    op.drop_index("audit_log_created", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_column("newsletters", "archived_at")
    op.drop_column("users", "suspended_by_role")
    op.drop_column("users", "suspended_reason")
    op.drop_column("users", "suspended_at")
    op.execute("UPDATE users SET role = 'user' WHERE role NOT IN ('user', 'admin')")
    op.drop_constraint("users_role_valid", "users", type_="check")
    op.create_check_constraint("users_role_valid", "users", "role IN ('user', 'admin')")
