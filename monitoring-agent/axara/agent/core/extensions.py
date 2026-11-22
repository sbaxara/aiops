# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Optional agent extensions.

An extension is a package in the ``axara`` namespace whose name starts with ``agent_`` (for
example ``axara.agent_example``) and which defines ``register(hooks)``. It can add capabilities
(monitor types reported at registration) and steps run once per maintenance check.

Extensions are found in two ways:

* packages named ``axara.agent_*`` on the Python path (script mode);
* names listed in ``axara.agent._build_extensions.EXTENSIONS``, a module the build script writes
  when it compiles extensions into the agent (compiled mode).

The agent runs without any extension. An extension that fails to load is logged and skipped.
"""
import importlib
import logging
import pkgutil
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

EXT_PREFIX = "agent_"


class AgentHooks:
    """What extensions add to the agent."""

    def __init__(self):
        """Start with no capabilities, steps or names."""
        self.capabilities: List[str] = []
        self.cycle_steps: List[Tuple[str, Callable]] = []
        self.loaded: List[str] = []

    def add_capabilities(self, *names: str) -> None:
        """Report more monitor types at registration.

        Args:
            *names: Monitor type names.
        """
        for n in names:
            if n not in self.capabilities:
                self.capabilities.append(n)

    def add_cycle_step(self, name: str, fn: Callable) -> None:
        """Run a step once per maintenance check, after the monitor updates.

        Args:
            name: Step name, used in log messages.
            fn: Called with the running MonitoringAgent. Exceptions are logged, not raised.
        """
        self.cycle_steps.append((name, fn))


_hooks: Optional[AgentHooks] = None


def _candidate_names() -> List[str]:
    """Extension module names, build-listed first, without duplicates.

    Returns:
        Fully qualified module names.
    """
    names: List[str] = []
    try:
        from axara.agent import _build_extensions  # written by the build script
        names += [str(n) for n in getattr(_build_extensions, "EXTENSIONS", ())]
    except ImportError:
        pass
    try:
        import axara
        for m in pkgutil.iter_modules(list(getattr(axara, "__path__", []))):
            if m.name.startswith(EXT_PREFIX) and m.ispkg:
                names.append(f"axara.{m.name}")
    except Exception:
        logger.debug("extension discovery failed", exc_info=True)
    seen, out = set(), []
    for n in names:
        if n not in seen and n.startswith("axara." + EXT_PREFIX):
            seen.add(n)
            out.append(n)
    return out


def load_extensions(reload: bool = False) -> AgentHooks:
    """Load the extensions once and return what they registered.

    Args:
        reload: Load again (tests).

    Returns:
        The hooks.
    """
    global _hooks
    if _hooks is not None and not reload:
        return _hooks
    hooks = AgentHooks()
    for name in _candidate_names():
        try:
            mod = importlib.import_module(name)
            register = getattr(mod, "register", None)
            if not callable(register):
                logger.warning("agent extension %s has no register(hooks); skipped", name)
                continue
            register(hooks)
            hooks.loaded.append(name)
            logger.info("agent extension loaded: %s", name)
        except Exception:
            logger.exception("agent extension %s could not be loaded", name)
    _hooks = hooks
    return hooks


def run_cycle_steps(agent) -> None:
    """Run every registered cycle step; errors are logged.

    Args:
        agent: The running MonitoringAgent.
    """
    for name, fn in load_extensions().cycle_steps:
        try:
            fn(agent)
        except Exception as e:
            logger.error(f"{name} step error: {e}", exc_info=True)
