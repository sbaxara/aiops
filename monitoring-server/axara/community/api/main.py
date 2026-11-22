# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Axara AIOps server: the FastAPI application.

Configures logging, middleware (CORS, security headers, rate limit, audit), mounts the core
routers, then the routers and start-up/shutdown hooks registered by plugins
(plugins.loader). Licence checks, when a plugin installs them, apply through the
registry's request guards.
"""
from fastapi import FastAPI, Depends, HTTPException
from axara.community import __version__
from fastapi.middleware.cors import CORSMiddleware
from axara.community.api.routes import auth, agents, metrics, incidents, config, dashboard, scripts, ssl_certificates
from axara.community.plugins.loader import get_registry
import logging
import os
from logging.handlers import RotatingFileHandler
from axara.community.api.middleware.rate_limit import RateLimitMiddleware
from axara.community.api.middleware.security import SecurityHeadersMiddleware
from axara.community.api.agent_auth import verify_agent_key
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
 
def _configure_logging():
    """Send log records to the console and to a rotating file under LOG_DIR.

    Raises:
        OSError: when LOG_DIR cannot be created.
    """
    log_dir = os.getenv("LOG_DIR", "/app/logs")
    os.makedirs(log_dir, exist_ok=True)
 
    fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
 
    # Stream handler — keeps docker logs working
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
 
    # Rotating file handler — writes to the mounted volume
    # Max 10 MB per file, keep 5 rotated files (50 MB total)
    file_handler = RotatingFileHandler(
        filename=os.path.join(log_dir, "server.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
 
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)
 
_configure_logging()


# Interactive API docs (/docs, /openapi.json) map every endpoint. Off unless
# API_DOCS_ENABLED=1 in .env (e.g. for an integration project).
_API_DOCS = (os.getenv("API_DOCS_ENABLED", "") or "").strip().lower() in ("1", "true", "yes")

app = FastAPI(
    title="Axara AIOps",
    description="AI-powered monitoring with automated incident management",
    version=__version__,
    # Keep the Bearer token in Swagger UI across page refreshes
    swagger_ui_parameters={"persistAuthorization": True},
    # disable default /docs
    docs_url=None,
    # disable default /redoc
    redoc_url=None,
    openapi_url="/openapi.json" if _API_DOCS else None,
)

# Serve swagger-ui assets from local static directory
app.mount("/static", StaticFiles(directory="/app/static"), name="static")

@app.on_event("startup")
async def _reapply_uvicorn_logging():
    # Re-applying here runs after uvicorn has finished its own logging setup.
    """Route uvicorn's loggers through the root handlers configured above."""
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.setLevel(logging.INFO)
        uv_logger.handlers.clear()
        uv_logger.propagate = True

@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui():
    """Serve the Swagger UI from local assets when API_DOCS_ENABLED is set.

    Returns:
        The Swagger UI page.

    Raises:
        HTTPException: 404 when the API docs are disabled.
    """
    if not _API_DOCS:
        raise HTTPException(status_code=404, detail="Not Found")
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " - Docs",
        swagger_js_url="/static/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger-ui.css",
        swagger_ui_parameters={"persistAuthorization": True},
    )

# CORS middleware
# Origins from ALLOWED_ORIGINS (comma-separated). An explicit allowlist is
# required because allow_credentials=True (browsers reject "*" with credentials,
# and a wildcard credentialed API is unsafe). Defaults to local dev origins; set
# ALLOWED_ORIGINS in the environment for production (e.g. https://monitoring.example).
_allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()] or [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:3000", "http://127.0.0.1:3000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security headers (OWASP best practices)
app.add_middleware(SecurityHeadersMiddleware)

# Rate limiting (agents exempt via Bearer agent_* key detection)
app.add_middleware(
    RateLimitMiddleware,
    requests_per_minute=int(os.getenv("RATE_LIMIT_RPM", "300")),
)

# Tamper-evident audit trail for every mutating user/integration request.
# Added last => outermost, so it also records requests rejected by inner layers.
from axara.community.api.middleware.audit import AuditMiddleware
app.add_middleware(AuditMiddleware)

from axara.community.api.guards import require_agent_intake, require_user

_registry = get_registry()
# Agent routers and UI routers run the registry's checks (Enterprise: licence intake and
# "licence not blocked"); in Community they only require the agent key / a signed-in user.
_agent_guard = [Depends(require_agent_intake)]
_auth_dep = [Depends(require_user)]

# Public routers (own authentication)
# auth:     login / register / logout must be public
# agents:   registration needs an enrolment token; heartbeat/check-scripts/deregister use
#           verify_agent_key; the list/detail/removal/enrolment routes need a signed-in user
# metrics:  metric ingestion uses verify_agent_key; metric reads need a signed-in user
# scripts:  script downloads are called by agents using verify_agent_key
app.include_router(auth.router)
app.include_router(agents.router)            # per-route auth: register=enrolment token, agent calls=agent key, UI=user
app.include_router(metrics.router)
app.include_router(scripts.router, dependencies=_agent_guard + [Depends(verify_agent_key)])  # agent API key
app.include_router(config.agent_router, dependencies=_agent_guard)  # monitors-yaml — agent API key auth

# Protected core routers
app.include_router(incidents.router,       dependencies=_auth_dep)
app.include_router(config.router,          dependencies=_auth_dep)
app.include_router(dashboard.router,       dependencies=_auth_dep)
app.include_router(ssl_certificates.router,dependencies=_auth_dep)
from axara.community.api.routes import audit as audit_routes
app.include_router(audit_routes.router,    dependencies=_auth_dep)   # admin-only inside

# Routers registered by plugins
for _entry in _registry.routers():
    _deps = [Depends(_registry.feature_guard(_entry.feature))] if _entry.feature else []
    _kind = _entry.options["kind"]
    if _kind == "user":
        _deps = _auth_dep + _deps
    elif _kind == "agent":
        _deps = _agent_guard + _deps
    app.include_router(_entry.item, dependencies=_deps)


@app.on_event("startup")
async def _load_platform_config():
    """Load the saved server settings into the in-memory cache before any plugin starts."""
    try:
        from axara.community.database import SessionLocal
        from axara.community.api.routes.config import load_server_config_from_db
        db = SessionLocal()
        try:
            load_server_config_from_db(db)
        finally:
            db.close()
    except Exception as e:
        logging.getLogger(__name__).warning(
            f'Platform config load failed: {e} — using defaults'
        )


@app.on_event("startup")
async def _plugin_startup():
    """Run the start-up hooks registered by the core and plugins (schedulers, ITSM sync)."""
    _registry.run_startup(app)


@app.on_event("shutdown")
async def _plugin_shutdown():
    """Run the shutdown hooks registered by the core and plugins."""
    _registry.run_shutdown(app)


@app.get("/")
def root():
    """Service banner.

    Returns:
        The service name.
    """
    return {"message": "Axara AIOps API"}

@app.get("/health")
def health_check():
    """Liveness check used by the container healthcheck.

    Returns:
        {"status": "healthy"}
    """
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
