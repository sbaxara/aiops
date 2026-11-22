# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Alembic environment: connects to DATABASE_URL and runs the selected migrations.

Run migrations with `python manage.py migrate` (database/migrate.py), which also loads the
migration folders of installed plugins. The database URL comes from DATABASE_URL
when the alembic CLI is used, or from axara.community.database.migrate, which passes its URL in the config.
No URL or password is kept in alembic.ini.
"""
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make sure the project root is on the path
# monitoring-server/ (env.py is in axara/community/database/migrations/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))))

from axara.community.database.models import Base  # noqa: E402

try:                                    # plugin tables: loading the plugins imports their models
    from axara.community.plugins.loader import get_registry  # noqa: E402
    get_registry()
except Exception:
    pass

config = context.config

# database.migrate passes its URL in the config; the plain alembic CLI uses DATABASE_URL.
database_url = os.getenv("DATABASE_URL")
if not config.get_main_option("sqlalchemy.url") and database_url:
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
if not config.get_main_option("sqlalchemy.url"):
    raise RuntimeError("DATABASE_URL is not set")

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit the migration SQL without connecting (alembic --sql)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run the migrations against the database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
