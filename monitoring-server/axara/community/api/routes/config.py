# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Config API Routes
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List, Any, Dict
from datetime import datetime, timezone
import hashlib
from axara.community.common.crypto import (encrypt_config_secrets, decrypt_config_secrets,
                           mask_config_secrets, is_mask)
import logging
import os
import re
import yaml
from pathlib import Path

from axara.community.database import SessionLocal
from axara.community.database.models import Application, MonitorConfig, Server, ApplicationServer, PlatformConfig
from axara.community.database.models import User
# Admin-only enforcement for configuration mutations (viewer/operator are read-only).
from axara.community.api.routes.auth import require_admin
from axara.community.api.agent_auth import require_own_server, verify_agent_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/config", tags=["config"])

# Separate router for agent-facing endpoints that must remain public.
# These are called by the monitoring agent using an API key, not a user JWT.
# Registered in main.py without the get_current_user dependency.
agent_router = APIRouter(prefix="/api/v1/config", tags=["config-agent"])

def get_db():
    """Dependency: a database session closed after the request.

    Yields:
        SQLAlchemy session.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# monitors.yaml file management
# Per-server monitors.yaml files are stored under AGENT_CONFIG_DIR.
# Docker volume must mount this directory so files survive container restarts.
# Default path: /app/data/agent-config/{server_id}/monitors.yaml (outside the code tree,
# so production images never need the source/config folder mounted).

_AGENT_CONFIG_DIR = Path(os.getenv("AGENT_CONFIG_DIR", "/app/data/agent-config"))
_SERVER_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

def _monitors_yaml_path(server_id: str) -> Path:
    """Return the path for a server's monitors.yaml, creating the directory if needed.
    server_id must be a UUID - it becomes a directory name (blocks path traversal)."""
    if not _SERVER_ID_RE.match(str(server_id)):
        raise ValueError(f"invalid server_id {server_id!r}")
    path = _AGENT_CONFIG_DIR / server_id / "monitors.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

def _sha256_file(path: Path) -> str:
    """SHA-256 checksum of a file in binary mode (cross-platform consistent)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"

def _regenerate_monitors_yaml(server_id: str, db: Session) -> str:
    """
    Write /app/config/{server_id}/monitors.yaml from the current DB state.
    Includes ALL monitor configs for the server (enabled and disabled) so
    the agent has full visibility — disabled monitors are simply skipped at
    collection time.  Returns the new SHA-256 checksum.

    Called:
      • immediately after create / update / delete in this file
      • on every agent heartbeat in agents.py (fallback sync)
    """
    srv = db.query(Server).filter(Server.id == server_id).first()
    if srv is not None and srv.status == "decommissioned":
        return None                     # removed agent: its folder was deleted, never re-create it
    monitors = (
        db.query(MonitorConfig)
        .filter(MonitorConfig.server_id == server_id, MonitorConfig.archived_at.is_(None))
        .order_by(MonitorConfig.type, MonitorConfig.name)
        .all()
    )

    # reveal=True: the agent needs plaintext credentials to connect.
    monitor_list = [_monitor_to_dict(m, reveal=True) for m in monitors]

    # Plugins may adjust what the agent receives (e.g. switch a monitor off while a
    # feature is disabled) without changing the stored rows.
    from axara.community.plugins.loader import get_registry
    get_registry().emit("monitors_yaml", server_id, monitor_list)

    yaml_path    = _monitors_yaml_path(server_id)

    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump({"monitors": monitor_list}, f,
                  default_flow_style=False, allow_unicode=True)

    checksum = _sha256_file(yaml_path)
    logger.info(
        f"monitors.yaml regenerated for server {server_id} "
        f"({len(monitor_list)} monitors, {checksum[:18]}…)"
    )
    return checksum

# monitors.yaml delivery endpoints

@agent_router.get("/monitors-yaml/{server_id}/checksum")
def get_monitors_yaml_checksum(server_id: str, db: Session = Depends(get_db),
                               agent: Server = Depends(verify_agent_key)):
    """
    Return the SHA-256 checksum of the current monitors.yaml for a server.
    The agent polls this endpoint every check cycle to detect changes.
    If the file does not yet exist (new server) it is generated on demand.
    Requires the agent's own API key (the file holds decrypted monitor credentials).
    """
    require_own_server(server_id, agent)
    try:
        server = agent

        yaml_path = _monitors_yaml_path(server_id)
        if not yaml_path.exists():
            _regenerate_monitors_yaml(server_id, db)

        return {
            "server_id": server_id,
            "checksum":  _sha256_file(yaml_path),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"monitors-yaml checksum error for {server_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@agent_router.get("/monitors-yaml/{server_id}")
def get_monitors_yaml(server_id: str, db: Session = Depends(get_db),
                      agent: Server = Depends(verify_agent_key)):
    """
    Return the full monitors.yaml content and checksum for a server.
    Called by the agent ConfigUpdater after detecting a checksum mismatch.
    Requires the agent's own API key (the file holds decrypted monitor credentials).
    """
    require_own_server(server_id, agent)
    try:
        server = agent

        yaml_path = _monitors_yaml_path(server_id)
        if not yaml_path.exists():
            _regenerate_monitors_yaml(server_id, db)

        content  = yaml_path.read_text(encoding="utf-8")
        checksum = f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"  # from memory

        return {
            "server_id": server_id,
            "checksum":  checksum,
            "content":   content,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"monitors-yaml download error for {server_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# Server Configuration
# The canonical store is the 'platform_config' DB table (key='server_config').
# _server_config is an in-memory cache — always authoritative within a process
# but populated from DB at startup so restarts don't lose saved changes.

_CONFIG_KEY = 'server_config'

# Settings that can be pinned from the environment (.env / compose). When the variable
# is set, it wins over the value stored in the DB and the UI shows it read-only; a blank
# stored value (e.g. saved by an older build) falls back to the built-in default.
_ENV_PINNED = {
    ('llm', 'base_url'):  ('OLLAMA_URL', 'http://ollama:11434'),
    ('rag', 'chroma_url'): ('CHROMA_URL', 'http://chromadb:8000'),
}


def _env_locked() -> list:
    """Settings that .env pins and the UI cannot change.

    Returns:
        Dotted setting names currently set by environment variables.
    """
    return [f'{sec}.{key}' for (sec, key), (var, _d) in _ENV_PINNED.items()
            if (os.getenv(var) or '').strip()]


def _apply_env_overrides(cfg: dict) -> dict:
    """Apply the .env-pinned values over a settings dict.

    Args:
        cfg: Server settings (modified in place).

    Returns:
        The same dict.
    """
    for (sec, key), (var, default) in _ENV_PINNED.items():
        section = cfg.setdefault(sec, {})
        env_val = (os.getenv(var) or '').strip()
        if env_val:
            section[key] = env_val
        elif not str(section.get(key) or '').strip():
            section[key] = default
    return cfg


def _strip_locked(incoming: dict) -> dict:
    """Drop UI metadata keys and any attempt to change an env-pinned setting."""
    clean = {k: v for k, v in (incoming or {}).items() if not str(k).startswith('_')}
    for name in _env_locked():
        sec, key = name.split('.', 1)
        if isinstance(clean.get(sec), dict):
            clean[sec] = {k: v for k, v in clean[sec].items() if k != key}
    return clean


def _plugin_config_defaults() -> dict:
    """Settings sections registered by plugins.

    Returns:
        {section: defaults}; empty when the registry cannot be built.
    """
    try:
        from axara.community.plugins.loader import get_registry
        return get_registry().config_defaults()
    except Exception:
        logger.warning("plugin settings defaults unavailable", exc_info=True)
        return {}


def _default_server_config() -> dict:
    """Return a fresh copy of the built-in defaults plus the plugins' sections."""
    return {**_plugin_config_defaults(), **_core_default_server_config()}


def _core_default_server_config() -> dict:
    """Return a fresh copy of the core's built-in defaults."""
    return {
        'incident': {
            'deduplication_window_minutes': 60,
            'auto_resolve_enabled': True,
            'consecutive_failures': 3,
        },
        'retention': {
            'metrics_days':   30,
            'incidents_days': 90,
            'logs_days':      14,
        },
        'llm': {
            'base_url': (os.getenv('OLLAMA_URL') or 'http://ollama:11434'),
            'model':    (os.getenv('LLM_MODEL') or 'gemma4:26b'),
            'timeout':  300,
        },
        'rag': {
            'top_k':              5,
            'similarity_threshold': 0.7,
            'chroma_url': (os.getenv('CHROMA_URL') or 'http://chromadb:8000'),
        },
    }

def _persist_config_to_db(db: Session, cfg: dict) -> None:
    """
    Upsert _server_config into the platform_config table using a single
    atomic PostgreSQL INSERT ... ON CONFLICT (key) DO UPDATE statement.

    This eliminates the race condition that caused UniqueViolation errors
    when all 8 uvicorn workers tried to INSERT simultaneously at startup,
    and avoids the ORM type-mismatch error (character varying = uuid) that
    occurred when the fallback UPDATE was generated against the VARCHAR(36)
    id column.
    """
    import copy
    import json
    import uuid as _uuid
    from sqlalchemy import text

    try:
        db.execute(
            text("""
                INSERT INTO platform_config (id, key, value, updated_at)
                VALUES (:id, :key, CAST(:value AS jsonb), :updated_at)
                ON CONFLICT (key)
                DO UPDATE SET
                    value      = CAST(EXCLUDED.value AS jsonb),
                    updated_at = EXCLUDED.updated_at
            """),
            {
                'id':         str(_uuid.uuid4()),
                'key':        _CONFIG_KEY,
                'value':      json.dumps(copy.deepcopy(cfg)),
                'updated_at': datetime.now(timezone.utc),
            },
        )
        db.commit()
        logger.debug('server_config persisted to platform_config table')
    except Exception as e:
        logger.error(f'Failed to persist server_config to DB: {e}', exc_info=True)
        db.rollback()

def load_server_config_from_db(db: Session, quiet: bool = False) -> None:
    """
    Called once at application startup (from main.py) to populate the
    in-memory _server_config from the platform_config DB table.

    Behaviour:
      • Row exists  → deep-merge DB values into _server_config so any keys
                       added by a future release are still present.
      • No row yet  → write the built-in defaults to the DB so the next
                       restart finds them.
    """
    global _server_config
    try:
        row = db.query(PlatformConfig).filter(
            PlatformConfig.key == _CONFIG_KEY
        ).first()

        if row and row.value:
            # Deep-merge: DB values override defaults; new default keys survive
            import copy
            merged = _default_server_config()
            for section, values in row.value.items():
                if section in merged and isinstance(values, dict):
                    merged[section].update(values)
                else:
                    merged[section] = values
            _server_config = _apply_env_overrides(merged)
            if not quiet:
                logger.info('server_config loaded from platform_config DB table')
        else:
            # First run — persist built-in defaults so future restarts reload them
            _server_config = _default_server_config()
            _apply_env_overrides(_server_config)
            _persist_config_to_db(db, _server_config)
            logger.info(
                'platform_config row not found — defaults written to DB'
            )
    except Exception as e:
        logger.error(
            f'Could not load server_config from DB: {e} — using in-memory defaults',
            exc_info=True,
        )

# In-memory cache — populated from DB at startup via load_server_config_from_db(), which also
# adds the settings sections plugins register (they are not known at import time).
_server_config = {
    "incident": {
        "deduplication_window_minutes": 60,
        "auto_resolve_enabled": True,
        "consecutive_failures": 3,
    },
    "retention": {
        "metrics_days":   30,
        "incidents_days": 90,
        "logs_days":      14,
    },
    "llm": {
        "base_url": (os.getenv("OLLAMA_URL") or "http://ollama:11434"),
        "model":    (os.getenv("LLM_MODEL") or "gemma4:26b"),
        "timeout":  300,
    },
    "rag": {
        "top_k":             5,
        "similarity_threshold": 0.7,
        "chroma_url":        (os.getenv("CHROMA_URL") or "http://chromadb:8000"),
    },
}

@router.get("/server")
def get_server_config():
    """Return current server configuration.

    Reloads from the platform_config DB table first so the value is correct
    regardless of which uvicorn worker handled the last PUT /server. Each of
    the N workers keeps its own in-memory _server_config, populated only at
    startup; a worker that did not serve the write (or started before it)
    otherwise returns stale values. The refresh is a single small indexed row read.
    """
    try:
        from axara.community.database import SessionLocal
        db = SessionLocal()
        try:
            load_server_config_from_db(db, quiet=True)
        finally:
            db.close()
    except Exception:
        logger.warning("get_server_config: DB refresh failed; serving in-memory cache",
                       exc_info=True)
    _apply_env_overrides(_server_config)
    # _env_locked: settings pinned by .env (shown read-only in the UI). Only on the
    # returned copy - never stored.
    return {**_server_config, '_env_locked': _env_locked()}

@router.put("/server")
def update_server_config(
    config: Dict[str, Any],
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Update server configuration in memory and persist to the platform_config table."""
    global _server_config

    import copy
    _old_config = copy.deepcopy(_server_config)

    # Deep merge incoming config into existing (env-pinned fields and UI metadata ignored)
    config = _strip_locked(config)
    for section, values in config.items():
        if section in _server_config and isinstance(values, dict):
            _server_config[section].update(values)
        else:
            _server_config[section] = values
    _apply_env_overrides(_server_config)
    # Persist to DB so the change survives container restarts
    _persist_config_to_db(db, _server_config)
    logger.info("Server configuration updated and persisted to DB")

    # Plugins react to changed settings (e.g. update the monitors that depend on them).
    from axara.community.plugins.loader import get_registry
    get_registry().emit("server_config_saved", db, _old_config, _server_config)

    return _server_config


# Applications

class ApplicationResponse(BaseModel):
    """An application as returned by GET /config/applications/{id}."""
    id:                   str
    name:                 str
    type:                 Optional[str]   = None
    description:          Optional[str]   = None
    environment:          Optional[str]   = None
    primary_support_team: Optional[str]   = None
    primary_assignee:     Optional[str]   = None
    tags:                 Optional[Any]   = None
    created_at:           Optional[datetime] = None

    class Config:
        """Pydantic settings: read attributes from ORM objects."""
        from_attributes = True


@router.get("/applications")
def list_applications(db: Session = Depends(get_db)):
    """Return all registered applications with aggregated stats."""
    try:
        from sqlalchemy import func
        from axara.community.database.models import Application, ApplicationServer, MonitorConfig, Incident

        apps = db.query(Application).order_by(Application.name).all()

        result = []
        for app in apps:
            # Count servers linked to this application
            server_count = (db.query(ApplicationServer)
                            .join(Server, Server.id == ApplicationServer.server_id)
                            .filter(ApplicationServer.application_id == app.id,
                                    (Server.status.is_(None)) | (Server.status != "decommissioned"))
                            .count())

            # Count active monitors
            monitor_count = db.query(MonitorConfig).filter(
                MonitorConfig.application_id == app.id,
                MonitorConfig.enabled == True,
            ).count()

            # Count open incidents
            open_incidents = db.query(Incident).filter(
                Incident.application_id == app.id,
                Incident.status.in_(["open", "in_progress"]),
            ).count()

            result.append({
                "id":                   app.id,
                "name":                 app.name,
                "type":                 app.type,
                "description":          app.description,
                "environment":          app.environment,
                "primary_support_team": app.primary_support_team,
                "primary_assignee":     app.primary_assignee,
                "itsm_provider":        app.itsm_provider,
                "tags":                 app.tags,
                "created_at":           app.created_at,
                # Aggregated stats for Applications.jsx
                "server_count":         server_count,
                "monitor_count":        monitor_count,
                "open_incidents":       open_incidents,
            })

        return result

    except Exception as e:
        logger.error(f"Failed to list applications: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/applications/{app_id}", response_model=ApplicationResponse)
def get_application(app_id: str, db: Session = Depends(get_db)):
    """Return a single application by ID."""
    try:
        app = db.query(Application).filter(Application.id == app_id).first()
        if not app:
            raise HTTPException(status_code=404, detail="Application not found")
        return app
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get application: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

class ApplicationConfigUpdate(BaseModel):
    """Payload for updating an application's ticketing tool."""
    itsm_provider:       Optional[str]  = None


@router.get("/ticket-providers")
def list_ticket_providers():
    """Ticketing tools available on this server (Jira, plus any plugins add).

    Returns:
        {"providers": [names]} — the values accepted as an application's itsm_provider.
    """
    from axara.community.plugins.loader import get_registry
    return {"providers": get_registry().ticket_provider_names()}


@router.patch("/applications/{app_id}/config")
def update_application_config(
    app_id: str,
    body: ApplicationConfigUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Update an application's ticketing tool (itsm_provider).

    Args:
        app_id: Application id.
        body: The new values (fields left out are unchanged).

    Returns:
        The application's id, name and itsm_provider.
    """
    try:
        app = db.query(Application).filter(Application.id == app_id).first()
        if not app:
            raise HTTPException(status_code=404, detail="Application not found")

        if body.itsm_provider is not None:
            from axara.community.plugins.loader import get_registry
            choice = body.itsm_provider.strip().lower()
            if choice not in get_registry().ticket_provider_names():
                raise HTTPException(status_code=400, detail=f"Unknown or unavailable ticketing tool '{body.itsm_provider}'")
            app.itsm_provider = choice

        app.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(app)

        logger.info(f"Updated config for application {app_id} (itsm_provider={app.itsm_provider})")
        return {
            "id":                  app.id,
            "name":                app.name,
            "itsm_provider":       app.itsm_provider,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update application config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# Monitor Config Management

VALID_MONITOR_TYPES = {
    'cpu', 'memory', 'disk', 'network', 'url', 'log',
    'database', 'process', 'port', 'browser', 'ssl',
}


class MonitorConfigCreate(BaseModel):
    """Fields required to create a new monitor config."""
    application_id:      str
    server_id:           str
    name:                str
    type:                str
    subtype:             Optional[str]  = None
    enabled:             bool           = True
    check_interval_seconds: int         = 300
    threshold_config:    Optional[Dict] = None
    consecutive_failures: int           = 3
    severity:            str            = "medium"
    config:              Optional[Dict] = None   # type-specific settings (url, path, etc.)
    assignee_override:   Optional[str]  = None


class MonitorConfigUpdate(BaseModel):
    """All fields optional — only provided fields are updated."""
    name:                Optional[str]  = None
    enabled:             Optional[bool] = None
    check_interval_seconds: Optional[int] = None
    threshold_config:    Optional[Dict] = None
    consecutive_failures: Optional[int] = None
    severity:            Optional[str]  = None
    config:              Optional[Dict] = None
    assignee_override:   Optional[str]  = None


def _monitor_to_dict(m: MonitorConfig, reveal: bool = False) -> Dict:
    """Serialise a MonitorConfig ORM object to a plain dict.

    Secrets in `config` are encrypted at rest. reveal=True decrypts them (for
    the agent's monitors.yaml, which needs plaintext to connect); reveal=False
    (default, used for every API/UI response) masks them so plaintext never
    leaves the server to a browser."""
    _cfg = decrypt_config_secrets(m.config) if reveal else mask_config_secrets(m.config)
    return {
        "monitor_config_id":    m.id,
        "application_id":       m.application_id,
        "server_id":            m.server_id,
        "name":                 m.name,
        "type":                 m.type,
        "subtype":              m.subtype,
        "enabled":              m.enabled,
        "check_interval_seconds": m.check_interval_seconds,
        "threshold_config":     m.threshold_config,
        "consecutive_failures": m.consecutive_failures,
        "severity":             m.severity,
        "config":               _cfg,
        "assignee_override":    m.assignee_override,
        "created_at":           m.created_at.isoformat() if m.created_at else None,
        "updated_at":           m.updated_at.isoformat() if m.updated_at else None,
    }


# ── Maintenance Windows ───────────────────────────────────────────────────────

class MaintenanceWindowCreate(BaseModel):
    """Create a maintenance window. server_id=None means ALL servers of the app."""
    application_id: str
    server_id:      Optional[str] = None       # None => all servers of the application
    start_time:     str                         # ISO 8601
    end_time:       str                         # ISO 8601
    description:    Optional[str] = None
    suppress_type:  str = "both"                # 'incidents' | 'alerts' | 'both'
    name:           Optional[str] = None


@router.get("/applications/{app_id}/maintenance")
def list_maintenance_windows(app_id: str, db: Session = Depends(get_db)):
    """
    List maintenance windows for an application, split into:
      active = end_time >= now (in effect or scheduled), past = ended.
    Read access for any authenticated user (router is behind get_current_user).
    """
    try:
        from axara.community.processing.maintenance_window import MaintenanceWindowManager
        mgr = MaintenanceWindowManager(db)
        return mgr.list_for_application(app_id)
    except Exception as e:
        logger.error(f"Failed to list maintenance windows for {app_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/applications/{app_id}/maintenance", status_code=201)
def create_maintenance_window(
    app_id: str,
    body: MaintenanceWindowCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Create a maintenance window (admin only)."""
    try:
        from axara.community.processing.maintenance_window import MaintenanceWindowManager
        mgr = MaintenanceWindowManager(db)
        data = body.dict()
        # path app_id is authoritative
        data["application_id"] = app_id
        data["created_by"] = _admin.username
        window = mgr.create_window(data)
        logger.info(
            f"Created maintenance window {window.id} for app {app_id} "
            f"(server={'ALL' if window.server_id is None else window.server_id}, "
            f"type={window.suppress_type})"
        )
        return {"id": window.id, "status": "created"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create maintenance window: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/maintenance/{window_id}", status_code=204)
def delete_maintenance_window(
    window_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Delete a maintenance window (admin only)."""
    try:
        from axara.community.processing.maintenance_window import MaintenanceWindowManager
        mgr = MaintenanceWindowManager(db)
        if not mgr.delete_window(window_id):
            raise HTTPException(status_code=404, detail="Maintenance window not found")
        logger.info(f"Deleted maintenance window {window_id}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete maintenance window {window_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/monitor-configs", status_code=201)
def create_monitor_config(
    body: MonitorConfigCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """
    Create a new monitor config and return its ID.
    Use the returned monitor_config_id in monitors.yaml to activate monitoring.
    Unlike registration, this allows multiple monitors of the same type per server
    (e.g. two URL monitors pointing at different endpoints).
    """
    try:
        from axara.community.plugins.loader import get_registry
        if body.type not in VALID_MONITOR_TYPES and not get_registry().monitor_type_creatable(body.type):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid type '{body.type}'. "
                       f"Valid types: {sorted(VALID_MONITOR_TYPES)}"
            )

        # Verify server and application exist
        server = db.query(Server).filter(Server.id == body.server_id).first()
        if not server:
            raise HTTPException(status_code=404, detail="Server not found")
        if server.status == "decommissioned":
            raise HTTPException(status_code=409, detail="This agent has been removed")

        app = db.query(Application).filter(Application.id == body.application_id).first()
        if not app:
            raise HTTPException(status_code=404, detail="Application not found")

        import uuid

        _check_interval = body.check_interval_seconds
        _threshold_config = body.threshold_config or {}
        _config = body.config or {}
        _severity = body.severity
        # A plugin may set the values for a monitor type it added (e.g. a fixed cadence).
        for d in get_registry().emit("monitor_defaults", body.type, body.config):
            if isinstance(d, dict):
                _check_interval = d.get('check_interval_seconds', _check_interval)
                _threshold_config = d.get('threshold_config', _threshold_config)
                _config = d.get('config', _config)
                _severity = d.get('severity', _severity)
                break

        monitor = MonitorConfig(
            id                    = str(uuid.uuid4()),
            application_id        = body.application_id,
            server_id             = body.server_id,
            name                  = body.name,
            type                  = body.type,
            subtype               = body.subtype,
            enabled               = body.enabled,
            check_interval_seconds = _check_interval,
            threshold_config      = _threshold_config,
            consecutive_failures  = body.consecutive_failures,
            severity              = _severity,
            config                = encrypt_config_secrets(_config),
            assignee_override     = body.assignee_override,
            created_at            = datetime.now(timezone.utc),
            updated_at            = datetime.now(timezone.utc),
        )
        db.add(monitor)
        db.commit()
        db.refresh(monitor)

        logger.info(
            f"Created monitor config '{monitor.name}' ({monitor.type}) "
            f"id={monitor.id} for server {server.hostname}"
        )

        # Regenerate monitors.yaml immediately so the agent picks up the new monitor
        try:
            _regenerate_monitors_yaml(body.server_id, db)
        except Exception as e:
            # Non-fatal: agent will get the update on next heartbeat fallback
            logger.warning(f"monitors.yaml regeneration failed after create: {e}")

        return _monitor_to_dict(monitor)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create monitor config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/monitor-configs")
def list_monitor_configs(
    server_id:      Optional[str] = None,
    application_id: Optional[str] = None,
    type:           Optional[str] = None,
    enabled:        Optional[bool] = None,
    include_archived: bool = False,
    db: Session = Depends(get_db),
):
    """
    List monitor configs with optional filters.
    Use server_id to get all monitors for a specific agent.
    Monitors of removed agents (archived) are left out unless include_archived=true.
    """
    try:
        q = db.query(MonitorConfig)
        if not include_archived:
            q = q.filter(MonitorConfig.archived_at.is_(None))
        if server_id:
            q = q.filter(MonitorConfig.server_id == server_id)
        if application_id:
            q = q.filter(MonitorConfig.application_id == application_id)
        if type:
            q = q.filter(MonitorConfig.type == type)
        if enabled is not None:
            q = q.filter(MonitorConfig.enabled == enabled)

        monitors = q.order_by(MonitorConfig.type, MonitorConfig.name).all()
        return [_monitor_to_dict(m) for m in monitors]

    except Exception as e:
        logger.error(f"Failed to list monitor configs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/monitor-configs/{monitor_id}")
def get_monitor_config(monitor_id: str, db: Session = Depends(get_db)):
    """Return a single monitor config by ID."""
    try:
        monitor = db.query(MonitorConfig).filter(MonitorConfig.id == monitor_id).first()
        if not monitor:
            raise HTTPException(status_code=404, detail="Monitor config not found")
        return _monitor_to_dict(monitor)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get monitor config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/monitor-configs/{monitor_id}")
def update_monitor_config(
    monitor_id: str,
    body: MonitorConfigUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """
    Update a monitor config. Only provided fields are changed.
    To update the target URL of a URL monitor supply: config={"url": "https://..."}
    Changes take effect on the agent within one hot-reload cycle (≤30s).
    """
    try:
        monitor = db.query(MonitorConfig).filter(MonitorConfig.id == monitor_id).first()
        if not monitor:
            raise HTTPException(status_code=404, detail="Monitor config not found")
        if monitor.archived_at:
            raise HTTPException(status_code=409, detail="This monitor belongs to a removed agent and is archived")

        updates = {k: v for k, v in body.model_dump().items() if v is not None}
        # Encrypt secrets in config at rest. A masked value returned from the UI
        # means "unchanged" — keep the stored (encrypted) secret instead of
        # overwriting it with the mask.
        if isinstance(updates.get("config"), dict):
            _existing = monitor.config or {}
            _merged = dict(updates["config"])
            for _ck, _cv in list(_merged.items()):
                if is_mask(_cv):
                    _merged[_ck] = _existing.get(_ck)
            updates["config"] = encrypt_config_secrets(_merged)
        updates["updated_at"] = datetime.now(timezone.utc)

        for k, v in updates.items():
            setattr(monitor, k, v)

        db.commit()
        db.refresh(monitor)

        logger.info(f"Updated monitor config {monitor_id}: {list(updates.keys())}")

        # Regenerate monitors.yaml so the agent hot-reloads within one poll cycle
        try:
            _regenerate_monitors_yaml(str(monitor.server_id), db)
        except Exception as e:
            logger.warning(f"monitors.yaml regeneration failed after update: {e}")

        return _monitor_to_dict(monitor)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update monitor config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/monitor-configs/{monitor_id}", status_code=204)
def delete_monitor_config(
    monitor_id: str,
    hard: bool = False,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """
    Delete or disable a monitor config.
    By default (hard=false) the config is disabled rather than deleted,
    preserving metric history and incident references.
    Pass ?hard=true to permanently remove the row.
    """
    try:
        monitor = db.query(MonitorConfig).filter(MonitorConfig.id == monitor_id).first()
        if not monitor:
            raise HTTPException(status_code=404, detail="Monitor config not found")
        if monitor.archived_at:
            raise HTTPException(status_code=409, detail="This monitor belongs to a removed agent and is archived")
        
        server_id = str(monitor.server_id)  # capture before possible deletion

        if hard:
            db.delete(monitor)
            db.commit()
            logger.info(f"Hard-deleted monitor config {monitor_id}")
        else:
            monitor.enabled    = False
            monitor.updated_at = datetime.now(timezone.utc)
            db.commit()
            logger.info(f"Disabled monitor config {monitor_id}")

        # Regenerate monitors.yaml immediately
        try:
            _regenerate_monitors_yaml(server_id, db)
        except Exception as e:
            logger.warning(f"monitors.yaml regeneration failed after delete: {e}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete monitor config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
