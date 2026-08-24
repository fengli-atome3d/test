"""
This is a TEMPLATE for migrations/env.py — Alembic's own `alembic init`
command generates that file, and you edit it to point at our models. Rather
than hand-editing the auto-generated file blind, copy the relevant pieces
below into the real migrations/env.py after running `alembic init migrations`
(see the setup steps).
"""

# --- Add near the top, alongside the other imports Alembic already put there:
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Base
import db_models  # noqa: F401 — import so models register on Base.metadata
import config

# --- Replace the line `target_metadata = None` with:
target_metadata = Base.metadata

# --- In both run_migrations_offline() and run_migrations_online(), find the
# context.configure(...) call and add version_table_schema and the schema
# include filter, e.g.:
#
#   context.configure(
#       url=url,
#       target_metadata=target_metadata,
#       version_table_schema="middleware",   # <- add this line
#       include_schemas=True,                # <- add this line
#       ...
#   )

# --- Also override the sqlalchemy.url that alembic.ini would otherwise use,
# so it reads from the same config.py / .env as the app instead of a
# hardcoded value sitting in alembic.ini:
config_ini = None  # placeholder, real file has `config = context.config` already
# find that line in the generated file, then right after it add:
#   config.set_main_option("sqlalchemy.url", config.MIDDLEWARE_DB_URL)
# (note: this uses our config.py's MIDDLEWARE_DB_URL — careful not to
# confuse Alembic's `config` object with our own config module; rename
# one of the imports if needed to avoid shadowing)