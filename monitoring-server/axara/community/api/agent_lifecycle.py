# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Agent lifecycle: enrolment tokens (who may register an agent) and decommissioning
(what happens when an agent is uninstalled with --purge or removed by an admin).

Enrolment tokens
  * created by an admin with a label, a use limit (1-1000) and an expiry (1-30 days);
  * the secret ("axe_" + 43 random URL-safe characters, 256 bits) is returned once and only
    its SHA-256 is stored; it is never logged or written to the audit log;
  * a registration consumes one use with a single conditional UPDATE, so two agents racing
    for the last use cannot both succeed; a registration that fails afterwards gives the use
    back.

Decommissioning (one transaction, then file clean-up)
  * the agent's monitors are archived: disabled and hidden, kept because deleting a monitor
    cascades to its metrics (past reports keep the host);
  * plugins delete their own data about the host (agent_decommissioned event), its API
    key is revoked and the server row is
    marked 'decommissioned' - it no longer counts as an active agent. The link to its
    platform application is kept so reports for past periods still show the host under
    that application;
  * its monitors.yaml folder under AGENT_CONFIG_DIR is deleted.
A later registration from the same host creates a NEW server id.
"""
import hashlib
import logging
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from sqlalchemy import func, or_, text, update
from sqlalchemy.orm import Session

from axara.community.database.models import AgentEnrolmentToken, MonitorConfig, Server

logger = logging.getLogger(__name__)

DECOMMISSIONED = "decommissioned"
SECRET_PREFIX = "axe_"
MAX_USES_LIMIT = 1000
MAX_DAYS = 30
ENROLMENT_HEADER = "X-Axara-Enrolment-Token"


def _now() -> datetime:
    """Current time (timezone-aware UTC).

    Returns:
        datetime.
    """
    return datetime.now(timezone.utc)


def _aware(ts: Optional[datetime]) -> Optional[datetime]:
    """Treat a naive timestamp as UTC (SQLite returns naive values).

    Args:
        ts: Timestamp or None.

    Returns:
        Timezone-aware timestamp, or None.
    """
    if ts is not None and ts.tzinfo is None:          # SQLite (tests) returns naive values
        return ts.replace(tzinfo=timezone.utc)
    return ts


def hash_secret(secret: str) -> str:
    """SHA-256 of an enrolment token secret (only the hash is stored).

    Args:
        secret: Token secret as issued.

    Returns:
        Hex digest.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def not_decommissioned():
    """SQL filter: servers that are registered agents (any status except decommissioned)."""
    return or_(Server.status.is_(None), Server.status != DECOMMISSIONED)


# ------------------------------------------------------------------ enrolment tokens

class EnrolmentError(ValueError):
    """An enrolment token request or registration that is not allowed (message is shown to the user)."""
    pass


def create_token(db: Session, *, label: str, max_uses: int, expires_in_days: int,
                 created_by: str) -> Tuple[AgentEnrolmentToken, str]:
    """Create an enrolment token.

    Args:
        db: SQLAlchemy session.
        label: Admin's label for the token (1-100 characters).
        max_uses: Number of agents that may register with it (1..MAX_USES_LIMIT).
        expires_in_days: Lifetime in days (1..MAX_DAYS).
        created_by: Admin username.

    Returns:
        (row, secret): the stored row and the secret, shown to the admin once and never again.

    Raises:
        EnrolmentError: when a value is out of range.
    """
    label = (label or "").strip()
    if not 1 <= len(label) <= 100:
        raise EnrolmentError("Label must be 1-100 characters")
    if not isinstance(max_uses, int) or not 1 <= max_uses <= MAX_USES_LIMIT:
        raise EnrolmentError(f"Number of agents must be between 1 and {MAX_USES_LIMIT}")
    if not isinstance(expires_in_days, int) or not 1 <= expires_in_days <= MAX_DAYS:
        raise EnrolmentError(f"Expiry must be between 1 and {MAX_DAYS} days")
    secret = SECRET_PREFIX + secrets.token_urlsafe(32)
    now = _now()
    row = AgentEnrolmentToken(label=label, secret_hash=hash_secret(secret), hint=secret[-4:],
                              max_uses=max_uses, use_count=0, created_by=created_by,
                              created_at=now, expires_at=now + timedelta(days=expires_in_days))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, secret


def token_state(row: AgentEnrolmentToken, now: Optional[datetime] = None) -> str:
    """State of an enrolment token.

    Args:
        row: Token row.
        now: Time to evaluate at (now when None).

    Returns:
        'revoked', 'expired', 'used_up' or 'active'.
    """
    now = now or _now()
    if row.revoked_at:
        return "revoked"
    if _aware(row.expires_at) <= now:
        return "expired"
    if row.use_count >= row.max_uses:
        return "used_up"
    return "active"


def token_public(row: AgentEnrolmentToken) -> Dict:
    """Token as shown in the dashboard (never includes the secret or its hash).

    Args:
        row: Token row.

    Returns:
        JSON-serialisable dict.
    """
    iso = lambda t: _aware(t).isoformat() if t else None
    return {"id": row.id, "label": row.label, "hint": f"{SECRET_PREFIX}...{row.hint}",
            "max_uses": row.max_uses, "use_count": row.use_count, "state": token_state(row),
            "created_by": row.created_by, "created_at": iso(row.created_at),
            "expires_at": iso(row.expires_at), "last_used_at": iso(row.last_used_at),
            "revoked_at": iso(row.revoked_at), "revoked_by": row.revoked_by}


def find_token(db: Session, secret: Optional[str]) -> Tuple[Optional[AgentEnrolmentToken], str]:
    """(row, state) for a presented secret; state 'missing' / 'unknown' when there is no row."""
    if not secret:
        return None, "missing"
    secret = secret.strip()
    if not secret.startswith(SECRET_PREFIX) or len(secret) > 200:
        return None, "unknown"
    row = db.query(AgentEnrolmentToken).filter(
        AgentEnrolmentToken.secret_hash == hash_secret(secret)).first()
    if not row:
        return None, "unknown"
    return row, token_state(row)


def consume_token(db: Session, token_id: str) -> bool:
    """Take one use, atomically. False when the token stopped being valid meanwhile."""
    now = _now()
    res = db.execute(
        update(AgentEnrolmentToken)
        .where(AgentEnrolmentToken.id == token_id,
               AgentEnrolmentToken.revoked_at.is_(None),
               AgentEnrolmentToken.expires_at > now,
               AgentEnrolmentToken.use_count < AgentEnrolmentToken.max_uses)
        .values(use_count=AgentEnrolmentToken.use_count + 1, last_used_at=now)
        .execution_options(synchronize_session=False))
    db.commit()
    return res.rowcount == 1


def release_token(db: Session, token_id: str) -> None:
    """Give a use back after a registration that failed once the use was taken."""
    try:
        db.rollback()
        db.execute(update(AgentEnrolmentToken)
                   .where(AgentEnrolmentToken.id == token_id, AgentEnrolmentToken.use_count > 0)
                   .values(use_count=AgentEnrolmentToken.use_count - 1)
                   .execution_options(synchronize_session=False))
        db.commit()
    except Exception:
        logger.exception("Could not give back an enrolment token use")


def revoke_token(db: Session, token_id: str, by: str) -> Optional[AgentEnrolmentToken]:
    """Revoke an enrolment token (no-op when already revoked).

    Args:
        db: SQLAlchemy session.
        token_id: Token id.
        by: Admin username.

    Returns:
        The token row, or None when not found.
    """
    row = db.query(AgentEnrolmentToken).filter(AgentEnrolmentToken.id == token_id).first()
    if row and not row.revoked_at:
        row.revoked_at, row.revoked_by = _now(), by
        db.commit()
        db.refresh(row)
    return row


# ------------------------------------------------------------------ agents

def registered_server_by_hostname(db: Session, hostname: str) -> Optional[Server]:
    """The registered (not decommissioned) agent for a hostname; case-insensitive."""
    return (db.query(Server)
            .filter(func.lower(Server.hostname) == (hostname or "").strip().lower(), not_decommissioned())
            .order_by(Server.created_at.desc())
            .first())


def decommission_server(db: Session, server: Server, by: str) -> Dict:
    """Decommission a registered agent. Returns a summary for the caller and the audit log."""
    if server.status == DECOMMISSIONED:
        raise ValueError("This agent has already been removed")
    sid, now = str(server.id), _now()
    monitors = (db.query(MonitorConfig)
                .filter(MonitorConfig.server_id == sid, MonitorConfig.archived_at.is_(None)).all())
    for m in monitors:
        m.enabled, m.archived_at, m.updated_at = False, now, now
    # Plugins remove their own data about the host, in this
    # transaction; a failure aborts the removal.
    from axara.community.plugins.loader import get_registry
    plugin_rows = sum(int(n or 0) for n in get_registry().emit("agent_decommissioned", db, sid, strict=True))
    server.status = DECOMMISSIONED
    server.api_key_hash = None                       # every agent endpoint now answers 401
    server.decommissioned_at, server.decommissioned_by = now, (by or "")[:255]
    db.commit()

    yaml_removed = False
    try:
        from axara.community.api.routes.config import _AGENT_CONFIG_DIR, _SERVER_ID_RE
        folder = _AGENT_CONFIG_DIR / sid
        if _SERVER_ID_RE.match(sid) and folder.is_dir() and folder.parent == _AGENT_CONFIG_DIR:
            shutil.rmtree(folder)
            yaml_removed = True
    except Exception:
        logger.warning("decommission: could not remove monitors.yaml folder for %s", sid, exc_info=True)
    from axara.community.plugins.loader import get_registry
    get_registry().emit("agents_changed")             # e.g. the licence's agent count
    logger.info("Agent %s (%s) decommissioned by %s: %d monitors archived", server.hostname, sid, by,
                len(monitors))
    return {"server_id": sid, "hostname": server.hostname, "monitors_archived": len(monitors),
            "plugin_rows_removed": int(plugin_rows),
            "monitors_yaml_removed": yaml_removed, "decommissioned_at": now.isoformat()}
