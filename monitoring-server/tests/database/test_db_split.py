# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Database: the Community schema and migrations, the pre-3.2 refusal, and the incident API's
generic ticket and plugin fields.

SQLite tests always run. The Postgres tests run the real migrations and the raw-SQL upserts
and need AXARA_TEST_PG_URL (an empty database the test may fill and drop tables in), e.g.
postgresql://postgres@/axara_test?host=/tmp&port=55432 ; they are skipped otherwise.
"""
import datetime as dt
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

SERVER = Path(__file__).resolve().parents[2]
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/axara-db-split-import.db")
os.environ.setdefault("SECRET_KEY", "test-secret")
sys.modules.setdefault("chromadb", types.ModuleType("chromadb"))

from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(_t, _c, **_k):
    """Render JSONB as JSON on SQLite."""
    return "JSON"


from axara.community.database import migrate  # noqa: E402
from axara.community.database import models as m  # noqa: E402
from axara.community.plugins.loader import build_registry  # noqa: E402
from axara.community.plugins.registry import Registry, TicketProvider  # noqa: E402
PG_URL = os.getenv("AXARA_TEST_PG_URL")
needs_pg = pytest.mark.skipif(not PG_URL, reason="AXARA_TEST_PG_URL not set")

@pytest.fixture
def sdb(tmp_path):
    """SQLite session with every model table (plugin tables too, when their models are imported)."""
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    m.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _incident(s, **kw):
    """Add and return an incident (with an application)."""
    app = s.query(m.Application).first() or m.Application(name="payments", itsm_provider="jira")
    s.add(app)
    s.flush()
    inc = m.Incident(title=kw.pop("title", "Disk"), status="open", application_id=app.id,
                     created_at=dt.datetime.now(dt.timezone.utc), **kw)
    s.add(inc)
    s.commit()
    return inc


# ------------------------------------------------------------------ schema

CORE_TABLES = {
    "agent_enrolment_tokens", "api_keys", "application_servers", "applications", "audit_log",
    "incident_comments", "incidents", "log_monitor_states", "maintenance_windows", "metrics",
    "monitor_configs", "monitor_states", "platform_config", "servers", "users",
}
CORE_COLUMNS = {
    "incidents": {"application_id", "assignee", "attachment", "created_at", "description", "id",
                  "incident_key", "llm_analysis", "llm_analysis_data", "monitor_config_id",
                  "resolution_notes", "resolved_at", "server_id", "severity", "similar_incidents",
                  "status", "ticket_provider", "ticket_ref", "title", "updated_at"},
    "servers": {"agent_version", "api_key_hash", "created_at", "decommissioned_at", "decommissioned_by",
                "hostname", "id", "ip_address", "last_heartbeat", "os_type", "os_version", "status"},
    "applications": {"created_at", "description", "environment", "id", "itsm_provider", "name",
                     "primary_assignee", "primary_support_team", "tags", "type", "updated_at"},
}


def test_core_models_are_the_community_schema():
    """The core models hold exactly the Community tables and columns (plugin data lives in
    plugin tables)."""
    core = {t for t in m.Base.metadata.tables if t in CORE_TABLES}
    assert core == CORE_TABLES
    for t, cols in CORE_COLUMNS.items():
        assert set(m.Base.metadata.tables[t].columns.keys()) == cols, t


def test_core_tables_never_reference_plugin_tables():
    """Foreign keys of the core tables point only at core tables."""
    for name in CORE_TABLES:
        t = m.Base.metadata.tables[name]
        assert {fk.column.table.name for fk in t.foreign_keys} <= CORE_TABLES, name


def test_core_migration_folder():
    """The core folder holds only the Community baseline, which creates exactly the core tables."""
    core = sorted(p.name for p in migrate.CORE_VERSIONS.glob("*.py"))
    assert core == ["v32_core_base_community_baseline.py"]
    text_core = (migrate.CORE_VERSIONS / core[0]).read_text()
    assert "AGPL-3.0-or-later" in text_core
    import re
    assert set(re.findall(r"create_table\('([a-z_]+)'", text_core)) == CORE_TABLES


def test_core_registry_has_no_migration_dirs():
    """Without plugins only the core migrations run; a missing folder is refused."""
    assert build_registry(builtin=[], use_entry_points=False).migration_dirs() == []
    assert migrate.version_dirs(Registry()) == [migrate.CORE_VERSIONS]
    with pytest.raises(ValueError):
        Registry().add_migration_dir(migrate.MIGRATIONS / "nope")


def test_alembic_config_has_no_password_and_escapes_percent():
    """The Alembic config takes its URL from the caller and survives a % in the password."""
    cfg = migrate.alembic_config("postgresql://u:p%40ss@h/db", Registry())
    assert cfg.get_main_option("sqlalchemy.url") == "postgresql://u:p%40ss@h/db"
    ini = (SERVER / "alembic.ini").read_text()
    assert "sqlalchemy.url" not in ini and "P1ssw0Rd" not in ini


def test_legacy_database_refused(tmp_path):
    """A database at a pre-3.2 revision is refused before anything runs; a new one is not."""
    url = f"sqlite:///{tmp_path / 'old.sqlite'}"
    migrate.check_not_legacy(url)                                     # empty database: fine
    e = create_engine(url)
    with e.begin() as c:
        c.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        c.execute(text("INSERT INTO alembic_version VALUES ('e1f2a3b4c5d6')"))
    with pytest.raises(migrate.LegacySchemaError, match="before v3.2"):
        migrate.upgrade(url, Registry())


# ------------------------------------------------------------------ incident API fields

class _Prov(TicketProvider):
    """Ticket provider with a URL."""
    name = "fake"

    def ticket_url(self, ticket):
        """Fake ticket url."""
        return f"https://itsm.example/{ticket}"


def test_incident_fields_merge_and_survive_a_failing_provider(sdb):
    """Fields from several providers merge; a failing provider is skipped."""
    inc = _incident(sdb)
    reg = Registry()
    reg.add_incident_fields(lambda db, ids, detail: {i: {"a": 1} for i in ids})
    reg.add_incident_fields(lambda db, ids, detail: 1 / 0)
    reg.add_incident_fields(lambda db, ids, detail: {i: {"b": detail} for i in ids}, feature="x")
    assert reg.incident_fields(sdb, [inc.id], detail=True) == {inc.id: {"a": 1, "b": True}}
    reg.set_licence_gates(lambda f: f != "x", lambda f: True)
    assert reg.incident_fields(sdb, [inc.id]) == {inc.id: {"a": 1}}
    assert reg.incident_fields(sdb, []) == {}


def test_enrich_adds_ticket_url_and_plugin_fields_without_overwriting(sdb, monkeypatch):
    """The incident API shows the ticket link from its provider and plugin fields."""
    from axara.community.api.routes import incidents
    from axara.community.plugins import loader
    reg = Registry()
    reg.add_ticket_provider(_Prov())
    reg.add_incident_fields(lambda db, ids, detail: {i: {"parent_incident_id": "p", "title": "x"} for i in ids})
    monkeypatch.setattr(loader, "_registry", reg)
    inc = _incident(sdb, ticket_provider="fake", ticket_ref="FAKE-7")
    d = incidents._enrich(incidents._load(inc.id, sdb), sdb, detail=True)
    assert d["ticket_provider"] == "fake" and d["ticket_ref"] == "FAKE-7"
    assert d["ticket_url"] == "https://itsm.example/FAKE-7"
    assert d["parent_incident_id"] == "p" and d["title"] == "Disk"          # core column wins
    assert "jira_ticket_id" not in d


# ------------------------------------------------------------------ Postgres (real migrations)

@pytest.fixture
def pg():
    """The test Postgres database, emptied before and after."""
    e = create_engine(PG_URL)

    def wipe():
        """Drop everything the migrations create."""
        with e.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
    wipe()
    yield e
    e.dispose()
    wipe()


def _tables(e):
    """Table names of the database."""
    return set(inspect(e).get_table_names())


@needs_pg
def test_pg_community_schema(pg):
    """The Community migrations create exactly the core tables, and run again cleanly."""
    reg = Registry()
    migrate.upgrade(PG_URL, reg)
    assert _tables(pg) - {"alembic_version"} == CORE_TABLES
    cols = {c["name"] for c in inspect(pg).get_columns("applications")}
    assert cols == CORE_COLUMNS["applications"]
    migrate.upgrade(PG_URL, reg)                                          # idempotent


@needs_pg
def test_pg_audit_log_stays_append_only(pg):
    """The baseline keeps the triggers that block UPDATE/DELETE on audit_log."""
    migrate.upgrade(PG_URL, Registry())
    with pg.begin() as c:
        c.execute(text("INSERT INTO audit_log (ts, actor_type, action, outcome, prev_hash, hash) "
                       "VALUES (now(), 'system', 't', 'success', '0', 'h1')"))
    with pytest.raises(Exception, match="append-only"):
        with pg.begin() as c:
            c.execute(text("DELETE FROM audit_log"))
