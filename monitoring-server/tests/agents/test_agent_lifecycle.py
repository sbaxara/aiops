# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Agent lifecycle: enrolment tokens, registration rules, deregistration / admin removal,
archived monitors, and the agent-key checks on agent endpoints.
Runs the real routes against SQLite (JSONB rendered as JSON), with the core registry only
(no plugins); make_env() lets plugin tests run the same routes with their registry."""
import datetime as dt
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SERVER = Path(__file__).resolve().parents[2]
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/axara-agent-lifecycle-import.db")
os.environ.setdefault("SECRET_KEY", "test-secret")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(_t, _c, **_k):
    """Render JSONB as JSON on SQLite."""
    return "JSON"


from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from axara.community import database  # noqa: E402
from axara.community.database import models as m  # noqa: E402
from axara.community.api import agent_auth, agent_lifecycle as lifecycle  # noqa: E402
from axara.community.api.routes import agents, auth, config, metrics  # noqa: E402
from axara.community.plugins import loader  # noqa: E402
ADMIN = SimpleNamespace(username="admin", id=1, role="admin")
VIEWER = SimpleNamespace(username="viewer", id=2, role="viewer")
H = lifecycle.ENROLMENT_HEADER


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Test app with the core registry only (no plugins). See make_env.

    Returns:
        Namespace with client, Session, events (audit records) and a settable user.
    """
    return make_env(tmp_path, monkeypatch, loader.build_registry(builtin=[], use_entry_points=False))


def make_env(tmp_path, monkeypatch, registry):
    """Test app with the agent, config and metrics routes on a fresh SQLite database.

    Args:
        tmp_path: Test folder.
        monkeypatch: pytest monkeypatch.
        registry: Plugin registry the routes use.

    Returns:
        Namespace with client, Session, events (audit records) and a settable user.
    """
    monkeypatch.setattr(loader, "_registry", registry)
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    m.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(database, "SessionLocal", Session)
    monkeypatch.setattr(config, "SessionLocal", Session)
    monkeypatch.setattr(config, "_AGENT_CONFIG_DIR", tmp_path / "agent-config")
    events = []
    from axara.community.common import audit
    monkeypatch.setattr(audit, "record", lambda action, **kw: events.append({"action": action, **kw}))

    def db_dep():
        """Session dependency override.

        Yields:
            SQLAlchemy session.
        """
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(agents.router)
    app.include_router(config.agent_router)
    app.include_router(config.router)
    app.include_router(metrics.router)
    user = {"u": ADMIN}

    def admin_only():
        """require_admin override using the test's current user.

        Returns:
            The user.

        Raises:
            HTTPException: 403 for a non-admin.
        """
        if user["u"].role != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")
        return user["u"]
    for dep in (agent_auth.get_db, config.get_db, metrics.get_db):
        app.dependency_overrides[dep] = db_dep
    app.dependency_overrides[auth.require_admin] = admin_only
    app.dependency_overrides[auth.get_current_user] = lambda: user["u"]
    return SimpleNamespace(client=TestClient(app), Session=Session, events=events, user=user,
                           tmp=tmp_path)


def make_token(env, max_uses=5, days=7, label="rollout"):
    """Create an enrolment token through the API.

    Returns:
        Response JSON (includes the secret).
    """
    r = env.client.post("/api/v1/agents/enrolment-tokens",
                        json={"label": label, "max_uses": max_uses, "expires_in_days": days})
    assert r.status_code == 201, r.text
    return r.json()


def reg_body(host="app01", **kw):
    """Agent registration payload.

    Returns:
        Request body dict.
    """
    return {"hostname": host, "ip_address": "10.0.0.5", "os_type": "linux", "os_version": "6.1",
            "agent_version": "1.0", "capabilities": ["cpu", "memory"],
            "application_name": "payments", "environment": "production",
            "support_team": {"name": "Support", "email": "support@example.com"}, **kw}


def register(env, secret, host="app01"):
    """Register an agent through the API.

    Returns:
        The response.
    """
    return env.client.post("/api/v1/agents/register", json=reg_body(host),
                           headers={H: secret} if secret else {})


def actions(env, outcome=None):
    """Audit actions recorded so far.

    Returns:
        Action names.
    """
    return [e["action"] for e in env.events if outcome is None or e.get("outcome", "success") == outcome]


# ------------------------------------------------------------------ enrolment tokens

def test_token_created_once_and_never_listed(env):
    """The secret is returned once at creation and never listed."""
    tok = make_token(env)
    assert tok["secret"].startswith("axe_") and len(tok["secret"]) >= 40
    assert tok["state"] == "active" and tok["max_uses"] == 5 and tok["use_count"] == 0
    listed = env.client.get("/api/v1/agents/enrolment-tokens").json()
    assert "secret" not in listed[0] and tok["secret"] not in str(listed)
    assert listed[0]["hint"].endswith(tok["secret"][-4:])
    with env.Session() as db:                                    # only the hash is stored
        row = db.query(m.AgentEnrolmentToken).one()
        assert row.secret_hash == lifecycle.hash_secret(tok["secret"]) and tok["secret"] not in str(vars(row))
    assert "agent.enrolment.create" in actions(env)
    assert tok["secret"] not in str(env.events)


@pytest.mark.parametrize("body", [
    {"label": "", "max_uses": 1, "expires_in_days": 1},
    {"label": "x", "max_uses": 0, "expires_in_days": 1},
    {"label": "x", "max_uses": 1001, "expires_in_days": 1},
    {"label": "x", "max_uses": 1, "expires_in_days": 0},
    {"label": "x", "max_uses": 1, "expires_in_days": 31},
])
def test_token_limits(env, body):
    """Out-of-range token values are refused."""
    assert env.client.post("/api/v1/agents/enrolment-tokens", json=body).status_code == 400


def test_tokens_are_admin_only(env):
    """Only admins can create, list and revoke tokens."""
    env.user["u"] = VIEWER
    assert env.client.get("/api/v1/agents/enrolment-tokens").status_code == 403
    assert env.client.post("/api/v1/agents/enrolment-tokens",
                           json={"label": "x", "max_uses": 1, "expires_in_days": 1}).status_code == 403


# ------------------------------------------------------------------ registration

def test_register_without_or_with_bad_token_refused(env):
    """Registration without a valid token is refused."""
    r = register(env, None)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "enrolment_missing"
    r = register(env, "axe_not-a-real-token")
    assert r.status_code == 401 and r.json()["detail"]["code"] == "enrolment_unknown"
    assert actions(env, "denied") == ["agent.register", "agent.register"]
    with env.Session() as db:
        assert db.query(m.Server).count() == 0


def test_register_consumes_one_use_and_audits(env):
    """A registration consumes one use and is audited."""
    tok = make_token(env, max_uses=2)
    r = register(env, tok["secret"], "app01")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["api_key"] and body["server_id"]
    assert register(env, tok["secret"], "app02").status_code == 200
    r = register(env, tok["secret"], "app03")
    assert r.status_code == 401 and r.json()["detail"]["code"] == "enrolment_used_up"
    listed = env.client.get("/api/v1/agents/enrolment-tokens").json()[0]
    assert listed["use_count"] == 2 and listed["state"] == "used_up"
    ok = [e for e in env.events if e["action"] == "agent.register" and e.get("outcome", "success") == "success"]
    assert len(ok) == 2 and ok[0]["details"]["enrolment_label"] == "rollout"
    assert tok["secret"] not in str(env.events) and body["api_key"] not in str(env.events)


def test_expired_and_revoked_tokens_refused(env):
    """Expired and revoked tokens are refused."""
    tok = make_token(env)
    with env.Session() as db:
        row = db.query(m.AgentEnrolmentToken).one()
        row.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
        db.commit()
    assert register(env, tok["secret"]).json()["detail"]["code"] == "enrolment_expired"
    tok2 = make_token(env, label="second")
    r = env.client.post(f"/api/v1/agents/enrolment-tokens/{tok2['id']}/revoke")
    assert r.status_code == 200 and r.json()["state"] == "revoked" and r.json()["revoked_by"] == "admin"
    assert register(env, tok2["secret"]).json()["detail"]["code"] == "enrolment_revoked"
    assert "agent.enrolment.revoke" in actions(env)


def test_registered_hostname_refused_case_insensitive(env):
    """A hostname that already has a registered agent is refused, whatever its case."""
    tok = make_token(env)
    first = register(env, tok["secret"], "App01").json()
    r = register(env, tok["secret"], "app01")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "hostname_registered"
    assert first["server_id"] in r.json()["detail"]["message"]
    assert env.client.get("/api/v1/agents/enrolment-tokens").json()[0]["use_count"] == 1   # not consumed


def test_last_use_cannot_be_taken_twice(env):
    """The last use of a token cannot be consumed twice."""
    tok = make_token(env, max_uses=1)
    with env.Session() as db:
        assert lifecycle.consume_token(db, tok["id"]) is True
        assert lifecycle.consume_token(db, tok["id"]) is False
        lifecycle.release_token(db, tok["id"])
        assert db.query(m.AgentEnrolmentToken).one().use_count == 0


# ------------------------------------------------------------------ removal

def _registered(env, host="app01"):
    """Register an agent and return its registration.

    Returns:
        Response JSON (server_id, api_key, ...).
    """
    tok = make_token(env, label=f"t-{host}")
    r = register(env, tok["secret"], host)
    assert r.status_code == 200, r.text
    return r.json()


def _auth(key):
    """Agent authorization header.

    Returns:
        Headers dict.
    """
    return {"Authorization": f"Bearer {key}"}


def test_agent_deregister_archives_monitors(env, monkeypatch):
    """Agent self-removal archives its monitors and revokes its key."""
    a = _registered(env)
    sid, key = a["server_id"], a["api_key"]
    assert env.client.get(f"/api/v1/config/monitors-yaml/{sid}", headers=_auth(key)).status_code == 200
    folder = env.tmp / "agent-config" / sid
    assert (folder / "monitors.yaml").exists()
    with env.Session() as db:
        n_monitors = db.query(m.MonitorConfig).filter_by(server_id=sid).count()
        assert n_monitors >= 2

    r = env.client.post("/api/v1/agents/deregister", headers=_auth(key))
    assert r.status_code == 200, r.text
    assert r.json()["monitors_archived"] == n_monitors and r.json()["monitors_yaml_removed"] is True
    assert not folder.exists()
    with env.Session() as db:
        srv = db.get(m.Server, sid)
        assert srv.status == "decommissioned" and srv.api_key_hash is None
        assert srv.decommissioned_by == "agent:app01" and srv.decommissioned_at
        mons = db.query(m.MonitorConfig).filter_by(server_id=sid).all()
        assert len(mons) == n_monitors and all(x.archived_at and not x.enabled for x in mons)
        assert db.query(m.ApplicationServer).filter_by(server_id=sid).count() == 1   # kept for reports
    ev = [e for e in env.events if e["action"] == "agent.deregister"][0]
    assert ev["actor_type"] == "agent" and ev["details"]["monitors_archived"] == n_monitors

    # the key is dead everywhere
    assert env.client.post("/api/v1/agents/heartbeat", json={"server_id": sid}, headers=_auth(key)).status_code == 401
    assert env.client.get(f"/api/v1/config/monitors-yaml/{sid}", headers=_auth(key)).status_code == 401
    assert env.client.post("/api/v1/agents/deregister", headers=_auth(key)).status_code == 401
    # the heartbeat never re-creates the deleted folder
    with env.Session() as db:
        assert config._regenerate_monitors_yaml(sid, db) is None
    assert not folder.exists()

    # list hides it; detail and include_decommissioned show it
    assert env.client.get("/api/v1/agents/").json() == []
    assert len(env.client.get("/api/v1/agents/?include_decommissioned=true").json()) == 1
    assert env.client.get(f"/api/v1/agents/{sid}").json()["status"] == "decommissioned"
    # archived monitors: hidden from the list, read-only
    assert env.client.get(f"/api/v1/config/monitor-configs?server_id={sid}").json() == []
    all_m = env.client.get(f"/api/v1/config/monitor-configs?server_id={sid}&include_archived=true").json()
    assert len(all_m) == n_monitors
    assert env.client.patch(f"/api/v1/config/monitor-configs/{all_m[0]['monitor_config_id']}",
                            json={"enabled": True}).status_code == 409

    # reinstall on the same host: a NEW server id, old row kept
    b = _registered(env, "app01")
    assert b["server_id"] != sid
    with env.Session() as db:
        assert db.query(m.Server).filter(m.Server.hostname == "app01").count() == 2


def test_admin_remove(env):
    """An admin can remove an agent; viewers cannot."""
    a = _registered(env)
    sid = a["server_id"]
    env.user["u"] = VIEWER
    assert env.client.delete(f"/api/v1/agents/{sid}").status_code == 403
    env.user["u"] = ADMIN
    r = env.client.delete(f"/api/v1/agents/{sid}")
    assert r.status_code == 200 and r.json()["hostname"] == "app01"
    assert env.client.delete(f"/api/v1/agents/{sid}").status_code == 409
    assert env.client.delete("/api/v1/agents/00000000-0000-0000-0000-000000000000").status_code == 404
    ev = [e for e in env.events if e["action"] == "agent.remove"][0]
    assert ev["actor"] == "admin" and ev["details"]["method"] == "dashboard"
    with env.Session() as db:
        assert db.get(m.Server, sid).decommissioned_by == "admin"


def test_monitors_yaml_needs_own_key(env):
    """monitors.yaml is served only with the agent's own key."""
    a, b = _registered(env, "app01"), _registered(env, "app02")
    url = f"/api/v1/config/monitors-yaml/{a['server_id']}"
    assert env.client.get(url).status_code == 401
    assert env.client.get(url + "/checksum").status_code == 401
    assert env.client.get(url, headers=_auth(b["api_key"])).status_code == 403
    assert env.client.get(url, headers=_auth("agent_x_bogus")).status_code == 401
    assert env.client.get(url, headers=_auth(a["api_key"])).status_code == 200


def test_heartbeat_and_check_scripts_only_for_own_server(env):
    """Heartbeat and check-scripts refuse another server's id."""
    a, b = _registered(env, "app01"), _registered(env, "app02")
    r = env.client.post("/api/v1/agents/heartbeat", json={"server_id": b["server_id"]},
                        headers=_auth(a["api_key"]))
    assert r.status_code == 403
    r = env.client.post("/api/v1/agents/check-scripts",
                        json={"server_id": b["server_id"], "local_scripts": []}, headers=_auth(a["api_key"]))
    assert r.status_code == 403


def test_metrics_need_own_key_and_own_monitors(env, monkeypatch):
    """Metrics need the agent's own key and its own monitors."""
    monkeypatch.setattr(metrics, "_note_agent_clock", lambda *a, **k: None)
    monkeypatch.setattr(metrics, "process_all_metrics_state", lambda *a, **k: None)
    a, b = _registered(env, "app01"), _registered(env, "app02")
    with env.Session() as db:
        mine = db.query(m.MonitorConfig).filter_by(server_id=a["server_id"]).first().id
        theirs = db.query(m.MonitorConfig).filter_by(server_id=b["server_id"]).first().id

    def payload(sid, mc):
        """Metrics payload for a monitor.

        Returns:
            Request body dict.
        """
        return {"server_id": sid, "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                "metrics": [{"schema_version": "1.0", "type": "cpu", "metric_name": "cpu_percent", "value": 12.5, "status": "pass",
                             "monitor_config_id": mc, "details": {"cpu_percent": 12.5}}]}
    assert env.client.post("/api/v1/metrics/", json=payload(a["server_id"], mine)).status_code == 401
    r = env.client.post("/api/v1/metrics/", json=payload(b["server_id"], mine), headers=_auth(a["api_key"]))
    assert r.status_code == 403
    r = env.client.post("/api/v1/metrics/", json=payload(a["server_id"], theirs), headers=_auth(a["api_key"]))
    assert r.status_code == 200 and r.json()["metrics_stored"] == 0 and r.json()["metrics_rejected"] == 1
    r = env.client.post("/api/v1/metrics/", json=payload(a["server_id"], mine), headers=_auth(a["api_key"]))
    assert r.status_code == 200 and r.json()["metrics_stored"] == 1, r.text


def test_agent_list_needs_signed_in_user():
    """Without the test override the list/detail routes require a user (JWT)."""
    app = FastAPI()
    app.include_router(agents.router)
    c = TestClient(app)
    assert c.get("/api/v1/agents/").status_code == 401
    assert c.get("/api/v1/agents/00000000-0000-0000-0000-000000000000").status_code == 401
    assert c.post("/api/v1/agents/deregister").status_code == 401


def test_audit_middleware_attributes_admin_on_agent_paths():
    """The audit middleware attributes an admin's call on an agent path to the admin."""
    from axara.community.api.middleware import audit as mw
    tok = auth.create_token({"sub": "admin", "user_id": 1, "role": "admin"}) \
        if "data" in auth.create_token.__code__.co_varnames else None
    req = SimpleNamespace(url=SimpleNamespace(path="/api/v1/agents/abc"),
                          headers={"authorization": "Bearer not-a-jwt"})
    assert mw.actor_for(req)[0] == "agent"
    assert mw.should_audit("DELETE", "/api/v1/agents/abc") is False      # explicit events instead
    if tok:
        req.headers = {"authorization": f"Bearer {tok}"}
        assert mw.actor_for(req)[:2] == ("user", "admin")


def test_default_monitors_enabled_at_registration(env):
    """The core enables cpu, memory, disk, network and ssl at registration."""
    caps = ["cpu", "memory", "disk", "network", "url", "log", "process", "port", "ssl"]
    tok = make_token(env, label="defaults")
    r = env.client.post("/api/v1/agents/register", json=reg_body("app09", capabilities=caps),
                        headers={H: tok["secret"]})
    assert r.status_code == 200, r.text
    sid = r.json()["server_id"]
    with env.Session() as db:
        on = {mc.type for mc in db.query(m.MonitorConfig).filter_by(server_id=sid, enabled=True)}
    assert on == {"cpu", "memory", "disk", "network", "ssl"}
