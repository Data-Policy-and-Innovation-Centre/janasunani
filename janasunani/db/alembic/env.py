"""Alembic environment for the OLTP store.

Runs migrations with a **sync** engine derived from ``settings.OLTP_DB_URL`` — the
runtime app uses async drivers (aiosqlite/asyncpg), but Alembic only needs sync, so
we strip the async driver here. This keeps one schema source (the ORM models) and
makes migrations engine-portable (SQLite ↔ Postgres). ``render_as_batch`` is on so
SQLite ALTERs work too.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from janasunani.config import settings
from janasunani.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url() -> str:
    """OLTP_DB_URL with the async driver stripped for Alembic's sync engine."""
    url = settings.OLTP_DB_URL
    return url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_sync_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
