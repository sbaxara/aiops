# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQLAlchemy models of the Community tables.

Plugins may add their own tables; they share this Base and may point at these tables, but
nothing here points at them. The schema itself is created by the migrations
(database/migrations/versions, branch `core`).
"""
from sqlalchemy import (
    Column, String, Integer, BigInteger, Float, Boolean, DateTime,
    Text, ForeignKey, Index, JSON, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

Base = declarative_base()


def generate_uuid():
    """Generate UUID as string"""
    return str(uuid.uuid4())


class Application(Base):
    """Application being monitored"""
    __tablename__ = 'applications'
    
    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False, unique=True)
    type = Column(String(50))  # web, java, microservice, database
    description = Column(Text)
    environment = Column(String(50))  # production, staging, dev
    
    # Support team (ticket assignee)
    primary_support_team = Column(String(255))
    primary_assignee = Column(String(255))
    tags = Column(JSON, default=list)
    itsm_provider = Column(String(50))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    servers = relationship("ApplicationServer", back_populates="application")
    monitor_configs = relationship("MonitorConfig", back_populates="application")
    incidents = relationship("Incident", back_populates="application")


class Server(Base):
    """Server/host being monitored"""
    __tablename__ = 'servers'
    
    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    hostname = Column(String(255), nullable=False)
    ip_address = Column(String(45))
    os_type = Column(String(50))  # linux, windows
    os_version = Column(String(100))
    agent_version = Column(String(50))
    last_heartbeat = Column(DateTime(timezone=True))
    status = Column(String(20), default='active')  # active, inactive, maintenance, decommissioned
    # SHA-256 hex digest of the agent API key — raw token is never stored.
    # Populated by ServerRepository.generate_api_key() on registration.
    api_key_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    # Agent lifecycle (migration e1f2a3b4c5d6): set when the agent is uninstalled with --purge
    # or removed by an admin. The row is kept so past metrics/incidents/reports keep their host;
    # it no longer counts toward the licence and its API key is revoked.
    decommissioned_at = Column(DateTime(timezone=True))
    decommissioned_by = Column(String(255))
    
    # Relationships
    applications = relationship("ApplicationServer", back_populates="server")
    monitor_configs = relationship("MonitorConfig", back_populates="server")
    metrics = relationship("Metric", back_populates="server")
    incidents = relationship("Incident", back_populates="server")


class ApplicationServer(Base):
    """Many-to-many relationship between applications and servers"""
    __tablename__ = 'application_servers'
    
    application_id = Column(UUID(as_uuid=False), ForeignKey('applications.id'), primary_key=True)
    server_id = Column(UUID(as_uuid=False), ForeignKey('servers.id'), primary_key=True)
    
    # Relationships
    application = relationship("Application", back_populates="servers")
    server = relationship("Server", back_populates="applications")


class MonitorConfig(Base):
    """Monitor configuration"""
    __tablename__ = 'monitor_configs'
    
    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    type = Column(String(50), nullable=False)  # cpu, memory, disk, network, log, database
    subtype = Column(String(50))  # For database: mongodb, mysql, postgres
    application_id = Column(UUID(as_uuid=False), ForeignKey('applications.id'))
    server_id = Column(UUID(as_uuid=False), ForeignKey('servers.id'))
    enabled = Column(Boolean, default=True)
    check_interval_seconds = Column(Integer, default=300)
    threshold_config = Column(JSONB)
    consecutive_failures = Column(Integer, default=3)
    severity = Column(String(20))  # critical, high, medium, low
    
    # Override application defaults (optional)
    assignee_override = Column(String(255))
    notification_override = Column(JSONB)
    
    config = Column(JSONB)  # Type-specific configuration
    # Set when the monitor's agent was removed (disabled + hidden; kept because deleting a
    # monitor cascades to its metrics). Archived monitors cannot be edited.
    archived_at = Column(DateTime(timezone=True))
    
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    application = relationship("Application", back_populates="monitor_configs")
    server = relationship("Server", back_populates="monitor_configs")
    metrics = relationship("Metric", back_populates="monitor_config")
    monitor_state = relationship("MonitorState", back_populates="monitor_config", uselist=False)


class Metric(Base):
    """Time-series metrics"""
    __tablename__ = 'metrics'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    server_id = Column(UUID(as_uuid=False), ForeignKey('servers.id'), nullable=False)
    monitor_config_id = Column(UUID(as_uuid=False),
                               ForeignKey('monitor_configs.id', ondelete='CASCADE'))
    metric_type = Column(String(50))
    metric_name = Column(String(100))
    metric_value = Column(Float)
    metric_data = Column(JSONB)  # Additional metric details
    # Versioned envelope metadata: schema_version, monitor_version, unit.
    # Stored as JSONB per JSON Standardization Design (D2 §2). Greenfield
    # install — created with the table, no Alembic migration required.
    envelope = Column(JSONB)
    status = Column(String(20))  # pass, fail, warning, timeout, error
    timestamp = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    server = relationship("Server", back_populates="metrics")
    monitor_config = relationship("MonitorConfig", back_populates="metrics")
    
    __table_args__ = (
        Index('idx_metrics_server_time', 'server_id', 'timestamp'),
        Index('idx_metrics_monitor_time', 'monitor_config_id', 'timestamp'),
    )


class MonitorState(Base):
    """Tracks consecutive failures/successes for state machine"""
    __tablename__ = 'monitor_states'
    
    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    monitor_config_id = Column(UUID(as_uuid=False),
                               ForeignKey('monitor_configs.id', ondelete='CASCADE'), unique=True)
    current_status = Column(String(20), default='healthy')  # healthy, degraded, failed
    consecutive_failures = Column(Integer, default=0)
    last_failure_time = Column(DateTime(timezone=True))
    last_success_time = Column(DateTime(timezone=True))
    incident_id = Column(UUID(as_uuid=False), ForeignKey('incidents.id'))
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    monitor_config = relationship("MonitorConfig", back_populates="monitor_state")
    incident = relationship("Incident", foreign_keys=[incident_id])


class LogMonitorState(Base):
    """Tracks state per log file + pattern"""
    __tablename__ = 'log_monitor_states'
    
    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    monitor_config_id = Column(UUID(as_uuid=False),
                               ForeignKey('monitor_configs.id', ondelete='CASCADE'))
    log_file_path = Column(String(500))
    pattern_name = Column(String(100))
    consecutive_failures = Column(Integer, default=0)
    last_failure_time = Column(DateTime(timezone=True))
    last_success_time = Column(DateTime(timezone=True))
    last_error_count = Column(Integer, default=0)
    incident_id = Column(UUID(as_uuid=False), ForeignKey('incidents.id'))
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_log_state_unique', 'monitor_config_id', 'log_file_path', 'pattern_name', unique=True),
    )


class Incident(Base):
    """Incident record"""
    __tablename__ = 'incidents'
    
    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    incident_key = Column(String(255))  # For deduplication
    monitor_config_id = Column(UUID(as_uuid=False),
                               ForeignKey('monitor_configs.id', ondelete='SET NULL'))
    server_id = Column(UUID(as_uuid=False), ForeignKey('servers.id'))
    application_id = Column(UUID(as_uuid=False), ForeignKey('applications.id'))
    
    title = Column(String(500))
    description = Column(Text)
    severity = Column(String(20))  # critical, high, medium, low
    status = Column(String(50), default='open')  # open, in_progress, resolved, closed
    
    # ITSM ticket: the provider that opened it (Application.itsm_provider at the time, e.g.
    # 'jira') and its reference in that tool. One ticket per incident.
    ticket_provider = Column(String(30))
    ticket_ref = Column(String(100))
    
    assignee = Column(String(255))
    llm_analysis = Column(Text)
    llm_analysis_data = Column(JSONB, nullable=True)  # structured analysis (root_cause, recommendations, ...)
    similar_incidents = Column(JSONB)  # Array of similar incident references
    resolution_notes = Column(Text, nullable=True)     # how it was resolved (operator / ITSM)
    
    # Path to a screenshot PNG saved at incident-creation time.
    # Populated when a browser monitor captures a failure screenshot.
    # Stored as the absolute container path (e.g. /app/attachments/...).
    attachment = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at = Column(DateTime(timezone=True))
    
    # Relationships
    application = relationship("Application", back_populates="incidents")
    server = relationship("Server", back_populates="incidents")
    comments = relationship("IncidentComment", back_populates="incident")
    
    __table_args__ = (
        Index('idx_incidents_status', 'status'),
        Index('idx_incidents_severity', 'severity'),
        Index('idx_incidents_ticket', 'ticket_provider', 'ticket_ref'),
    )


class IncidentComment(Base):
    """Incident comments"""
    __tablename__ = 'incident_comments'
    
    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    incident_id = Column(UUID(as_uuid=False), ForeignKey('incidents.id'), nullable=False)
    comment_type = Column(String(50))  # llm_analysis, manual, system
    content = Column(Text)
    created_by = Column(String(255))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    incident = relationship("Incident", back_populates="comments")


class MaintenanceWindow(Base):
    """
    Scheduled maintenance windows.

    Scope:
      - server_id set      -> applies to that single server.
      - server_id NULL     -> applies to ALL servers of application_id ("All").
    application_id always records which application owns the window (used for
    listing under that app and for the "all servers" match).

    suppress_type controls what is suppressed while the window is in effect:
      - 'incidents' -> do not CREATE new incidents for the scoped server(s)
                       (updates and auto-resolution still proceed).
      - 'alerts'    -> incidents proceed normally, but notifications and
                       escalation are suppressed for the scoped server(s).
      - 'both'      -> incident creation AND notifications/escalation suppressed.
    """
    __tablename__ = 'maintenance_windows'

    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    name = Column(String(255))
    application_id = Column(UUID(as_uuid=False), ForeignKey('applications.id'))
    # Nullable: NULL means "all servers of application_id".
    server_id = Column(UUID(as_uuid=False), ForeignKey('servers.id'), nullable=True)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    description = Column(Text)
    # Renamed from suppress_alerts (Boolean). One of: 'incidents' | 'alerts' | 'both'.
    suppress_type = Column(String(20), default='both')
    created_by = Column(String(255))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class User(Base):
    """User accounts for RBAC"""
    __tablename__ = 'users'
    
    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    username = Column(String(255), unique=True, nullable=False)
    email = Column(String(255), unique=True)
    password_hash = Column(String(255))
    role = Column(String(50))  # admin, operator, viewer
    # Approval workflow: new registrations are pending until an admin approves
    is_approved = Column(Boolean, default=False)
    status = Column(String(20), default='pending')  # pending, active, disabled
    # Token invalidation: set on logout so previously issued JWTs are rejected
    token_invalidated_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    last_login = Column(DateTime(timezone=True))
    # Identity source: 'local' (password held here) or an SSO provider
    # ('ldap' | 'oidc' | 'saml'). SSO users have no password_hash.
    auth_source = Column(String(20), nullable=False, default='local', server_default='local')
    external_id = Column(String(255))      # IdP subject / directory DN

    __table_args__ = (
        UniqueConstraint('auth_source', 'external_id', name='uq_users_auth_source_external_id'),
    )


class PlatformConfig(Base):
    """
    Persisted platform-level configuration.
    Each row is a named config blob (key + JSONB value).
    Currently only one row is used: key='server_config'.
    Storing as a keyed table (rather than a single fixed row) makes it
    easy to add other platform-level configs later without schema changes.
    """
    __tablename__ = 'platform_config'

    id         = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    key        = Column(String(100), nullable=False, unique=True)
    value      = Column(JSONB, nullable=False)
    updated_at = Column(DateTime(timezone=True),
                        default=datetime.utcnow, onupdate=datetime.utcnow)

class APIKey(Base):
    """Encrypted API keys for external services"""
    __tablename__ = 'api_keys'
    
    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    service_name = Column(String(100))  # jira, servicenow, teams, slack
    key_name = Column(String(255))
    encrypted_key = Column(Text)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    expires_at = Column(DateTime(timezone=True))

class AgentEnrolmentToken(Base):
    """Admin-issued token an agent presents (X-Axara-Enrolment-Token) to register
    (migration e1f2a3b4c5d6). Only the SHA-256 of the secret is stored; the secret is shown
    once when created. Valid while not revoked, not expired and use_count < max_uses; a
    registration consumes one use atomically."""
    __tablename__ = 'agent_enrolment_tokens'

    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    label = Column(String(100), nullable=False)
    secret_hash = Column(String(64), nullable=False, unique=True)
    hint = Column(String(12), nullable=False)               # last characters, for identification
    max_uses = Column(Integer, nullable=False)
    use_count = Column(Integer, nullable=False, default=0)
    created_by = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    last_used_at = Column(DateTime(timezone=True))
    revoked_at = Column(DateTime(timezone=True))
    revoked_by = Column(String(255))


class AuditLog(Base):
    """Tamper-evident, append-only audit trail (common/audit.py; migration
    b8c9d0e1f2a3). hash = SHA-256 over the row's canonical contents incl.
    prev_hash, chaining every row to its predecessor. UPDATE/DELETE/TRUNCATE are
    blocked by DB triggers. Never write to this table except via common.audit.record."""
    __tablename__ = 'audit_log'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ts = Column(DateTime(timezone=True), nullable=False)
    actor_type = Column(String(20), nullable=False)       # user|agent|integration|system|anonymous
    actor = Column(String(255))
    actor_id = Column(String(64))
    action = Column(String(150), nullable=False)
    resource_type = Column(String(100))
    resource_id = Column(String(255))
    outcome = Column(String(20), nullable=False)          # success|denied|failure
    source_ip = Column(String(64))
    user_agent = Column(String(300))
    details = Column(JSONB)
    prev_hash = Column(String(64), nullable=False)
    hash = Column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint('hash', name='uq_audit_log_hash'),
        Index('idx_audit_log_ts', 'ts'),
        Index('idx_audit_log_actor', 'actor'),
        Index('idx_audit_log_action', 'action'),
        Index('idx_audit_log_outcome', 'outcome'),
    )
