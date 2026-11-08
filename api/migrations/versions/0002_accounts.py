"""accounts: users, sessions, email tokens, login throttle and the email outbox

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _ts(name: str, nullable: bool = True, now: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if now else None,
    )


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255)),
        sa.Column("display_name", sa.String(80)),
        sa.Column("role", sa.String(20), nullable=False, server_default="user"),
        _ts("email_verified_at"),
        _ts("created_at", nullable=False, now=True),
        _ts("updated_at", nullable=False, now=True),
        sa.CheckConstraint("email = lower(email)", name="users_email_lowercase"),
        sa.CheckConstraint("role IN ('user', 'admin')", name="users_role_valid"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id",
            sa.Uuid,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("client", sa.String(10), nullable=False),
        sa.Column("device_name", sa.String(100)),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("previous_token_hash", sa.String(64), index=True),
        sa.Column("access_hash", sa.String(64), unique=True),
        _ts("access_expires_at"),
        _ts("rotated_at"),
        _ts("created_at", nullable=False, now=True),
        _ts("last_used_at"),
        _ts("expires_at", nullable=False),
        _ts("revoked_at"),
        sa.CheckConstraint("client IN ('web', 'app')", name="auth_sessions_client_valid"),
    )
    op.create_table(
        "email_tokens",
        sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id",
            sa.Uuid,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("pending_password_hash", sa.String(255)),
        _ts("created_at", nullable=False, now=True),
        _ts("expires_at", nullable=False),
        _ts("used_at"),
        sa.CheckConstraint("purpose IN ('verify', 'reset')", name="email_tokens_purpose_valid"),
    )
    op.create_table(
        "login_throttle",
        sa.Column("key", sa.String(400), primary_key=True),
        sa.Column("failures", sa.Integer, nullable=False, server_default="0"),
        _ts("window_started_at", nullable=False),
        _ts("locked_until"),
    )
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("to_address", sa.String(320), nullable=False, index=True),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body_text", sa.Text, nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text),
        _ts("send_after", nullable=False, now=True),
        _ts("sent_at"),
        _ts("created_at", nullable=False, now=True),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed')", name="email_outbox_status_valid"
        ),
    )
    op.create_index(
        "email_outbox_due",
        "email_outbox",
        ["send_after"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_table("email_outbox")
    op.drop_table("login_throttle")
    op.drop_table("email_tokens")
    op.drop_table("auth_sessions")
    op.drop_table("users")
