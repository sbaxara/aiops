# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Audit trail API — admin only (router also sits behind the global JWT guard).

  GET /api/v1/audit/events   filtered, paginated list (newest first)
  GET /api/v1/audit/export   CSV export (logged as an explicit audit event)
  GET /api/v1/audit/verify   recompute the hash chain and report integrity
"""
import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from axara.community.database import get_db
from axara.community.database.models import User
from axara.community.api.routes.auth import require_admin
from axara.community.common.audit import client_ip, record as audit_record, verify_chain

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])

_COLS = ("id", "ts", "actor_type", "actor", "actor_id", "action", "resource_type",
         "resource_id", "outcome", "source_ip", "user_agent", "details",
         "prev_hash", "hash")
_EXPORT_MAX = 50000


def _parse_ts(v: str) -> datetime:
    """Parse an ISO-8601 timestamp (a trailing Z is accepted).

    Args:
        v: Timestamp text.

    Returns:
        The datetime.

    Raises:
        HTTPException: 400 when the text is not a timestamp.
    """
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timestamp: {v}")


def _filters(actor, action, outcome, since, until):
    """Build the WHERE clause and parameters for the audit-log filters.

    Args:
        actor: Substring of the actor.
        action: Substring of the action.
        outcome: Exact outcome.
        since: Earliest timestamp (ISO-8601).
        until: Latest timestamp (ISO-8601).

    Returns:
        (where clause, bind parameters).
    """
    where, params = ["1=1"], {}
    if actor:
        where.append("actor ILIKE :actor"); params["actor"] = f"%{actor}%"
    if action:
        where.append("action ILIKE :action"); params["action"] = f"%{action}%"
    if outcome:
        where.append("outcome = :outcome"); params["outcome"] = outcome
    if since:
        where.append("ts >= :since"); params["since"] = _parse_ts(since)
    if until:
        where.append("ts <= :until"); params["until"] = _parse_ts(until)
    return " AND ".join(where), params


def _row(r) -> dict:
    """Turn an audit_log row into a dict with an ISO timestamp."""
    d = dict(zip(_COLS, r))
    d["ts"] = d["ts"].isoformat() if d["ts"] else None
    return d


@router.get("/events")
def list_events(actor: Optional[str] = None, action: Optional[str] = None,
                outcome: Optional[str] = None, since: Optional[str] = None,
                until: Optional[str] = None,
                limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    """List audit events, newest first, with optional filters (admin only).

    Returns:
        {'total': count, 'events': [...]}.
    """
    where, params = _filters(actor, action, outcome, since, until)
    total = db.execute(text(f"SELECT count(*) FROM audit_log WHERE {where}"), params).scalar()
    rows = db.execute(text(f"""
        SELECT {', '.join(_COLS)} FROM audit_log WHERE {where}
         ORDER BY id DESC LIMIT :lim OFFSET :off
    """), {**params, "lim": limit, "off": offset}).fetchall()
    return {"total": total, "events": [_row(r) for r in rows]}


def _csv_safe(v) -> str:
    """Neutralise spreadsheet formula injection (=, +, -, @, tab, CR)."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@router.get("/export")
async def export_events(request: Request, actor: Optional[str] = None,
                        action: Optional[str] = None, outcome: Optional[str] = None,
                        since: Optional[str] = None, until: Optional[str] = None,
                        db: Session = Depends(get_db),
                        admin: User = Depends(require_admin)):
    """Export the filtered audit events as CSV, oldest first, up to the export limit (admin only).

    The export itself is recorded in the audit log.

    Returns:
        CSV download response.
    """
    where, params = _filters(actor, action, outcome, since, until)
    rows = db.execute(text(f"""
        SELECT {', '.join(_COLS)} FROM audit_log WHERE {where}
         ORDER BY id ASC LIMIT :lim
    """), {**params, "lim": _EXPORT_MAX}).fetchall()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_COLS)
    for r in rows:
        d = _row(r)
        w.writerow([_csv_safe(d[c]) for c in _COLS])

    await run_in_threadpool(
        audit_record, "audit.export", actor=admin.username, actor_id=str(admin.id),
        resource_type="audit", outcome="success", source_ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={"rows": len(rows), "filters": {"actor": actor, "action": action,
                 "outcome": outcome, "since": since, "until": until}},
    )
    fname = f"audit-log-{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/verify")
def verify(_admin: User = Depends(require_admin)):
    """Check the audit log's hash chain (admin only).

    Returns:
        The verification result from verify_chain().
    """
    return verify_chain()
