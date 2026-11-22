# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Agent extensions: discovery in the axara namespace, registration, and error isolation."""
import sys
import textwrap
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1]
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))

from axara.agent.core import extensions  # noqa: E402


@pytest.fixture
def ext_root(tmp_path, monkeypatch):
    """A sys.path folder that can hold axara.agent_* test packages."""
    (tmp_path / "axara").mkdir()
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    for name in [n for n in sys.modules if n.startswith("axara.agent_t")]:
        del sys.modules[name]
    extensions.load_extensions(reload=True)


def _package(root, name, body):
    """Write axara/<name>/__init__.py."""
    d = root / "axara" / name
    d.mkdir()
    (d / "__init__.py").write_text(textwrap.dedent(body), encoding="utf-8")


def test_extension_registers_capabilities_and_steps(ext_root):
    """A discovered extension adds capabilities and a cycle step that receives the agent."""
    _package(ext_root, "agent_tgood", """
        seen = []
        def register(hooks):
            hooks.add_capabilities("widget", "widget")
            hooks.add_cycle_step("widget", seen.append)
    """)
    hooks = extensions.load_extensions(reload=True)
    assert "axara.agent_tgood" in hooks.loaded
    assert hooks.capabilities.count("widget") == 1
    extensions.run_cycle_steps("AGENT")
    assert sys.modules["axara.agent_tgood"].seen == ["AGENT"]


def test_broken_extension_is_skipped(ext_root):
    """An extension that fails to import, or a failing step, never stops the agent."""
    _package(ext_root, "agent_tbroken", "raise RuntimeError('boom')\n")
    _package(ext_root, "agent_tstep", """
        def register(hooks):
            hooks.add_cycle_step("bad", lambda agent: 1 / 0)
    """)
    hooks = extensions.load_extensions(reload=True)
    assert "axara.agent_tbroken" not in hooks.loaded and "axara.agent_tstep" in hooks.loaded
    extensions.run_cycle_steps(object())                   # logs, does not raise


def test_other_names_are_ignored(ext_root):
    """Only axara.agent_* packages are extensions."""
    _package(ext_root, "tnotext", "def register(hooks):\n    hooks.add_capabilities('x')\n")
    assert "x" not in extensions.load_extensions(reload=True).capabilities
