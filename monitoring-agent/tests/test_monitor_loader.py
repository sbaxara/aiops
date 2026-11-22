# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Monitor scripts are loaded from <agent home>/axara/agent/monitors and reloaded after an update."""
import shutil
import sys
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1]
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))

from axara.agent.core import monitor_loader  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A copy of the monitor scripts in a temporary agent home."""
    shutil.copytree(AGENT / "axara" / "agent" / "monitors", tmp_path / "axara" / "agent" / "monitors",
                    ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setenv("AXARA_AGENT_HOME", str(tmp_path))
    monitor_loader.evict()
    yield tmp_path
    monitor_loader.evict()


def test_loads_every_monitor_class(home):
    """All monitor classes come from the agent home's monitors folder."""
    m = monitor_loader.load()
    assert monitor_loader.monitors_dir() == home / "axara" / "agent" / "monitors"
    for name in ("CPUMonitor", "SSLMonitor", "BrowserMonitor", "PortMonitor"):
        assert hasattr(m, name)
    assert Path(sys.modules["axara.agent.monitors.cpu_monitor"].__file__).parent == monitor_loader.monitors_dir()
    assert monitor_loader.load() is m                                  # cached until evicted


def test_updated_script_is_read_after_evict(home):
    """After the server updates a script, evict() makes the next load read the new file."""
    first = monitor_loader.load().CPUMonitor
    f = monitor_loader.monitors_dir() / "cpu_monitor.py"
    f.write_text(f.read_text(encoding="utf-8") + "\nRELOADED_MARKER = 1\n", encoding="utf-8")
    assert "axara.agent.monitors.cpu_monitor" in monitor_loader.evict()
    monitor_loader.load()
    assert sys.modules["axara.agent.monitors.cpu_monitor"].RELOADED_MARKER == 1
    assert monitor_loader.load().CPUMonitor is not first


def test_missing_folder_is_an_import_error(tmp_path, monkeypatch):
    """A home without monitor scripts fails clearly and leaves nothing half-loaded."""
    monkeypatch.setenv("AXARA_AGENT_HOME", str(tmp_path))
    monitor_loader.evict()
    with pytest.raises(ImportError, match="monitor scripts not found"):
        monitor_loader.load()
    assert "axara.agent.monitors" not in sys.modules


def test_new_script_installed_and_its_type_found(home):
    """A script the server offers as new is installed; its MONITOR_TYPE class is offered."""
    import hashlib
    from types import SimpleNamespace
    from axara.agent.core.script_updater import ScriptUpdater
    content = ("from .base_monitor import BaseMonitor\n__version__ = '1.0.0'\n"
               "class XMonitor(BaseMonitor):\n    MONITOR_TYPE = 'x'\n"
               "    def check(self):\n        return {}\n")
    sender = SimpleNamespace(download_script=lambda name, version: content)
    upd = ScriptUpdater(SimpleNamespace(api_sender=sender))
    ok = upd.apply_updates([
        {"script_name": "x_monitor.py", "current_version": None, "latest_version": "1.0.0",
         "checksum": "sha256:" + hashlib.sha256(content.encode()).hexdigest()},
        {"script_name": "../evil.py", "current_version": None, "latest_version": "1",
         "checksum": "sha256:0"},
    ])
    assert ok and (monitor_loader.monitors_dir() / "x_monitor.py").is_file()
    assert not (home / "axara" / "agent" / "evil.py").exists()
    monitor_loader.evict()
    assert "x" in monitor_loader.extra_monitor_classes()


def test_ssl_monitor_probes_ports_in_parallel(home, monkeypatch):
    """Ports are probed concurrently, so slow ports do not add up."""
    cls = monitor_loader.load().SSLMonitor
    mon = cls({"monitor_config_id": "m", "name": "ssl", "type": "ssl", "config": {"timeout": 1}})
    monkeypatch.setattr(mon, "_get_listening_ports", lambda: [("127.0.0.1", 10000 + i, "0.0.0.0") for i in range(12)])
    import time as _t

    def slow(ip, port, bind, timeout, w, c):
        """Probe stand-in taking 0.5 s."""
        _t.sleep(0.5)
        return {"ip": bind, "port": port, "ssl": False, "status": "pass"}
    monkeypatch.setattr(mon, "_probe_port", slow)
    t0 = _t.monotonic()
    r = mon.check()
    assert _t.monotonic() - t0 < 2.5 and r["details"]["scanned_ports"] == 12
    assert [c["port"] for c in r["details"]["certificates"]] == [10000 + i for i in range(12)]   # order kept
    assert cls.RUN_TIMEOUT >= 120
