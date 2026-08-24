import sys
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Make the repo root importable (this file lives in migrations/, one level down).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Base
import db_models  # noqa: F401 — import so models register on Base.metadata
import config as app_config  # aliased — "config" below is Alembic's own object

# This is the Alembic Config object, which provides access to the values
# within the .ini file in use.
config = context.config

# Interpret the config file for Python logging (loggers/formatters/handlers).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Point Alembic at our SQLAlchemy models for autogenerate support.
target_metadata = Base.metadata

# Use MIDDLEWARE_DB_URL from our own config.py / .env instead of whatever
# static value alembic.ini has — keeps one source of truth for the DB URL.
config.set_main_option("sqlalchemy.url", app_config.MIDDLEWARE_DB_URL)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — generates SQL without a live DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema="middleware",
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — connects to the DB and applies changes directly."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema="middleware",
            include_schemas=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()