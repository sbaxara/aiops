# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
AuditMiddleware — records every mutating user/integration request to the
tamper-evident audit log (common/audit.py).

  * Methods: POST, PUT, PATCH, DELETE only (reads are not logged; sensitive reads
    such as audit export log explicit events).
  * Actor: from a valid user JWT; agent paths without a JWT are 'agent';
    anything else is 'anonymous'.
  * Never captures request bodies (secrets stay out of the log); records route
    name, path template, path params, query string (redacted), status code.
  * Skips high-volume agent traffic and endpoints that emit richer explicit events
    themselves (login, register, logout, SSO).
"""
import logging

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from axara.community.common.audit import client_ip, record

logger = logging.getLogger(__name__)

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
_SKIP_PREFIXES = (
    "/api/v1/metrics",              # agent metric ingestion (every cycle)
    "/api/v1/agents/",              # heartbeat/check-scripts skipped; register, deregister,
                                    # removal and enrolment tokens emit explicit events
    "/api/v1/config/monitors-yaml",
    "/api/v1/auth/login",           # explicit events in auth.py
    "/api/v1/auth/register",
    "/api/v1/auth/logout",
    "/api/v1/auth/sso/",            # SSO flows emit explicit events
)


def should_audit(method: str, path: str) -> bool:
    """Whether a request is written to the audit log by the middleware.

    Args:
        method: HTTP method.
        path: Request path.

    Returns:
        True for mutating methods outside the skipped prefixes (core and plugin).
    """
    if method.upper() not in _MUTATING or any(path.startswith(p) for p in _SKIP_PREFIXES):
        return False
    try:
        from axara.community.plugins.loader import get_registry
        return not any(path.startswith(p) for p in get_registry().audit_skip_prefixes())
    except Exception:
        return True


def outcome_for(status: int) -> str:
    """Audit outcome for an HTTP status code.

    Args:
        status: Response status code.

    Returns:
        'success', 'denied' (401/403) or 'failure'.
    """
    if status < 400:
        return "success"
    if status in (401, 403):
        return "denied"
    return "failure"


def resource_from(template: str, path_params: dict):
    """'/api/v1/config/monitor-configs/{monitor_id}' -> ('config/monitor-configs', <id>)."""
    segs = [s for s in template.split("/") if s][2:]          # drop 'api', 'v1'
    fixed = [s for s in segs if not s.startswith("{")][:2]
    rid = next(iter(path_params.values()), None) if path_params else None
    return ("/".join(fixed) or None), (str(rid) if rid is not None else None)


def actor_for(request):
    """Who made a request, for the audit log.

    Args:
        request: Starlette request.

    Returns:
        (actor_type, actor, actor_id): a user from a valid JWT, 'agent' on agent paths, else 'anonymous'.
    """
    path = request.url.path
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            from axara.community.api.routes.auth import decode_token
            p = decode_token(auth[7:].strip())
            return "user", p.get("sub"), p.get("user_id")
        except Exception:
            pass
    if path.startswith("/api/v1/agents/"):              # agent API keys are not JWTs
        return "agent", None, None
    return "anonymous", None, None


class AuditMiddleware(BaseHTTPMiddleware):
    """Starlette middleware writing one audit event per mutating request."""
    async def dispatch(self, request, call_next):
        """Run the request and record it in the audit log when should_audit() says so.

        Args:
            request: Starlette request.
            call_next: Next handler in the chain.

        Returns:
            The response (an audit failure never changes it).
        """
        method, path = request.method, request.url.path
        if not should_audit(method, path):
            return await call_next(request)

        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            try:
                route = request.scope.get("route")
                template = getattr(route, "path", None) or path
                path_params = dict(request.scope.get("path_params") or {})
                rtype, rid = resource_from(template, path_params)
                actor_type, actor, actor_id = actor_for(request)
                await run_in_threadpool(
                    record,
                    getattr(route, "name", None) or f"{method} {template}",
                    actor=actor, actor_id=actor_id, actor_type=actor_type,
                    resource_type=rtype, resource_id=rid,
                    outcome=outcome_for(status),
                    source_ip=client_ip(request),
                    user_agent=request.headers.get("user-agent"),
                    details={"method": method, "path": template,
                             "path_params": path_params,
                             "query": dict(request.query_params),
                             "status_code": status},
                )
            except Exception as e:
                logger.error(f"audit middleware error on {method} {path}: {e}", exc_info=True)
