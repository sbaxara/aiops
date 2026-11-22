# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Apply the database migrations of the core and of every installed plugin.

The schema is one Alembic history with branches: the core branch (label `core`, in
database/migrations/versions) and one branch per plugin that has its own tables (for
example `enterprise`), each depending on the core baseline. Plain `alembic upgrade head`
only knows the core folder, so the server runs migrations through this module
(`python manage.py migrate`), which adds the folders plugins register
(Registry.add_migration_dir) and upgrades every branch head.

A database created before v3.2 carries a revision of the old single chain. v3.2 starts
from a fresh baseline with no upgrade path, so such a database is refused with a clear
message instead of failing halfway through a migration.
"""
import logging
import os
from pathlib import Path
from typing import List, Optional

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

logger = logging.getLogger(__name__)

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
CORE_VERSIONS = MIGRATIONS / "versions"

# Revisions of the pre-v3.2 migration chain (removed at the v3.2 baseline).
LEGACY_REVISIONS = frozenset({
    "f746779f978d", "a1b2c3d4e5f6", "b2c3d4e5f6a7", "c3d4e5f6a7b8", "d4e5f6a7b8c9",
    "e5f6a7b8c9d0", "f6a7b8c9d0e1", "a7b8c9d0e1f2", "b8c9d0e1f2a3", "c9d0e1f2a3b4",
    "d0e1f2a3b4c5", "e1f2a3b4c5d6",
})


class LegacySchemaError(RuntimeError):
    """The database was created by a version before v3.2 and cannot be upgraded."""


def database_url() -> str:
    """The database URL from the environment.

    Returns:
        DATABASE_URL.

    Raises:
        RuntimeError: when DATABASE_URL is not set.
    """
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def version_dirs(registry=None) -> List[Path]:
    """Migration folders: the core folder, then each plugin's.

    Args:
        registry: Plugin registry; the server's registry when None.

    Returns:
        Folder paths.
    """
    if registry is None:
        from axara.community.plugins.loader import get_registry
        registry = get_registry()
    return [CORE_VERSIONS, *registry.migration_dirs()]


def alembic_config(url: Optional[str] = None, registry=None) -> Config:
    """An Alembic configuration covering the core and every plugin branch.

    Args:
        url: Database URL; DATABASE_URL when None.
        registry: Plugin registry; the server's registry when None.

    Returns:
        The configuration (no alembic.ini needed).

    Raises:
        RuntimeError: when no database URL is available.
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("version_path_separator", "os")
    cfg.set_main_option("version_locations", os.pathsep.join(str(d) for d in version_dirs(registry)))
    # ConfigParser interpolation: a literal % (e.g. in a URL-encoded password) must be doubled.
    cfg.set_main_option("sqlalchemy.url", (url or database_url()).replace("%", "%%"))
    return cfg


def stored_revisions(url: str) -> List[str]:
    """Revisions recorded in the database's alembic_version table.

    Args:
        url: Database URL.

    Returns:
        Revision ids (empty for a new database).
    """
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            if not inspect(conn).has_table("alembic_version"):
                return []
            return [r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version"))]
    finally:
        engine.dispose()


def check_not_legacy(url: str) -> None:
    """Refuse a database created before v3.2.

    Args:
        url: Database URL.

    Raises:
        LegacySchemaError: when the database is at a pre-v3.2 revision.
    """
    old = sorted(set(stored_revisions(url)) & LEGACY_REVISIONS)
    if old:
        raise LegacySchemaError(
            f"This database was created by Axara AIOps before v3.2 (revision {', '.join(old)}). "
            "v3.2 starts from a new database schema and cannot upgrade it. Back up what you need, "
            "then install into a new, empty database.")


def upgrade(url: Optional[str] = None, registry=None) -> None:
    """Bring the database to the latest revision of every branch.

    Args:
        url: Database URL; DATABASE_URL when None.
        registry: Plugin registry; the server's registry when None.

    Raises:
        LegacySchemaError: when the database predates v3.2.
        RuntimeError: when no database URL is available.
    """
    url = url or database_url()
    check_not_legacy(url)
    cfg = alembic_config(url, registry)
    logger.info("migrating: %s", cfg.get_main_option("version_locations"))
    command.upgrade(cfg, "heads")


def main() -> int:
    """`python manage.py migrate` entry point.

    Returns:
        0 on success, 1 when the database is refused or a migration fails.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        upgrade()
    except LegacySchemaError as e:
        print(f"ERROR: {e}")
        return 1
    except Exception as e:
        print(f"ERROR: migration failed: {e}")
        return 1
    print("Database schema is up to date.")
    return 0
