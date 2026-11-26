# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Load the monitor scripts (axara.agent.monitors) from disk, and reload them on update.

The monitors are plain .py files in <agent home>/axara/agent/monitors/, kept as source so the
server can update them (core.script_updater). In the compiled agent the axara.agent package is
compiled, and a compiled package does not find a plain-source subpackage through the normal
import system, so the package is loaded explicitly from its folder. The same loader is used
when the agent runs from Python source, so both behave alike.
"""
import importlib
import importlib.util
import logging
import os
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Dict

logger = logging.getLogger(__name__)

PACKAGE = "axara.agent.monitors"
# File names of monitor scripts (also the only names the script updater installs).
SCRIPT_NAME = re.compile(r"^[a-z0-9_]+_monitor\.py$")


def agent_home() -> Path:
    """The agent's folder (holds config/, logs/, queue/ and axara/agent/monitors/).

    Returns:
        AXARA_AGENT_HOME when set (the launcher sets it), else the folder three levels above
        this package (the source checkout, or the compiled agent's folder).
    """
    env = os.environ.get("AXARA_AGENT_HOME")
    return Path(env) if env else Path(__file__).resolve().parents[3]


def monitors_dir() -> Path:
    """Folder of the monitor scripts.

    Returns:
        <agent home>/axara/agent/monitors.
    """
    return agent_home() / "axara" / "agent" / "monitors"


def load() -> ModuleType:
    """The monitors package, loading it from disk when it is not loaded yet.

    Returns:
        The axara.agent.monitors module (exports every monitor class).

    Raises:
        ImportError: when the folder has no __init__.py or a monitor fails to import.
    """
    mod = sys.modules.get(PACKAGE)
    if mod is not None:
        return mod
    folder = monitors_dir()
    init = folder / "__init__.py"
    if not init.is_file():
        raise ImportError(f"monitor scripts not found in {folder}")
    spec = importlib.util.spec_from_file_location(PACKAGE, init, submodule_search_locations=[str(folder)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        evict()
        raise
    return mod


def evict() -> list:
    """Forget the loaded monitor modules so the next load() reads the files again.

    Returns:
        Names of the modules removed from sys.modules.
    """
    stale = [k for k in sys.modules if k == PACKAGE or k.startswith(PACKAGE + ".")]
    for key in stale:
        del sys.modules[key]
    return stale


def extra_monitor_classes() -> Dict[str, type]:
    """Monitor classes from scripts that declare their own type (MONITOR_TYPE).

    The built-in monitors are exported by the package's __init__; a script added later by
    the server (e.g. one a server plugin adds) is not, so every *_monitor.py file is
    imported and any class with a MONITOR_TYPE attribute is offered for that type.
    A script that fails to import is logged and skipped.

    Returns:
        {monitor type: class}.
    """
    load()
    out: Dict[str, type] = {}
    for path in sorted(monitors_dir().glob("*_monitor.py")):
        if not SCRIPT_NAME.match(path.name):
            continue
        name = f"{PACKAGE}.{path.stem}"
        try:
            mod = sys.modules.get(name) or importlib.import_module(name)
        except Exception:
            logger.exception("monitor script %s could not be loaded", path.name)
            continue
        for obj in vars(mod).values():
            mtype = getattr(obj, "MONITOR_TYPE", None) if isinstance(obj, type) else None
            if isinstance(mtype, str) and mtype and getattr(obj, "__module__", "") == name:
                out[mtype] = obj
    return out
