# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Rate Limiting Middleware
Protects API endpoints from abuse and excessive requests.

Design decisions:
  - Agent API keys (prefixed 'agent_') are EXEMPT from rate limiting.
    Agents are trusted internal clients that may burst during offline
    replay — rate-limiting them risks dropping metrics.
  - Dashboard and public endpoints are rate-limited per client IP or
    API key hash.
  - Uses asyncio.Lock to prevent concurrent mutation of the request
    history dict within a single uvicorn worker.
  - Returns JSONResponse (not HTTPException) for 429 — Starlette's
    BaseHTTPMiddleware doesn't propagate HTTPException correctly and
    would return a 500 instead.

Wired in main.py as:
    from axara.community.api.middleware.rate_limit import RateLimitMiddleware
    app.add_middleware(RateLimitMiddleware, requests_per_minute=300)
"""
import asyncio
import hashlib
import logging
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Sliding-window rate limiter.

    Tracks request timestamps per client+endpoint and rejects requests
    that exceed the configured limit within the time window.
    """

    def __init__(self, default_rpm: int = 300):
        """Set up the per-client request history and the per-endpoint limits.

        Args:
            default_rpm: Requests per minute allowed on endpoints without their own limit.
        """
        # {client_id: {endpoint_pattern: [datetime, ...]}}
        self.requests: dict = defaultdict(lambda: defaultdict(list))

        # Async lock — prevents concurrent coroutines from corrupting
        # the request history list during slice assignment.
        self._lock = asyncio.Lock()

        # Rate limit configurations: {pattern: {requests, window}}
        # Patterns ending with * are prefix-matched; others exact-match.
        self.limits = {
            # Agents POST metrics — high limit for replay bursts.
            # (Agents with 'agent_' keys are fully exempt, but non-agent
            # callers of the metrics endpoint still get a generous limit.)
            'POST:/api/v1/metrics':      {'requests': 2000, 'window': 60},

            # Dashboard polling endpoints — moderate limits
            '/api/v1/dashboard/*':       {'requests': 60,   'window': 60},
            '/api/v1/metrics/*':         {'requests': 120,  'window': 60},
            '/api/v1/incidents/*':       {'requests': 120,  'window': 60},

            # Auth endpoints — tight to prevent brute force
            '/api/v1/auth/login':        {'requests': 10,   'window': 60},
            '/api/v1/auth/register':     {'requests': 5,    'window': 60},

            # Config and admin — moderate
            '/api/v1/config/*':          {'requests': 60,   'window': 60},
            '/api/v1/agents/*':          {'requests': 120,  'window': 60},

            # Default for anything not matched above
            'default':                   {'requests': default_rpm, 'window': 60},
        }

        self.last_cleanup = datetime.utcnow()

    def get_client_id(self, request: Request) -> str:
        """
        Derive a unique client identifier.

        Uses the API key hash if an Authorization header is present,
        otherwise falls back to the client IP (respecting X-Forwarded-For
        from the Nginx reverse proxy).
        """
        auth_header = request.headers.get('Authorization', '')
        if auth_header:
            return hashlib.sha256(auth_header.encode()).hexdigest()[:16]

        forwarded = request.headers.get('X-Forwarded-For')
        if forwarded:
            return forwarded.split(',')[0].strip()

        return request.client.host if request.client else 'unknown'

    def is_agent_request(self, request: Request) -> bool:
        """
        Check whether this request comes from a trusted monitoring agent.

        Agent API keys are prefixed with 'agent_' and set during
        registration. These clients are exempt from rate limiting
        because they may burst during offline metric replay.
        """
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            token = auth[7:]
            return token.startswith('agent_')
        return False

    def get_limit_config(self, method: str, path: str) -> dict:
        """
        Find the rate limit config for a request.

        Matching priority:
          1. METHOD:path exact match  (e.g. POST:/api/v1/metrics)
          2. Path exact match         (e.g. /api/v1/auth/login)
          3. Path prefix/wildcard     (e.g. /api/v1/dashboard/*)
          4. Default
        """
        # 1. Method-qualified exact match
        method_key = f"{method}:{path}"
        if method_key in self.limits:
            return self.limits[method_key]

        # 2. Path exact match
        if path in self.limits:
            return self.limits[path]

        # 3. Wildcard prefix match (longest prefix wins)
        best_match = None
        best_len = 0
        for pattern, config in self.limits.items():
            if pattern.endswith('*'):
                prefix = pattern[:-1]
                if path.startswith(prefix) and len(prefix) > best_len:
                    best_match = config
                    best_len = len(prefix)

        if best_match:
            return best_match

        # 4. Default
        return self.limits['default']

    async def is_allowed(self, client_id: str, method: str, path: str) -> tuple:
        """
        Check whether a request is within the rate limit.

        Returns:
            (allowed: bool, retry_after_seconds: int, limit_config: dict)
        """
        config = self.get_limit_config(method, path)
        max_requests = config['requests']
        window = config['window']
        now = datetime.utcnow()
        cutoff = now - timedelta(seconds=window)

        async with self._lock:
            # Periodic cleanup (every 5 minutes)
            if (now - self.last_cleanup).total_seconds() > 300:
                self._cleanup_old_entries(now)
                self.last_cleanup = now

            history = self.requests[client_id][path]

            # Prune expired entries
            history[:] = [ts for ts in history if ts > cutoff]

            if len(history) >= max_requests:
                # Calculate retry-after from oldest entry in window
                if history:
                    retry_after = int(
                        (history[0] + timedelta(seconds=window) - now)
                        .total_seconds()
                    )
                    retry_after = max(retry_after, 1)
                else:
                    retry_after = window
                return False, retry_after, config

            # Record this request
            history.append(now)
            remaining = max_requests - len(history)

            return True, 0, config

    def _cleanup_old_entries(self, now: datetime):
        """Remove stale entries older than 1 hour to free memory."""
        cutoff = now - timedelta(hours=1)

        for client_id in list(self.requests.keys()):
            for endpoint in list(self.requests[client_id].keys()):
                history = self.requests[client_id][endpoint]
                history[:] = [ts for ts in history if ts > cutoff]
                if not history:
                    del self.requests[client_id][endpoint]
            if not self.requests[client_id]:
                del self.requests[client_id]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware for request rate limiting.

    Constructor accepts `requests_per_minute` to set the default limit,
    matching the wiring in main.py:

        app.add_middleware(RateLimitMiddleware, requests_per_minute=300)
    """

    def __init__(self, app, requests_per_minute: int = 300):
        """Wrap the app with a rate limiter.

        Args:
            app: ASGI application.
            requests_per_minute: Default limit per client.
        """
        super().__init__(app)
        self.limiter = RateLimiter(default_rpm=requests_per_minute)

    async def dispatch(self, request: Request, call_next):
        """Apply the rate limit to one request (health checks and agent keys are exempt).

        Args:
            request: Incoming request.
            call_next: Next handler.

        Returns:
            The response, or 429 with Retry-After when the client is over its limit.
        """
        path = request.url.path

        # Skip rate limiting for health/ready endpoints
        if path in ('/health', '/ready'):
            return await call_next(request)

        # Exempt agent API keys — trusted internal clients
        if self.limiter.is_agent_request(request):
            return await call_next(request)

        client_id = self.limiter.get_client_id(request)
        method = request.method

        allowed, retry_after, config = await self.limiter.is_allowed(
            client_id, method, path
        )

        if not allowed:
            logger.warning(
                f"Rate limit exceeded: client={client_id} "
                f"endpoint={method} {path} "
                f"retry_after={retry_after}s"
            )
            # Return JSONResponse directly — NOT HTTPException.
            # Starlette's BaseHTTPMiddleware doesn't propagate HTTPException
            # correctly; it would surface as a 500 instead of 429.
            return JSONResponse(
                status_code=429,
                content={
                    'error': 'Rate limit exceeded',
                    'retry_after': retry_after,
                },
                headers={'Retry-After': str(retry_after)},
            )

        response = await call_next(request)

        # Add rate-limit headers so clients can self-throttle
        response.headers['X-RateLimit-Limit'] = str(config['requests'])
        response.headers['X-RateLimit-Window'] = f"{config['window']}s"

        return response
