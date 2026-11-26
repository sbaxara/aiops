# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FastAPI dependencies that run the plugin registry's checks and gates at request time.

The core declares these on its routes; in Community no check is registered, so they only
require sign-in (require_user) or do nothing. Plugins may add their own checks.
"""
from typing import Callable

from fastapi import Depends

from axara.community.api.routes.auth import get_current_user
from axara.community.database.models import User


def _registry():
    """The process registry (imported late: the registry imports plugin code)."""
    from axara.community.plugins.loader import get_registry
    return get_registry()


def require_user(user: User = Depends(get_current_user)) -> User:
    """A signed-in user that every registered "user" check accepts.

    Args:
        user: From get_current_user.

    Returns:
        The user.

    Raises:
        HTTPException: 401 when not signed in; a plugin check's refusal (e.g. 403).
    """
    _registry().run_checks("user", user)
    return user


def require_agent_intake() -> None:
    """Router dependency for agent-facing routes without a server id (e.g. script downloads).

    Raises:
        HTTPException: a registered "agent_intake" check's refusal.
    """
    _registry().run_checks("agent_intake", None)


def require_function(function: str) -> Callable:
    """Endpoint dependency: 403 while a stage-gated function (incidents, ...) is off.

    Args:
        function: One of plugins.registry.FUNCTIONS.

    Returns:
        The dependency.
    """
    def _dep():
        """Refuse the request while the function is off.

        Raises:
            HTTPException: 403 when a plugin gate turns the function off.
        """
        _registry().function_guard(function)()
    return _dep

