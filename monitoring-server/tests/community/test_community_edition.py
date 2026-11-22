# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Community edition stands alone: the core never imports axara.enterprise, a server
without the Enterprise package serves only the core API and runs no plugin checks, plugin
registration is all-or-nothing, and request checks fail closed."""
import ast
import json
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[2]
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/axara-community-import.db")
os.environ.setdefault("SECRET_KEY", "test-secret")
sys.modules.setdefault("chromadb", types.ModuleType("chromadb"))

from fastapi import HTTPException  # noqa: E402

from axara.community.plugins import loader  # noqa: E402
from axara.community.plugins.registry import Registry  # noqa: E402

COMMUNITY = SERVER / "axara" / "community"


def test_core_never_imports_enterprise():
    """No module of axara.community imports axara.enterprise."""
    offenders = []
    for path in COMMUNITY.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.startswith("axara.enterprise"):
                    rel = path.relative_to(SERVER).as_posix()
                    offenders.append(f"{rel}:{node.lineno} {name}")
    assert offenders == []


_COMMUNITY_SERVER = textwrap.dedent('''
    import importlib.abc, json, os, sys, types
    class _NoEnterprise(importlib.abc.MetaPathFinder):
        """Behave as a Community image: axara.enterprise is not installed."""
        def find_spec(self, name, path=None, target=None):
            if name == "axara.enterprise" or name.startswith("axara.enterprise."):
                raise ModuleNotFoundError(f"No module named {name!r}", name=name)
            return None
    sys.meta_path.insert(0, _NoEnterprise())
    sys.modules.setdefault("chromadb", types.ModuleType("chromadb"))
    from fastapi.testclient import TestClient
    import axara.community.api.main as main
    from axara.community.plugins.loader import get_registry
    reg = get_registry()
    c = TestClient(main.app)
    urls = ["/api/v1/incidents/", "/api/v1/agents/", "/api/v1/ssl-certificates/", "/api/v1/audit/events",
            "/api/v1/config/ticket-providers"]
    probe = {u: c.get(u).status_code for u in urls}
    prefixes = sorted({"/".join(p.split("/")[:4]) for p in main.app.openapi()["paths"]
                       if p.startswith("/api/")})
    import manage
    print(json.dumps({
        "plugins": reg.plugins, "probe": probe, "prefixes": prefixes,
        "checks": {k: len(v) for k, v in reg._checks.items()},
        "providers": reg.ticket_provider_names(), "migration_dirs": [str(p) for p in reg.migration_dirs()],
        "commands": sorted(reg.commands()), "core_commands": sorted(manage.CORE),
        "health": c.get("/health").status_code,
        "loaded_enterprise": sorted(m for m in sys.modules if m.startswith("axara.enterprise")),
    }))
''')


@pytest.fixture(scope="module")
def community():
    """What a server without the Enterprise package registers and serves."""
    env = {**os.environ, "DATABASE_URL": "sqlite:////tmp/axara-community-only.db", "SECRET_KEY": "test-secret",
           "LOG_DIR": "/tmp/axara-community-logs", "PYTHONPATH": str(SERVER)}
    out = subprocess.run([sys.executable, "-c", _COMMUNITY_SERVER], cwd=SERVER, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_community_server_has_no_enterprise_code(community):
    """Nothing from axara.enterprise is loaded, registered or checked."""
    assert community["plugins"] == [] and community["loaded_enterprise"] == []
    assert all(n == 0 for n in community["checks"].values())
    assert community["providers"] == ["jira"] and community["migration_dirs"] == []
    assert community["commands"] == [] and "migrate" in community["core_commands"]


CORE_API = ["/api/v1/agents", "/api/v1/audit", "/api/v1/auth", "/api/v1/config", "/api/v1/dashboard",
            "/api/v1/incidents", "/api/v1/metrics", "/api/v1/scripts", "/api/v1/ssl-certificates"]


def test_community_server_routes(community):
    """Core routes answer (401 without sign-in), and only the core API is mounted."""
    probe = community["probe"]
    assert community["health"] == 200
    for core, status in probe.items():
        assert status == 401, (core, status)
    assert community["prefixes"] == CORE_API


def test_failed_plugin_leaves_no_registrations(monkeypatch):
    """A plugin whose register() fails part-way is rolled back completely."""
    mod = types.ModuleType("axara_test_bad_plugin")

    def register(reg):
        """Register a gate and a check, then fail."""
        reg.set_licence_gates(lambda f: False, lambda f: False)
        reg.add_check("user", lambda u: None)
        reg.add_command("x", lambda a: 0, "x")
        raise RuntimeError("broken plugin")
    mod.register = register
    monkeypatch.setitem(sys.modules, "axara_test_bad_plugin", mod)
    reg = loader.build_registry(builtin=["axara_test_bad_plugin"], use_entry_points=False)
    assert reg.plugins == [] and reg.commands() == {} and not reg._checks["user"]
    assert reg.feature_allowed("any-feature") and reg.function_allowed("incidents")   # no gate left behind


def test_checks_fail_closed():
    """A check refuses with its HTTPException; a check that crashes refuses with 403."""
    reg = Registry()
    reg.run_checks("user", "u")
    reg.add_check("user", lambda u: (_ for _ in ()).throw(HTTPException(status_code=403, detail="blocked")))
    with pytest.raises(HTTPException) as e:
        reg.run_checks("user", "u")
    assert e.value.detail == "blocked"
    reg2 = Registry()
    reg2.add_check("agent_intake", lambda sid: 1 / 0)
    with pytest.raises(HTTPException) as e:
        reg2.run_checks("agent_intake", "s1")
    assert e.value.status_code == 403 and e.value.detail["code"] == "check_failed"
    with pytest.raises(ValueError):
        reg.add_check("nope", lambda: None)


def test_events_and_template_conditions(tmp_path):
    """Event hooks are isolated unless strict; template folders follow their condition."""
    reg = Registry()
    seen = []
    reg.add_event_hook("agents_changed", lambda: 1 / 0)
    reg.add_event_hook("agents_changed", lambda: seen.append("ok"))
    reg.emit("agents_changed")
    assert seen == ["ok"]
    with pytest.raises(ZeroDivisionError):
        reg.emit("agents_changed", strict=True)
    state = {"active": True}
    reg.add_monitor_templates(tmp_path, condition=lambda: state["active"])
    assert reg.monitor_template_dirs() == [tmp_path]
    state["active"] = False
    assert reg.monitor_template_dirs() == []


def test_plugin_scripts_are_served_and_offered_as_new(tmp_path, monkeypatch):
    """A plugin's monitor script is listed, downloadable and offered to agents that lack it;
    a core script of the same name always wins."""
    from axara.community.api.routes import scripts
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "tls_monitor.py").write_text('__version__ = "1.0.0"\n', encoding="utf-8")
    (extra / "cpu_monitor.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
    reg = Registry()
    reg.add_monitor_templates(extra)
    monkeypatch.setattr(loader, "_registry", reg)
    listed = {s["script_name"]: s["version"] for s in scripts.list_scripts()}
    assert listed["tls_monitor.py"] == "1.0.0" and listed["cpu_monitor.py"] != "9.9.9"
    assert scripts.get_script("tls_monitor.py", "1.0.0")["content"].startswith("__version__")
    from axara.community.api.routes import agents
    names = {s["script_name"] for s in agents._get_monitor_scripts()}
    assert "tls_monitor.py" in names


def test_ssl_monitor_rates_expiry():
    """Community SSL monitor: expired or within critical = fail, within warning = warning."""
    sys.path.insert(0, str(SERVER / "config" / "monitor_templates"))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ssl_tpl", SERVER / "config" / "monitor_templates" / "ssl_monitor.py",
        submodule_search_locations=None)
    src = (SERVER / "config" / "monitor_templates" / "ssl_monitor.py").read_text(encoding="utf-8")
    assert "min_tls_version" not in src and "protocols_supported" not in src
    ns = {}
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "expiry_status")
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "ssl_monitor", "exec"), ns)
    rate = ns["expiry_status"]
    assert rate(-1, 30, 7) == "fail" and rate(7, 30, 7) == "fail" and rate(8, 30, 7) == "warning"
    assert rate(30, 30, 7) == "warning" and rate(31, 30, 7) == "pass"
    agent_copy = SERVER.parent / "monitoring-agent" / "axara" / "agent" / "monitors" / "ssl_monitor.py"
    assert agent_copy.read_text(encoding="utf-8") == src                 # agent ships the same script
