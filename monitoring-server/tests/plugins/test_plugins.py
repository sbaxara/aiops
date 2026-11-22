# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Plugin registry (v3.2 phase 1): loading rules, licence gating at call time, the core's
Jira provider, the orchestrator's use of providers and notifiers, and the ITSM sync job."""
import datetime as dt
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import importlib.util

import pytest

SERVER = Path(__file__).resolve().parents[2]
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/axara-plugins-import.db")
os.environ.setdefault("SECRET_KEY", "test-secret")
sys.modules.setdefault("chromadb", types.ModuleType("chromadb"))       # incident memory is faked

from axara.community.plugins import loader  # noqa: E402
from axara.community.plugins.core import JiraTicketProvider, _adf_text  # noqa: E402
from axara.community.plugins.registry import Notifier, Registry, TicketProvider  # noqa: E402


class FakeProvider(TicketProvider):
    """Ticket provider recording its calls; fetch_resolution returns .resolution."""
    name = "fake"

    def __init__(self):
        """Create the fake."""
        self.calls = []
        self.resolution = None

    def create_ticket(self, incident):
        """Fake create ticket."""
        self.calls.append(("create", incident["id"]))
        return "FAKE-1"

    def add_attachment(self, ticket, file_path):
        """Fake add attachment."""
        self.calls.append(("attach", ticket, file_path))
        return True

    def add_comment(self, ticket, text):
        """Fake add comment."""
        self.calls.append(("comment", ticket, text))
        return True

    def resolve(self, ticket, notes):
        """Fake resolve."""
        self.calls.append(("resolve", ticket, notes))
        return True

    def fetch_resolution(self, ticket):
        """Fake fetch resolution."""
        return self.resolution


class FakeNotifier(Notifier):
    """Notifier recording what it was asked to send."""
    name = "fake"

    def __init__(self):
        """Create the fake."""
        self.sent = []

    def incident(self, incident):
        """Fake incident."""
        self.sent.append(("incident", incident.get("event_type")))

    def resolution(self, incident):
        """Fake resolution."""
        self.sent.append(("resolution", incident["id"]))


# ------------------------------------------------------------------ registry and loader

def test_core_only_registry():
    """Core only registry."""
    reg = loader.build_registry(builtin=[], use_entry_points=False)
    assert reg.ticket_provider_names() == ["jira"]
    assert reg.notifiers() == [] and reg.password_logins() == [] and reg.routers() == []
    assert all(reg.function_allowed(f) for f in ("analysis", "notifications", "incidents"))
    assert reg.feature_allowed("reports") is True                       # no gate installed (Community)
    assert all(not reg._checks[k] for k in ("login", "user", "agent_registration", "agent_intake"))
    reg.run_checks("user", object())                                 # nothing to refuse
    assert reg.commands() == {} and reg.audit_sinks() == [] and reg.migration_dirs() == []
    assert reg.local_registration_enabled() is True
    with pytest.raises(ValueError):
        reg.function_allowed("unknown")


def test_gates_apply_at_call_time():
    """Gates apply at call time."""
    reg = Registry()
    reg.add_ticket_provider(FakeProvider, feature="itsm")
    reg.add_notifier(FakeNotifier, feature="notify")
    state = {"itsm": True, "notify": False, "analysis": True}
    reg.set_licence_gates(lambda f: state[f], lambda f: state.get(f, False))
    assert reg.ticket_provider("fake") is not None and reg.notifiers() == []
    state.update(itsm=False, notify=True)                           # licence changed, no restart
    assert reg.ticket_provider("fake") is None and reg.ticket_provider_names() == []
    assert [n.name for n in reg.notifiers()] == ["fake"]
    assert reg.function_allowed("analysis") and not reg.function_allowed("notifications")
    with pytest.raises(RuntimeError):
        reg.set_licence_gates(lambda f: True, lambda f: True)       # only one gate owner


def test_gate_errors_fail_closed():
    """Gate errors fail closed."""
    reg = Registry()
    reg.set_licence_gates(lambda f: 1 / 0, lambda f: 1 / 0)
    assert reg.feature_allowed("reports") is False and reg.function_allowed("analysis") is False
    assert reg.feature_allowed(None) is True


def test_broken_factories_are_skipped():
    """Broken factories are skipped."""
    reg = Registry()
    reg.add_ticket_provider(type("Bad", (TicketProvider,), {"name": "bad", "__init__": lambda self: 1 / 0}))
    reg.add_notifier(lambda: 1 / 0)
    assert reg.ticket_provider("bad") is None and reg.notifiers() == []


def test_duplicates_refused():
    """Duplicates refused."""
    reg = Registry()
    reg.add_ticket_provider(FakeProvider)
    with pytest.raises(ValueError):
        reg.add_ticket_provider(FakeProvider)
    with pytest.raises(ValueError):
        reg.add_router(object(), "everyone")


def test_builtin_missing_module_is_skipped_but_core_loads():
    """Builtin missing module is skipped but core loads."""
    reg = loader.build_registry(builtin=["axara.community.plugins.not_in_this_image"], use_entry_points=False)
    assert reg.plugins == [] and reg.ticket_provider_names() == ["jira"]


def test_entry_points_load(monkeypatch):
    """Entry points load."""
    mod = types.ModuleType("axara_test_plugin")
    mod.register = lambda reg: reg.add_ticket_provider(FakeProvider)
    sys.modules["axara_test_plugin"] = mod
    ep = SimpleNamespace(value="axara_test_plugin:register", load=lambda: mod.register)
    monkeypatch.setattr(loader.metadata, "entry_points", lambda group: [ep] if group == loader.ENTRY_POINT_GROUP else [])
    reg = loader.build_registry(builtin=[])
    assert "fake" in reg.ticket_provider_names() and "axara_test_plugin:register" in reg.plugins


def test_startup_hooks_isolated():
    """Startup hooks isolated."""
    reg = Registry()
    ran = []
    reg.add_startup("bad", lambda app: 1 / 0)
    reg.add_startup("good", lambda app: ran.append("good"))
    reg.add_startup("gated", lambda app: ran.append("gated"), feature="reports")
    reg.set_licence_gates(lambda f: False, lambda f: True)
    reg.run_startup(SimpleNamespace())
    assert ran == ["good"]


# ------------------------------------------------------------------ Jira provider

class FakeJira:
    """Stand-in for JiraClient returning a fixed issue."""
    _enabled = True

    def __init__(self, issue, me="acc-bot"):
        """Create the fake."""
        self.issue, self.me = issue, me

    def _get(self, path):
        """Answer /myself or the fixed issue."""
        if path == "/myself":
            return {"accountId": self.me}
        return self.issue


def _issue(status="Done", category="done", comments=(), resolution="Fixed"):
    """A Jira issue payload with the given status, comments and resolution."""
    return {"fields": {"status": {"name": status, "statusCategory": {"key": category}},
                       "resolution": {"name": resolution} if resolution else None,
                       "comment": {"comments": list(comments)}}}


def test_jira_fetch_resolution_uses_latest_human_comment():
    """Jira fetch resolution uses latest human comment."""
    adf = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Restarted the pool"}]}]}
    comments = [{"author": {"accountId": "acc-human"}, "body": adf},
                {"author": {"accountId": "acc-bot"}, "body": "[AUTO-RESOLUTION] ..."}]
    p = JiraTicketProvider(FakeJira(_issue(comments=comments)))
    assert p.fetch_resolution("OPS-1") == "Restarted the pool\nJira OPS-1 resolution: Fixed"


def test_jira_open_ticket_returns_none():
    """Jira open ticket returns none."""
    p = JiraTicketProvider(FakeJira(_issue(status="In Progress", category="indeterminate")))
    assert p.fetch_resolution("OPS-1") is None


def test_jira_done_without_notes_has_default_text():
    """Jira done without notes has default text."""
    p = JiraTicketProvider(FakeJira(_issue(resolution=None)))
    assert p.fetch_resolution("OPS-2") == "Resolved in Jira (OPS-2)"


def test_adf_text_plain_string():
    """Adf text plain string."""
    assert _adf_text("plain") == "plain"


# ------------------------------------------------------------------ orchestrator

def _orchestrator(reg):
    """An IncidentOrchestrator wired to a registry and fake repositories."""
    from axara.community.incident_management import orchestrator as om
    o = om.IncidentOrchestrator.__new__(om.IncidentOrchestrator)
    o.registry = reg
    o.db = SimpleNamespace(refresh=lambda x: None)
    o.incident_repo = SimpleNamespace(update=lambda i, d: o.updates.append(d), find_by_id=lambda i: None)
    o.updates = []
    o._incident_to_dict = lambda inc: {"id": inc.id, "attachment": "/tmp/shot.png"}
    return o


def _inc(provider="fake", ticket=None):
    """A minimal incident object for the orchestrator."""
    return SimpleNamespace(id="inc-1", application=SimpleNamespace(itsm_provider=provider),
                           ticket_provider=provider if ticket else None, ticket_ref=ticket, llm_analysis="RCA",
                           server_id="s", application_id="a")


def test_orchestrator_opens_ticket_through_provider():
    """Orchestrator opens ticket through provider."""
    reg = Registry()
    prov = FakeProvider()
    reg.add_ticket_provider(prov)
    o = _orchestrator(reg)
    o._create_external_tickets(_inc())
    assert prov.calls == [("create", "inc-1"), ("attach", "FAKE-1", "/tmp/shot.png")]
    assert o.updates == [{"ticket_provider": "fake", "ticket_ref": "FAKE-1"}]


def test_orchestrator_unknown_provider_skips_ticket():
    """Orchestrator unknown provider skips ticket."""
    o = _orchestrator(Registry())
    o._create_external_tickets(_inc(provider="servicenow"))
    assert o.updates == []


def test_orchestrator_notifies_each_notifier(monkeypatch):
    """Orchestrator notifies each notifier."""
    import axara.community.processing.maintenance_window as mw
    monkeypatch.setattr(mw.MaintenanceWindowManager, "__init__", lambda self, db: None)
    monkeypatch.setattr(mw.MaintenanceWindowManager, "active_suppress_type", lambda self, s, a: None)
    reg = Registry()
    n = FakeNotifier()
    reg.add_notifier(n)
    o = _orchestrator(reg)
    o._send_notifications(_inc(), "opened")
    o._send_resolution_notifications(_inc())
    assert n.sent == [("incident", "opened"), ("resolution", "inc-1")]
    reg.set_licence_gates(lambda f: True, lambda f: f != "notifications")
    o._send_notifications(_inc(), "opened")
    assert len(n.sent) == 2                                      # monitoring-only: nothing sent


def test_orchestrator_analysis_note_and_recheck():
    """Orchestrator analysis note and recheck."""
    reg = Registry()
    prov = FakeProvider()
    reg.add_ticket_provider(prov)
    o = _orchestrator(reg)
    o._add_llm_comment(_inc(ticket="FAKE-9"), {"raw_analysis": "Root cause: disk"})
    assert prov.calls == [("comment", "FAKE-9", "Root cause: disk")]


# ------------------------------------------------------------------ ITSM sync job

@pytest.fixture
def db(tmp_path, monkeypatch):
    """SQLite session with every model's table (JSONB rendered as JSON)."""
    from sqlalchemy import create_engine
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker

    @compiles(JSONB, "sqlite")
    def _j(_t, _c, **_k):
        """Fake j."""
        return "JSON"
    from axara.community.database import models as m
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    m.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s, m
    s.close()


def test_itsm_sync_resolves_and_remembers(db, monkeypatch):
    """Itsm sync resolves and remembers."""
    s, m = db
    from axara.community.incident_management import itsm_sync, orchestrator as om
    stored = []
    monkeypatch.setattr(om.IncidentOrchestrator, "__init__", lambda self, db: None)
    monkeypatch.setattr(om.IncidentOrchestrator, "_store_in_rag",
                        lambda self, inc, resolution_notes=None, source=None: stored.append((inc.id, resolution_notes, source)))
    app = m.Application(name="payments", itsm_provider="fake")
    s.add(app)
    s.flush()
    open_inc = m.Incident(title="Disk", status="open", application_id=app.id, ticket_provider="fake", ticket_ref="FAKE-1",
                          created_at=dt.datetime.now(dt.timezone.utc))
    no_ticket = m.Incident(title="CPU", status="open", application_id=app.id)
    s.add_all([open_inc, no_ticket])
    s.commit()
    reg = Registry()
    prov = FakeProvider()
    reg.add_ticket_provider(prov)
    assert itsm_sync.sync_once(s, reg) == 0                         # still open in the ITSM tool
    prov.resolution = "Purged old logs"
    assert itsm_sync.sync_once(s, reg) == 1
    s.refresh(open_inc)
    assert open_inc.status == "resolved" and open_inc.resolution_notes == "Purged old logs"
    assert stored == [(open_inc.id, "Purged old logs", "fake")]
    assert ("resolve", "FAKE-1", "Purged old logs") not in prov.calls   # never resolved back
    assert itsm_sync.sync_once(s, reg) == 0                         # already resolved


def test_itsm_sync_interval_default():
    """Itsm sync interval default."""
    from axara.community.incident_management import itsm_sync
    assert itsm_sync.interval_minutes() >= 1


# ------------------------------------------------------------------ extension hooks

def test_metric_type_options():
    """Plugin metric types can be observation-only, stand in for a core type, or stay out of
    the health summary; two stand-ins for one core type are refused."""
    reg = Registry()
    reg.add_metric_type("x_census", alertable=False, in_health_summary=False)
    reg.add_metric_type("x_cpu", alertable=False, stands_in_for="cpu")
    reg.add_metric_type("x_tls")
    assert reg.non_alertable_metric_types() == {"x_census", "x_cpu"}
    assert reg.health_summary_excluded_types() == {"x_census"}
    assert reg.metric_fallback("cpu") == "x_cpu" and reg.metric_fallback("disk") is None
    assert reg.metric_base_type("x_cpu") == "cpu" and reg.metric_base_type("x_tls") == "x_tls"
    with pytest.raises(ValueError):
        reg.add_metric_type("y_cpu", stands_in_for="cpu")


def test_core_registry_has_no_stand_ins():
    """Without plugins every metric type is alertable and resolves to itself."""
    reg = Registry()
    assert reg.non_alertable_metric_types() == set() and reg.metric_fallback("cpu") is None


def test_audit_skip_prefixes():
    """Plugins can leave agent paths out of the request audit; only /api/.../ prefixes."""
    from axara.community.api.middleware import audit
    reg = Registry()
    reg.add_audit_skip_prefix("/api/v1/x/agent/")
    reg.add_audit_skip_prefix("/api/v1/x/agent/")
    assert reg.audit_skip_prefixes() == ["/api/v1/x/agent/"]
    for bad in ("/other/", "/api/v1/x"):
        with pytest.raises(ValueError):
            reg.add_audit_skip_prefix(bad)
    real = loader._registry
    try:
        loader._registry = reg
        assert not audit.should_audit("POST", "/api/v1/x/agent/evidence")
        assert audit.should_audit("POST", "/api/v1/x/settings")
    finally:
        loader._registry = real


def test_llm_slot_default_and_failing_provider():
    """llm_slot is a no-op by default; a provider that fails never blocks the LLM call."""
    import contextlib
    reg = Registry()
    with reg.llm_slot("high"):
        pass
    seen = []

    @contextlib.contextmanager
    def slot(priority):
        """Record the priority."""
        seen.append(priority)
        yield 1
    reg.set_llm_slot(slot)
    with reg.llm_slot("high"):
        pass
    assert seen == ["high"]
    reg.set_llm_slot(lambda p: 1 / 0)
    with reg.llm_slot("high"):
        pass


def test_new_events_are_known():
    """The hooks the core emits are registrable events; unknown events are refused."""
    reg = Registry()
    for ev in ("monitors_yaml", "server_config_saved", "monitor_defaults", "application_created"):
        reg.add_event_hook(ev, lambda *a: None)
    with pytest.raises(ValueError):
        reg.add_event_hook("nope", lambda: None)


def test_monitors_yaml_hook_changes_what_the_agent_gets(db, monkeypatch, tmp_path):
    """A monitors_yaml hook may switch a monitor off in the file without changing the row."""
    import yaml
    s, m = db
    from axara.community.api.routes import config as cfg
    monkeypatch.setattr(cfg, "_AGENT_CONFIG_DIR", tmp_path)
    app = m.Application(name="a")
    srv = m.Server(hostname="h", status="active")
    s.add_all([app, srv])
    s.flush()
    s.add(m.MonitorConfig(application_id=app.id, server_id=srv.id, name="c", type="cpu", enabled=True,
                          check_interval_seconds=60, threshold_config={}, config={}))
    s.commit()
    reg = Registry()
    reg.add_event_hook("monitors_yaml", lambda sid, mons: [d.update(enabled=False) for d in mons])
    monkeypatch.setattr(loader, "_registry", reg)
    cfg._regenerate_monitors_yaml(srv.id, s)
    data = yaml.safe_load((tmp_path / srv.id / "monitors.yaml").read_text())
    assert [d["enabled"] for d in data["monitors"]] == [False]
    assert s.query(m.MonitorConfig).one().enabled is True
