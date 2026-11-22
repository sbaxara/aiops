# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Builds the plugin registry once per process.

Sources, in order: the core's own registrations (plugins.core), the modules built into
this image (BUILTIN_PLUGINS), then Python entry points in the group `axara.plugins`
from packages installed in the image. Nothing is ever loaded from a path or module name
taken from the environment, the database or a request.
"""
import importlib
import logging
import threading
from importlib import metadata
from typing import List, Optional

from axara.community.plugins.registry import Registry

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "axara.plugins"
# Plugin modules loaded when present in the image. The Enterprise package (axara.enterprise)
# is simply absent from a Community image; release builds also install it with an
# `axara.plugins` entry point (phase 5).
BUILTIN_PLUGINS: List[str] = ["axara.enterprise.plugin"]

_registry: Optional[Registry] = None
_lock = threading.Lock()


def _register_module(reg: Registry, module_name: str) -> None:
    """Import a module and call its register(registry).

    Args:
        reg: Registry being built.
        module_name: Dotted module name from BUILTIN_PLUGINS or an installed entry point.

    Raises:
        TypeError: when the module has no callable register().
    """
    mod = importlib.import_module(module_name)
    fn = getattr(mod, "register", None)
    if not callable(fn):
        raise TypeError(f"plugin {module_name} has no register(registry)")
    _register_atomic(reg, fn, module_name)


def _register_atomic(reg: Registry, fn, name: str) -> None:
    """Run a plugin's register(); if it fails part-way, remove everything it registered.

    Args:
        reg: Registry being built.
        fn: The plugin's register function.
        name: Plugin name (recorded in reg.plugins on success).

    Raises:
        Exception: whatever register() raised (the registry is back to its earlier state).
    """
    snap = reg.snapshot()
    try:
        fn(reg)
    except BaseException:
        reg.restore(snap)
        raise
    reg.plugins.append(name)


def build_registry(builtin: Optional[List[str]] = None, use_entry_points: bool = True) -> Registry:
    """Build a new registry from the core, the built-in modules and installed entry points.

    Args:
        builtin: Built-in plugin modules (defaults to BUILTIN_PLUGINS); tests pass [].
        use_entry_points: Also load installed `axara.plugins` entry points.

    Returns:
        The populated registry. A plugin that fails to load is logged and skipped; the core
        always loads.
    """
    from axara.community.plugins import core
    reg = Registry()
    core.register(reg)
    for name in (BUILTIN_PLUGINS if builtin is None else builtin):
        try:
            _register_module(reg, name)
        except ModuleNotFoundError as e:
            if e.name and (name == e.name or name.startswith(e.name + ".")):   # not in this image (Community)
                logger.debug("built-in plugin %s not present", name)
            else:
                logger.exception("built-in plugin %s failed to load", name)
        except Exception:
            logger.exception("built-in plugin %s failed to load", name)
    if use_entry_points:
        for ep in metadata.entry_points(group=ENTRY_POINT_GROUP):
            try:
                obj = ep.load()
                fn = obj if callable(obj) else getattr(obj, "register", None)
                if not callable(fn):
                    raise TypeError("entry point is not callable and has no register()")
                _register_atomic(reg, fn, ep.value)
            except Exception:
                logger.exception("plugin entry point %s failed to load", ep.value)
    logger.info("plugins loaded: %s", ", ".join(reg.plugins) or "none (core only)")
    return reg


def get_registry() -> Registry:
    """The process-wide registry, built on first use.

    Returns:
        The shared Registry.
    """
    global _registry
    if _registry is None:
        with _lock:
            if _registry is None:
                _registry = build_registry()
    return _registry


def set_registry(reg: Optional[Registry]) -> None:
    """Replace the process-wide registry (tests only).

    Args:
        reg: Registry to use, or None to rebuild on next use.
    """
    global _registry
    _registry = reg
