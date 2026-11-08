"""Alembic environment: uses the app's settings and table metadata."""

from alembic import context
from sqlalchemy import create_engine, pool

from lumawen_api import models  # noqa: F401  (registers tables on Base.metadata)
from lumawen_api.config import get_settings
from lumawen_api.db import Base

# The URL is passed straight to SQLAlchemy, not through alembic.ini, because ConfigParser
# treats "%" as special and a percent-encoded password (p%40ss) would break migrations.
config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(get_settings().database_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
