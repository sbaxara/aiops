# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Collector timing: a monitor's RUN_TIMEOUT is its allowance, and a monitor still running
from an earlier pass is not started again."""
import sys
import threading
import time
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))

from axara.agent.core.collector import MetricsCollector  # noqa: E402


class _Slow:
    """Monitor stand-in whose check() blocks until released."""
    RUN_TIMEOUT = 0

    def __init__(self):
        """Start blocked."""
        self.release = threading.Event()
        self.calls = 0

    def check(self):
        """Count the call and wait for the release."""
        self.calls += 1
        self.release.wait(5)
        return {"status": "pass"}


def _collector(instance, timeout=1):
    """A collector holding one monitor (interval 0: due on every pass)."""
    c = MetricsCollector(max_workers=2, timeout=timeout)
    c._check_for_reload = lambda: None
    c.monitor_configs = {"slow": {"instance": instance, "monitor_config_id": "m1", "check_interval_seconds": 0,
                                  "config": {"type": "ssl", "config": {"timeout": 10}}}}
    return c


def test_run_timeout_attribute_is_the_allowance():
    """RUN_TIMEOUT on the class wins over config.timeout (which SSL uses per port)."""
    inst = _Slow()
    inst.RUN_TIMEOUT = 1
    c = _collector(inst, timeout=1)
    t0 = time.monotonic()
    res = c.collect_all_metrics()
    assert time.monotonic() - t0 < 4                       # 1 s allowance, not 10 s per config
    assert [r["status"] for r in res] == ["timeout"]
    inst.release.set()
    c.shutdown()


def test_running_monitor_not_started_again():
    """While the first run is still going, later passes skip the monitor."""
    inst = _Slow()
    inst.RUN_TIMEOUT = 1
    c = _collector(inst)
    c.collect_all_metrics()                                # times out, still running
    assert c.collect_all_metrics() == [] and inst.calls == 1
    inst.release.set()
    time.sleep(0.3)
    c.collect_all_metrics()                                # finished: runs again
    assert inst.calls == 2
    c.shutdown()


def test_has_monitor_type():
    """has_monitor_type reports only the types configured in monitors.yaml."""
    c = _collector(_Slow())
    assert c.has_monitor_type("ssl") and not c.has_monitor_type("url")
    c.shutdown()


class _Quick:
    """Monitor stand-in that returns at once."""

    def check(self):
        """A passing result."""
        return {"status": "pass"}


def test_new_monitor_runs_at_once_even_with_a_long_interval(monkeypatch):
    """A monitor not run yet is due now, whatever the host's uptime (monotonic clock)."""
    c = _collector(_Quick())
    c.monitor_configs["slow"]["check_interval_seconds"] = 86400
    c._last_run["slow"] = None
    monkeypatch.setattr(time, "monotonic", lambda: 60.0)          # host booted a minute ago
    assert len(c.collect_all_metrics()) == 1
    assert c.collect_all_metrics() == []                          # then waits a day
    c.shutdown()
