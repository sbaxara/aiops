# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Agent Registration API
Handles agent registration and management
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import logging
from axara.community.database.models import Server, Application, ApplicationServer, MonitorConfig
from axara.community.database.repositories.application_repo import ApplicationRepository
from axara.community.database.repositories.server_repo import ServerRepository
from axara.community.database.repositories.monitor_repo import MonitorRepository
from sqlalchemy.orm import Session
from axara.community.api.routes.config import _regenerate_monitors_yaml
from axara.community.api.agent_auth import get_db, require_own_server, verify_agent_key  # noqa: F401 (re-exported)
from axara.community.api import agent_lifecycle as lifecycle
from axara.community.api.routes.auth import require_admin
from axara.community.api.guards import require_user
from axara.community.database.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

# Request/Response Models
class SupportTeamConfig(BaseModel):
    """Support team contact sent by the agent at registration."""
    name: str
    email: str
    slack_channel: Optional[str] = None
    teams_webhook: Optional[str] = None

class AgentRegistrationRequest(BaseModel):
    """Agent registration payload (earlier definition, superseded below)."""
    hostname: str
    ip_address: str
    os_type: str
    os_version: str
    agent_version: str
    capabilities: List[str]
    application_name: str
    application_id: Optional[str] = None
    support_team: Optional[SupportTeamConfig] = None
    environment: str = "production"
    tags: List[str] = []

class MonitorConfigResponse(BaseModel):
    """One monitor configuration returned to a newly registered agent."""
    monitor_config_id: str
    name: str
    type: str
    subtype: Optional[str] = None
    enabled: bool
    check_interval_seconds: int
    thresholds: Dict[str, Any]
    consecutive_failures: int
    severity: str
    assignee: str
    notification_channels: List[Dict[str, Any]]
    config: Dict[str, Any]

class AgentRegistrationResponse(BaseModel):
    """Registration result: server id, API key and initial monitors."""
    success: bool
    server_id: str
    application_id: str
    check_interval_seconds: int
    api_key: str
    monitor_configs: List[MonitorConfigResponse]
    monitor_scripts: List[Dict[str, str]]

class ScriptInfo(BaseModel):
    """A monitor script the agent has locally (name, version, checksum)."""
    script_name: str
    version: str
    checksum: str

class CheckScriptsRequest(BaseModel):
    """Agent's list of local monitor scripts, compared with the server's."""
    server_id: str
    local_scripts: List[ScriptInfo]

class HeartbeatRequest(BaseModel):
    """Agent heartbeat payload."""
    server_id: str

class ApplicationInfo(BaseModel):
    """Application summary embedded in server responses."""
    id:          str
    name:        str
    environment: Optional[str] = None
 
    class Config:
        """Pydantic settings: read attributes from ORM objects."""
        from_attributes = True

class ServerResponse(BaseModel):
    """A registered server/agent as listed in the dashboard."""
    id:              str
    hostname:        str
    ip_address:      Optional[str]
    os_type:         Optional[str]
    os_version:      Optional[str]
    agent_version:   Optional[str]
    status:          Optional[str]
    last_heartbeat:  Optional[datetime]
    created_at:      Optional[datetime]
    decommissioned_at: Optional[datetime] = None
    decommissioned_by: Optional[str] = None
    application:     Optional[ApplicationInfo] = None

    class Config:
        """Pydantic settings: read attributes from ORM objects."""
        from_attributes = True  # allows reading from SQLAlchemy model

class ITSMConfig(BaseModel):
    """ITSM tool selection declared by the agent in bootstrap.yaml"""
    provider: str = "jira"   # "jira" or "servicenow"

class AgentRegistrationRequest(BaseModel):
    """Agent registration payload. `itsm` is accepted but ignored since v3.2."""
    hostname:         str
    ip_address:       str
    os_type:          str
    os_version:       str
    agent_version:    str
    capabilities:     List[str]
    application_name: str
    application_id:   Optional[str]       = None
    support_team:     Optional[SupportTeamConfig] = None
    environment:      str                 = "production"
    tags:             List[str]           = []
    itsm:             Optional[ITSMConfig] = None   # ignored since v3.2: the ticketing tool is set per application on the server

class EnrolmentTokenCreate(BaseModel):
    """Request to create an enrolment token (label, uses, days)."""
    label:           str
    max_uses:        int
    expires_in_days: int


def _audit(request: Optional[Request], action: str, *, actor_type: str, actor: Optional[str],
           actor_id: Optional[str] = None, resource_id: Optional[str] = None,
           outcome: str = "success", resource_type: str = "agent",
           details: Optional[Dict[str, Any]] = None):
    """Explicit audit event (the middleware skips /api/v1/agents/*). Never pass the enrolment
    secret or an API key in details."""
    from axara.community.common import audit
    audit.record(action, actor=actor, actor_id=actor_id, actor_type=actor_type,
                 resource_type=resource_type, resource_id=resource_id, outcome=outcome,
                 source_ip=audit.client_ip(request) if request is not None else None,
                 user_agent=request.headers.get("user-agent") if request is not None else None,
                 details=details or {})


def _server_dict(server: Server, application) -> Dict[str, Any]:
    """A server row as the API returns it.

    Args:
        server: Server row.
        application: Its Application row, or None.

    Returns:
        JSON-ready dict including the decommissioned fields.
    """
    return {
        "id":                server.id,
        "hostname":          server.hostname,
        "ip_address":        server.ip_address,
        "os_type":           server.os_type,
        "os_version":        server.os_version,
        "agent_version":     server.agent_version,
        "status":            server.status,
        "last_heartbeat":    server.last_heartbeat.isoformat() if server.last_heartbeat else None,
        "created_at":        server.created_at.isoformat() if server.created_at else None,
        "decommissioned_at": server.decommissioned_at.isoformat() if server.decommissioned_at else None,
        "decommissioned_by": server.decommissioned_by,
        "application": {
            "id":          application.id,
            "name":        application.name,
            "environment": application.environment,
        } if application else None,
    }


@router.get("/", response_model=list[ServerResponse])
def list_agents(include_decommissioned: bool = False, db: Session = Depends(get_db),
                _user: User = Depends(require_user)):
    """Registered servers/agents with their linked application (signed-in users).
    Removed (decommissioned) agents are left out unless include_decommissioned=true."""
    try:
        q = db.query(Server)
        if not include_decommissioned:
            q = q.filter(lifecycle.not_decommissioned())
        servers = q.all()

        result = []
        for server in servers:
            app_link = (
                db.query(ApplicationServer)
                .filter(ApplicationServer.server_id == server.id)
                .first()
            )
            application = (
                db.query(Application)
                .filter(Application.id == app_link.application_id)
                .first()
            ) if app_link else None

            result.append(_server_dict(server, application))

        return result
    except Exception as e:
        logger.error(f"Failed to list agents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------ enrolment tokens (admin)
# Declared before /{server_id} so the path is not taken for a server id.

@router.get("/enrolment-tokens")
def list_enrolment_tokens(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    """List enrolment tokens, newest first (admin). The secret is never returned.

    Args:
        db: SQLAlchemy session.
        _admin: The signed-in administrator.

    Returns:
        Token summaries (state, uses, expiry, hint).
    """
    from axara.community.database.models import AgentEnrolmentToken
    rows = db.query(AgentEnrolmentToken).order_by(AgentEnrolmentToken.created_at.desc()).all()
    return [lifecycle.token_public(r) for r in rows]


@router.post("/enrolment-tokens", status_code=201)
def create_enrolment_token(body: EnrolmentTokenCreate, request: Request,
                           db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Create a token for registering agents. The secret is in this response only."""
    try:
        row, secret = lifecycle.create_token(db, label=body.label, max_uses=body.max_uses,
                                             expires_in_days=body.expires_in_days,
                                             created_by=admin.username)
    except lifecycle.EnrolmentError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _audit(request, "agent.enrolment.create", actor_type="user", actor=admin.username,
           actor_id=str(admin.id), resource_type="agent_enrolment", resource_id=row.id,
           details={"label": row.label, "max_uses": row.max_uses,
                    "expires_at": row.expires_at.isoformat(), "hint": row.hint})
    return {**lifecycle.token_public(row), "secret": secret}


@router.post("/enrolment-tokens/{enrolment_id}/revoke")
def revoke_enrolment_token(enrolment_id: str, request: Request, db: Session = Depends(get_db),
                           admin: User = Depends(require_admin)):
    """Revoke an enrolment token (admin); agents already registered are unaffected.

    Args:
        enrolment_id: Token id.
        request: HTTP request (for the audit log).
        db: SQLAlchemy session.
        admin: The signed-in administrator.

    Returns:
        The token summary.

    Raises:
        HTTPException: 404 when the token does not exist.
    """
    row = lifecycle.revoke_token(db, enrolment_id, admin.username)
    if not row:
        raise HTTPException(status_code=404, detail="Enrolment token not found")
    _audit(request, "agent.enrolment.revoke", actor_type="user", actor=admin.username,
           actor_id=str(admin.id), resource_type="agent_enrolment", resource_id=row.id,
           details={"label": row.label, "use_count": row.use_count, "max_uses": row.max_uses})
    return lifecycle.token_public(row)


# ------------------------------------------------------------------ removal

@router.post("/deregister")
def deregister_agent(request: Request, db: Session = Depends(get_db),
                     server: Server = Depends(verify_agent_key)):
    """Called by `axara-agent uninstall --purge` with the agent's own key: archive its monitors,
    delete its server-side configuration, free its licence slot and revoke its key.
    Allowed in every licence stage (removing an agent only reduces usage)."""
    summary = lifecycle.decommission_server(db, server, f"agent:{server.hostname}")
    _audit(request, "agent.deregister", actor_type="agent", actor=server.hostname,
           actor_id=str(server.id), resource_id=str(server.id),
           details={**summary, "method": "agent uninstall --purge"})
    return {"success": True, **summary}


@router.delete("/{server_id}")
def remove_agent(server_id: str, request: Request, db: Session = Depends(get_db),
                 admin: User = Depends(require_admin)):
    """Admin removal, for hosts that can no longer run the uninstall themselves.
    Same effect as the agent's own deregistration; the agent's key stops working."""
    server = ServerRepository(db).find_by_id(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    if server.status == lifecycle.DECOMMISSIONED:
        raise HTTPException(status_code=409, detail="This agent has already been removed")
    summary = lifecycle.decommission_server(db, server, admin.username)
    _audit(request, "agent.remove", actor_type="user", actor=admin.username, actor_id=str(admin.id),
           resource_id=str(server.id), details={**summary, "method": "dashboard"})
    return {"success": True, **summary}


@router.get("/{server_id}")
def get_agent(server_id: str, db: Session = Depends(get_db),
              _user: User = Depends(require_user)):
    """Return a single server with its linked application (removed agents included)."""
    try:
        server_repo = ServerRepository(db)
        server = server_repo.find_by_id(server_id)
        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

        # Look up application via join table
        app_link = (
            db.query(ApplicationServer)
            .filter(ApplicationServer.server_id == server_id)
            .first()
        )
        application = (
            db.query(Application)
            .filter(Application.id == app_link.application_id)
            .first()
        ) if app_link else None

        return _server_dict(server, application)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get agent {server_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

_REFUSED = {
    "missing": "An enrolment token is required to register an agent. Create one in the dashboard "
               "(Agent enrolment) and run: axara-agent install --enrol-token-file <file> ...",
    "unknown": "The enrolment token is not valid.",
    "revoked": "The enrolment token has been revoked.",
    "expired": "The enrolment token has expired.",
    "used_up": "The enrolment token has already been used for its maximum number of agents.",
}


@router.post("/register", response_model=AgentRegistrationResponse)
def register_agent(
    request: AgentRegistrationRequest,
    http_request: Request,
    db: Session = Depends(get_db)
):
    """
    Register a NEW monitoring agent. Requires a valid enrolment token in the
    X-Axara-Enrolment-Token header. A hostname that already has a registered agent is refused
    (remove the old agent first); a host whose agent was removed gets a new server id.
    Creates the server, links the application and creates its monitor configurations.
    """
    from axara.community.plugins.loader import get_registry
    host = (request.hostname or "").strip()
    who = {"hostname": host, "ip_address": request.ip_address, "os_type": request.os_type,
           "agent_version": request.agent_version, "application": request.application_name}

    def refuse(status: int, reason: str, message: str, **extra):
        """Audit a refused registration and raise.

        Args:
            status: HTTP status to return.
            reason: Machine-readable reason (audit and response code).
            message: Message for the installer.
            **extra: Extra audit details.

        Raises:
            HTTPException: always.
        """
        _audit(http_request, "agent.register", actor_type="agent", actor=host or None,
               outcome="denied", details={**who, "reason": reason, **extra})
        raise HTTPException(status_code=status, detail={"code": reason, "message": message})

    if not host or len(host) > 255:
        refuse(400, "invalid_hostname", "A hostname is required")
    enrolment, state = lifecycle.find_token(db, http_request.headers.get(lifecycle.ENROLMENT_HEADER))
    enrol_info = {"enrolment_id": enrolment.id, "enrolment_label": enrolment.label} if enrolment else {}
    if state != "active":
        refuse(401, f"enrolment_{state}", _REFUSED[state], **enrol_info)
    existing = lifecycle.registered_server_by_hostname(db, host)
    if existing:
        refuse(409, "hostname_registered",
               f"An agent is already registered for host '{existing.hostname}' (server id {existing.id}). "
               "Run 'axara-agent uninstall --purge' on the old installation, or remove the agent "
               "in the dashboard, then install again.", server_id=str(existing.id), **enrol_info)
    try:
        get_registry().run_checks("agent_registration")   # Enterprise: licence blocked / agent limit
    except HTTPException as e:
        _audit(http_request, "agent.register", actor_type="agent", actor=host, outcome="denied",
               details={**who, "reason": "licence", **enrol_info})
        raise e
    if not lifecycle.consume_token(db, enrolment.id):
        refuse(401, "enrolment_used_up", _REFUSED["used_up"], **enrol_info)
    try:
        app_repo = ApplicationRepository(db)
        server_repo = ServerRepository(db)
        monitor_repo = MonitorRepository(db)
        
        # Find or create application
        application = app_repo.find_by_name(request.application_name)

        if not application:
            # Create new application
            application = app_repo.create({
                "name":                 request.application_name,
                "environment":          request.environment,          # ← saved on create
                "primary_support_team": request.support_team.name  if request.support_team else None,
                "primary_assignee":     request.support_team.email if request.support_team else None,
                "tags":                 request.tags,
            })
            # Plugins may keep more of the registration's settings for the application.
            get_registry().emit("application_created", db, application,
                                request.support_team.model_dump() if request.support_team else {})

            logger.info(f"Created new application: {application.name}")

        else:
            # Update fields that may have changed or were blank
            updates = {}

            # Always sync it from the current registration
            if request.environment and application.environment != request.environment:
                updates["environment"] = request.environment


            if updates:
                app_repo.update(application.id, updates)
                db.refresh(application)
                logger.info(f"Updated application {application.name}: {list(updates.keys())}")
            else:
                logger.info(f"Application already up to date: {application.name}")
        
        # Always a new server row (and id): a host whose previous agent was removed keeps
        # that old, decommissioned row for history.
        server = server_repo.create({
            'hostname':      host,
            'ip_address':    request.ip_address,
            'os_type':       request.os_type,
            'os_version':    (request.os_version or '')[:100],
            'agent_version': request.agent_version,
            'last_heartbeat': datetime.utcnow(),
            'status':        'active'
        })
        logger.info(f"Created new server: {server.hostname} (id={server.id})")
        
        # Link application and server
        app_repo.link_server(application.id, server.id)
        
        # Generate API key for agent
        api_key = server_repo.generate_api_key(server.id)
        
        # Create/update monitor configurations based on capabilities
        monitor_configs = _create_monitor_configs(
            db,
            monitor_repo,
            application,
            server,
            request.capabilities
        )
        
        # Get monitor script info
        monitor_scripts = _get_monitor_scripts()
        
        # Build response
        monitor_configs_response = []
        for config in monitor_configs:
            assignee = config.assignee_override or application.primary_assignee
            
            notification_channels = []
            notif_config = config.notification_override
            if notif_config and 'channels' in notif_config:
                notification_channels = notif_config['channels']
            
            monitor_configs_response.append(MonitorConfigResponse(
                monitor_config_id=config.id,
                name=config.name,
                type=config.type,
                subtype=config.subtype,
                enabled=config.enabled,
                check_interval_seconds=config.check_interval_seconds,
                thresholds=config.threshold_config or {},
                consecutive_failures=config.consecutive_failures,
                severity=config.severity,
                assignee=assignee,
                notification_channels=notification_channels,
                config=config.config or {}
            ))
        
        response = AgentRegistrationResponse(
            success=True,
            server_id=server.id,
            application_id=application.id,
            check_interval_seconds=300,  # Default 5 minutes
            api_key=api_key,
            monitor_configs=monitor_configs_response,
            monitor_scripts=monitor_scripts
        )
        
        logger.info(
            f"Agent registration complete: "
            f"server_id={server.id}, application_id={application.id}"
        )
        get_registry().emit("agents_changed")        # e.g. the licence's agent count
        _audit(http_request, "agent.register", actor_type="agent", actor=host,
               actor_id=str(server.id), resource_id=str(server.id),
               details={**who, **enrol_info, "server_id": str(server.id),
                        "monitors": len(monitor_configs)})
        return response
        
    except Exception as e:
        logger.error(f"Agent registration error: {e}", exc_info=True)
        lifecycle.release_token(db, enrolment.id)
        _audit(http_request, "agent.register", actor_type="agent", actor=host, outcome="failure",
               details={**who, **enrol_info, "reason": "server_error"})
        raise HTTPException(status_code=500, detail="Agent registration failed - see the server log")


@router.post("/heartbeat")
def agent_heartbeat(
    request: HeartbeatRequest,
    db: Session = Depends(get_db),
    _server: Server = Depends(verify_agent_key),
):
    """Update agent last heartbeat timestamp — requires valid agent Bearer token"""
    from axara.community.plugins.loader import get_registry
    require_own_server(request.server_id, _server)      # a key only speaks for its own server
    get_registry().run_checks("agent_intake", str(_server.id))
    try:
        server_repo = ServerRepository(db)
        server = _server
        
        server_repo.update(server.id, {
            'last_heartbeat': datetime.now(timezone.utc),
            'status': 'active'
        })
        db.commit()

        # Regenerate monitors.yaml as a fallback sync (non-fatal if it fails)
        try:
            _regenerate_monitors_yaml(str(server.id), db)
        except Exception as e:
            logger.warning(f"monitors.yaml regeneration failed on heartbeat: {e}")
        
        return {"status": "ok"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Heartbeat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/check-scripts")
def check_script_versions(
    request: CheckScriptsRequest,
    db: Session = Depends(get_db),
    _server: Server = Depends(verify_agent_key),
):
    """
    Check if agent's monitor scripts need updating — requires valid agent Bearer token
    """
    from axara.community.plugins.loader import get_registry
    require_own_server(request.server_id, _server)
    get_registry().run_checks("agent_intake", str(_server.id))
    try:
        # Get latest script versions from server
        latest_scripts = _get_monitor_scripts()
        
        # Compare with agent's local scripts
        scripts_to_update = []
        
        for local_script in request.local_scripts:
            latest = next(
                (s for s in latest_scripts if s['script_name'] == local_script.script_name),
                None
            )
            
            if latest:
                # Compare by checksum — detects content changes even when the
                # version string was not bumped. Fall back to version comparison
                # if the client did not send a checksum (older agent).
                checksum_differs = (
                    local_script.checksum and
                    latest['checksum'] != local_script.checksum
                )
                version_differs = latest['version'] != local_script.version
                if checksum_differs or (not local_script.checksum and version_differs):
                    scripts_to_update.append({
                        'script_name':    local_script.script_name,
                        'current_version': local_script.version,
                        'latest_version':  latest['version'],
                        'changelog': f"Updated to version {latest['version']}",
                        'checksum':   latest['checksum'],
                        'download_url': f"/api/v1/scripts/{local_script.script_name}/{latest['version']}"
                    })

        # Scripts the agent does not have yet (e.g. a monitor served by an Enterprise
        # plugin): offered as new installs. Agents only accept *_monitor.py names.
        local_names = {ls.script_name for ls in request.local_scripts}
        for latest in latest_scripts:
            if latest['script_name'] not in local_names:
                scripts_to_update.append({
                    'script_name':     latest['script_name'],
                    'current_version': None,
                    'latest_version':  latest['version'],
                    'changelog':       f"New monitor script {latest['version']}",
                    'checksum':        latest['checksum'],
                    'download_url':    f"/api/v1/scripts/{latest['script_name']}/{latest['version']}",
                })

        return {
            'updates_available': len(scripts_to_update) > 0,
            'scripts_to_update': scripts_to_update,
            'scripts_deprecated': []
        }
        
    except Exception as e:
        logger.error(f"Script version check error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _create_monitor_configs(
    db,
    monitor_repo,
    application,
    server,
    capabilities,
):
    """
    Create default MonitorConfig records based on declared agent capabilities.
    Called during agent registration.  Returns list of MonitorConfig ORM objects.
    The types in the registry's default_enabled_monitors() start enabled (Community: cpu,
    memory, disk, network and ssl); the rest start disabled until configured.
    """
    from axara.community.plugins.loader import get_registry
    on = get_registry().default_enabled_monitors()
    configs = []

    # CPU Monitor
    if 'cpu' in capabilities:
        configs.append(monitor_repo.find_or_create(
            application_id=application.id,
            server_id=server.id,
            monitor_type='cpu',
            defaults={
                'name': 'CPU Monitor',
                'enabled': 'cpu' in on,
                'check_interval_seconds': 300,
                'threshold_config': {
                    'cpu_percent': {'warning': 70, 'critical': 80}
                },
                'consecutive_failures': 3,
                'severity': 'high',
                'config': {}
            }
        ))

    # Memory Monitor
    if 'memory' in capabilities:
        configs.append(monitor_repo.find_or_create(
            application_id=application.id,
            server_id=server.id,
            monitor_type='memory',
            defaults={
                'name': 'Memory Monitor',
                'enabled': 'memory' in on,
                'check_interval_seconds': 300,
                'threshold_config': {
                    'memory_percent': {'warning': 80, 'critical': 90}
                },
                'consecutive_failures': 3,
                'severity': 'high',
                'config': {}
            }
        ))

    # Disk Monitor
    if 'disk' in capabilities:
        configs.append(monitor_repo.find_or_create(
            application_id=application.id,
            server_id=server.id,
            monitor_type='disk',
            defaults={
                'name': 'Disk Monitor',
                'enabled': 'disk' in on,
                'check_interval_seconds': 300,
                'threshold_config': {
                    'disk_percent': {'warning': 75, 'critical': 90}
                },
                'consecutive_failures': 3,
                'severity': 'high',
                'config': {}
            }
        ))

    # Network Monitor
    if 'network' in capabilities:
        configs.append(monitor_repo.find_or_create(
            application_id=application.id,
            server_id=server.id,
            monitor_type='network',
            defaults={
                'name': 'Network Monitor',
                'enabled': 'network' in on,
                'check_interval_seconds': 300,
                'threshold_config': {
                    'max_errors': 1000,
                    'max_drops':  1000,
                },
                'consecutive_failures': 3,
                'severity': 'medium',
                'config': {}
            }
        ))

    # URL Monitor
    # Created as disabled by default — operator must supply the URL in config
    if 'url' in capabilities:
        configs.append(monitor_repo.find_or_create(
            application_id=application.id,
            server_id=server.id,
            monitor_type='url',
            defaults={
                'name': 'URL Monitor',
                'enabled': False,           # enable after setting the URL below
                'check_interval_seconds': 60,
                'threshold_config': {
                    'response_time_ms': {'warning': 1000, 'critical': 3000}
                },
                'consecutive_failures': 3,
                'severity': 'critical',
                'config': {
                    'url':                    '',     # set this in monitors.yaml
                    'method':                 'GET',
                    'expected_status':        200,
                    'expected_body_contains': '',
                    'timeout':                10,
                    'verify_ssl':             True,
                }
            }
        ))

    # Process Monitor
    if 'process' in capabilities:
        configs.append(monitor_repo.find_or_create(
            application_id=application.id,
            server_id=server.id,
            monitor_type='process',
            defaults={
                'name': 'Process Monitor',
                'enabled': False,           # enable after setting process_name below
                'check_interval_seconds': 120,
                'threshold_config': {},
                'consecutive_failures': 2,
                'severity': 'critical',
                'config': {
                    'process_name':      '',    # set this in monitors.yaml
                    'expected_min_count': 1,
                    'match_cmdline':      False,
                }
            }
        ))

    # Port Monitor
    if 'port' in capabilities:
        configs.append(monitor_repo.find_or_create(
            application_id=application.id,
            server_id=server.id,
            monitor_type='port',
            defaults={
                'name': 'Port Monitor',
                'enabled': False,           # enable after setting port below
                'check_interval_seconds': 60,
                'threshold_config': {},
                'consecutive_failures': 3,
                'severity': 'critical',
                'config': {
                    'port':     0,          # set this in monitors.yaml
                    'protocol': 'tcp',
                }
            }
        ))

    # Browser Monitor
    if 'browser' in capabilities:
        configs.append(monitor_repo.find_or_create(
            application_id=application.id,
            server_id=server.id,
            monitor_type='browser',
            defaults={
                'name': 'Browser Monitor',
                'enabled': False,           # enable after creating the script
                'check_interval_seconds': 300,
                'threshold_config': {
                    'duration_ms': {'warning': 5000, 'critical': 10000}
                },
                'consecutive_failures': 2,
                'severity': 'high',
                'config': {
                    'script_path':        '',   # set this in monitors.yaml
                    'timeout':            120,
                }
            }
        ))

    # Log Monitor
    if 'log' in capabilities:
        configs.append(monitor_repo.find_or_create(
            application_id=application.id,
            server_id=server.id,
            monitor_type='log',
            defaults={
                'name': 'Log Monitor',
                'enabled': False,       # enable after setting log_paths + patterns
                'check_interval_seconds': 300,
                'threshold_config': {}, # thresholds are per-pattern (inside config)
                'consecutive_failures': 3,
                'severity': 'high',
                'config': {
                    # Glob patterns supported, e.g. /var/log/app/*.log
                    'log_paths': [],            # REQUIRED — list of file path globs
                    'include_context_lines': 5, # lines before/after match
                    'patterns': [
                        # Each pattern: name, regex (re.IGNORECASE applied),
                        #               level, threshold (max occurrences before fail)
                        {
                            'name':      'exceptions',
                            'regex':     r'(exception|traceback|error)',
                            'level':     'ERROR',
                            'threshold': 0,     # fail on first occurrence
                        },
                        {
                            'name':      'warnings',
                            'regex':     r'\bwarn(ing)?\b',
                            'level':     'WARN',
                            'threshold': 10,    # allow up to 10 per check interval
                        },
                        {
                            'name':      'out_of_memory',
                            'regex':     r'(out of memory|oom|killed)',
                            'level':     'CRITICAL',
                            'threshold': 0,
                        },
                    ]
                }
            }
        ))

    # Database — MongoDB Monitor
    if 'mongodb' in capabilities:
        configs.append(monitor_repo.find_or_create(
            application_id=application.id,
            server_id=server.id,
            monitor_type='database',
            defaults={
                'name': 'MongoDB Monitor',
                'subtype': 'mongodb',
                'enabled': False,       # enable after setting config
                'check_interval_seconds': 60,
                'threshold_config': {
                    'connection_time_ms': {'warning': 500, 'critical': 2000},
                    'replication_lag_ms': {'warning': 5000, 'critical': 30000},
                },
                'consecutive_failures': 3,
                'severity': 'critical',
                'config': {
                    'host':            'localhost',  # REQUIRED
                    'port':            27017,
                    'user':            '',           # REQUIRED
                    'password':        '',           # REQUIRED
                    'database':        'admin',
                    'timeout_seconds': 10,
                }
            }
        ))

    # Database — MySQL Monitor
    if 'mysql' in capabilities:
        configs.append(monitor_repo.find_or_create(
            application_id=application.id,
            server_id=server.id,
            monitor_type='database',
            defaults={
                'name': 'MySQL Monitor',
                'subtype': 'mysql',
                'enabled': False,
                'check_interval_seconds': 60,
                'threshold_config': {
                    'connection_time_ms': {'warning': 500, 'critical': 2000},
                },
                'consecutive_failures': 3,
                'severity': 'critical',
                'config': {
                    'host':            'localhost',  # REQUIRED
                    'port':            3306,
                    'user':            '',           # REQUIRED
                    'password':        '',           # REQUIRED
                    'database':        'mysql',
                    'timeout_seconds': 10,
                }
            }
        ))

    # Database — PostgreSQL Monitor
    # db_monitor.py reads: host, port, user, password, database, timeout_seconds
    if 'postgres' in capabilities:
        configs.append(monitor_repo.find_or_create(
            application_id=application.id,
            server_id=server.id,
            monitor_type='database',
            defaults={
                'name': 'PostgreSQL Monitor',
                'subtype': 'postgres',
                'enabled': False,
                'check_interval_seconds': 60,
                'threshold_config': {
                    'connection_time_ms': {'warning': 500, 'critical': 2000},
                },
                'consecutive_failures': 3,
                'severity': 'critical',
                'config': {
                    'host':            'localhost',  # REQUIRED
                    'port':            5432,
                    'user':            '',           # REQUIRED
                    'password':        '',           # REQUIRED
                    'database':        'postgres',
                    'timeout_seconds': 10,
                }
            }
        ))

    # SSL Certificate Monitor
    if 'ssl' in capabilities:
        configs.append(monitor_repo.find_or_create(
            application_id=application.id,
            server_id=server.id,
            monitor_type='ssl',
            defaults={
                'name': 'SSL Certificate Monitor',
                'enabled': 'ssl' in on,     # safe to enable immediately — no secrets needed
                'check_interval_seconds': 86400,  # daily
                'threshold_config': {
                    'days_remaining': {'warning': 30, 'critical': 7}
                },
                'consecutive_failures': 1,
                'severity': 'critical',
                'config': {
                    'timeout':          10,     # TLS handshake timeout per port (seconds)
                }
            }
        ))

    # Monitors added by plugins.
    from axara.community.plugins.loader import get_registry
    configs.extend(get_registry().run_agent_registered(db, monitor_repo, application, server, capabilities))

    return configs


def _get_monitor_scripts() -> List[Dict[str, str]]:
    """
    Get list of available monitor scripts with version and checksum.
    Delegates to scripts.py _build_registry() so the registration response
    and /api/v1/scripts/ endpoint always return consistent data.
    """
    from axara.community.api.routes.scripts import _build_registry
    registry = _build_registry()
    return [
        {"script_name": name, "version": info["version"], "checksum": info["checksum"]}
        for name, info in registry.items()
    ]
