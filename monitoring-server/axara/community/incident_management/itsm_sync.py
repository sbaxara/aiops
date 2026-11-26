# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ITSM sync job: resolves AIOps incidents whose tickets were resolved in the ticketing tool.

No inbound ticketing webhooks are needed. Every few minutes the job
asks each incident's ticket provider whether the ticket is resolved; when it is, the AIOps
incident is marked resolved, the ticket's resolution notes are kept on the incident, and the
incident is stored in incident memory (RAG) so similar incidents get those notes later.
The ticket is never resolved back from here, so the two systems cannot loop.

Interval: server setting itsm_sync.interval_minutes (default 5, minimum 1).
"""
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("open", "acknowledged", "in_progress", "investigating")
DEFAULT_INTERVAL_MINUTES = 5
BATCH_LIMIT = 200


def interval_minutes() -> int:
    """The polling interval from the server settings.

    Returns:
        Minutes between runs (at least 1; DEFAULT_INTERVAL_MINUTES when unset or invalid).
    """
    try:
        from axara.community.api.routes.config import _server_config
        value = int((_server_config.get("itsm_sync") or {}).get("interval_minutes", DEFAULT_INTERVAL_MINUTES))
        return max(1, value)
    except Exception:
        return DEFAULT_INTERVAL_MINUTES


def sync_once(db, registry=None) -> int:
    """Check every active incident that has a ticket and resolve those resolved in the ITSM tool.

    Args:
        db: SQLAlchemy session.
        registry: Plugin registry (defaults to the process registry).

    Returns:
        Number of incidents resolved in this run.
    """
    from axara.community.database.models import Incident
    from axara.community.incident_management.orchestrator import IncidentOrchestrator
    from axara.community.plugins.loader import get_registry

    reg = registry or get_registry()
    resolved = 0
    incidents = (db.query(Incident)
                 .filter(Incident.status.in_(ACTIVE_STATUSES), Incident.ticket_ref.isnot(None))
                 .order_by(Incident.created_at.asc())
                 .limit(BATCH_LIMIT).all())
    orch = None
    for inc in incidents:
        provider = reg.ticket_provider(inc.ticket_provider)
        if provider is None:                    # tool not installed or not enabled now
            continue
        ticket = inc.ticket_ref
        notes = provider.fetch_resolution(ticket)
        if notes is None:
            continue
        now = datetime.now(timezone.utc)
        inc.status, inc.resolved_at, inc.updated_at = "resolved", now, now
        if not inc.resolution_notes:
            inc.resolution_notes = notes
        db.commit()
        resolved += 1
        logger.info("Incident %s resolved from %s ticket %s", inc.id, provider.name, ticket)
        try:
            orch = orch or IncidentOrchestrator(db)
            orch._store_in_rag(inc, resolution_notes=inc.resolution_notes, source=provider.name)
        except Exception:
            logger.exception("Incident memory update failed for %s", inc.id)
    return resolved


class ItsmSync:
    """Background thread running sync_once every interval_minutes()."""

    def __init__(self):
        """Create a stopped job."""
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the background thread (idempotent)."""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="itsm-sync", daemon=True)
        self._thread.start()
        logger.info("ITSM sync job started (every %d min)", interval_minutes())

    def stop(self) -> None:
        """Ask the thread to stop at its next wake-up."""
        self._stop.set()

    def _run(self) -> None:
        """Thread body: one run per interval; a failed run is logged and retried next time."""
        from axara.community.database import SessionLocal
        while not self._stop.wait(interval_minutes() * 60):
            db = SessionLocal()
            try:
                n = sync_once(db)
                if n:
                    logger.info("ITSM sync: %d incident(s) resolved", n)
            except Exception:
                logger.exception("ITSM sync run failed")
                db.rollback()
            finally:
                db.close()
