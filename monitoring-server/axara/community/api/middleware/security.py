# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Security Hardening Middleware & Utilities

Middleware:
  SecurityHeadersMiddleware — OWASP security headers on every response.

Utilities:
  InputSanitizer  — validate/sanitise untrusted input strings.
  AuditLogger     — structured security event logging.

Wired in main.py as:
    from axara.community.api.middleware.security import SecurityHeadersMiddleware
    app.add_middleware(SecurityHeadersMiddleware)

CSP note:
  The React dashboard is served by Nginx on :443 and makes API calls
  to the FastAPI server on :9090.  Because these are different ports,
  the browser treats them as different origins.  The CSP connect-src
  directive must therefore allow the API origin explicitly.  We read
  ALLOWED_API_ORIGINS from the environment (default: 'self') so the
  deployment can set it to 'https://hostname:9090' without code changes.
"""
import logging
import os
import re

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


# ── Middleware ────────────────────────────────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Add OWASP-recommended security headers to every HTTP response.

    - X-Content-Type-Options: nosniff
    - X-Frame-Options: DENY
    - Strict-Transport-Security (HSTS): 1 year, includeSubDomains
    - Content-Security-Policy: configurable via ALLOWED_API_ORIGINS env var
    - Referrer-Policy: strict-origin-when-cross-origin
    - Permissions-Policy: disable geolocation, microphone, camera

    Removed:
    - X-XSS-Protection — deprecated, modern browsers ignore it, MDN
      recommends removal. The XSS Auditor was removed from Chrome 78+,
      Edge, and Firefox never implemented it.
    """

    def __init__(self, app):
        """Build the Content-Security-Policy once at start-up (connect-src from ALLOWED_API_ORIGINS).

        Args:
            app: ASGI application.
        """
        super().__init__(app)

        # Build CSP once at startup.
        # ALLOWED_API_ORIGINS controls connect-src so the React dashboard
        # (served on :443) can call the API (on :9090).
        # Default 'self' works when both are behind the same Nginx proxy.
        api_origins = os.getenv('ALLOWED_API_ORIGINS', "'self'")

        self.csp = (
            "default-src 'self'; "
            f"script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            f"style-src 'self' 'unsafe-inline'; "
            f"img-src 'self' data: https:; "
            f"font-src 'self' data:; "
            f"connect-src 'self' {api_origins}; "
            f"frame-ancestors 'none'"
        )

    async def dispatch(self, request: Request, call_next):
        """Add the security headers (nosniff, frame deny, HSTS, CSP, referrer and permissions policy).

        Args:
            request: Incoming request.
            call_next: Next handler.

        Returns:
            The response with the headers set.
        """
        response = await call_next(request)

        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Strict-Transport-Security'] = (
            'max-age=31536000; includeSubDomains'
        )
        response.headers['Content-Security-Policy'] = self.csp
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = (
            'geolocation=(), microphone=(), camera=()'
        )

        return response


# ── Input Sanitization ────────────────────────────────────────────────────────

class InputSanitizer:
    """
    Validate and sanitise untrusted input strings.

    Used by API route handlers before passing user-supplied values to
    the database layer.  Not a replacement for parameterised queries
    (SQLAlchemy handles that), but an additional defence-in-depth layer.
    """

    @staticmethod
    def sanitize_string(value: str, max_length: int = 1000) -> str:
        """
        Basic string sanitisation: truncate, strip null bytes, trim whitespace.
        """
        if not isinstance(value, str):
            raise ValueError("Input must be a string")
        value = value[:max_length]
        value = value.replace('\x00', '')
        return value.strip()

    @staticmethod
    def validate_email(email: str) -> bool:
        """Validate email format (RFC 5322 simplified)."""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email))

    @staticmethod
    def validate_uuid(uuid_str: str) -> bool:
        """Validate UUID v4 format."""
        pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        return bool(re.match(pattern, uuid_str.lower()))

    @staticmethod
    def detect_xss(value: str) -> bool:
        """
        Detect potential XSS payloads in user input.

        Returns True if a suspicious pattern is found.
        """
        patterns = [
            r'<script[^>]*>',
            r'javascript\s*:',
            r'on\w+\s*=',           # onclick=, onerror=, etc.
            r'<iframe',
            r'<embed',
            r'<object',
            r'<svg[^>]*onload',
        ]
        for pattern in patterns:
            if re.search(pattern, value, re.IGNORECASE):
                return True
        return False


# ── Audit Logging ─────────────────────────────────────────────────────────────

class AuditLogger:
    """
    Structured audit logging for security events.

    All methods are static — call via the module-level `audit_logger`
    instance or directly on the class.
    """

    @staticmethod
    def log_authentication(
        user: str,
        success: bool,
        ip_address: str,
        user_agent: str = None,
    ):
        """Log authentication attempt (login / API key validation)."""
        level = logging.INFO if success else logging.WARNING
        logger.log(
            level,
            f"AUTH: user={user} success={success} ip={ip_address} "
            f"ua={user_agent}",
        )

    @staticmethod
    def log_authorization_failure(
        user: str,
        resource: str,
        action: str,
        ip_address: str,
    ):
        """Log failed authorisation check (role/permission denied)."""
        logger.warning(
            f"AUTHZ_FAIL: user={user} resource={resource} "
            f"action={action} ip={ip_address}"
        )

    @staticmethod
    def log_suspicious_activity(
        ip_address: str,
        endpoint: str,
        reason: str,
        request_data: dict = None,
    ):
        """Log suspicious request (XSS attempt, unusual pattern)."""
        logger.warning(
            f"SUSPICIOUS: ip={ip_address} endpoint={endpoint} "
            f"reason={reason} data={request_data}"
        )

    @staticmethod
    def log_data_access(
        user: str,
        resource: str,
        action: str,
        ip_address: str,
    ):
        """Log sensitive data access (credential retrieval, config change)."""
        logger.info(
            f"DATA_ACCESS: user={user} resource={resource} "
            f"action={action} ip={ip_address}"
        )


# ── Module-level instances ────────────────────────────────────────────────────

audit_logger = AuditLogger()
input_sanitizer = InputSanitizer()
