# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Maintenance Window Manager

Owns maintenance-window logic: CRUD and the hot-path suppression lookup that the
incident lifecycle, notifier and escalation manager consult.

suppress_type semantics (while a window is in effect for the scoped server(s)):
  - 'incidents' : suppress incident CREATION (updates / auto-resolve still run)
  - 'alerts'    : suppress NOTIFICATIONS and ESCALATION for the server's incidents
  - 'both'      : suppress creation AND notifications/escalation

Scope:
  - window.server_id set   -> that single server
  - window.server_id NULL  -> all servers of window.application_id
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from axara.community.database.models import MaintenanceWindow
from axara.community.database.repositories.maintenance_window_repo import MaintenanceWindowRepository

logger = logging.getLogger(__name__)

VALID_SUPPRESS_TYPES = {"incidents", "alerts", "both"}


class MaintenanceWindowManager:
    """Maintenance window CRUD + suppression resolution."""

    def __init__(self, db: Session):
        """Bind the repository to a session.

        Args:
            db: SQLAlchemy session.
        """
        self.db = db
        self.repo = MaintenanceWindowRepository(db)

    # ---------------------------------------------------------- suppression

    def active_suppress_type(
        self, server_id: str, application_id: str = None
    ) -> Optional[str]:
        """
        Return the effective suppress_type for a server right now, or None if the
        server is not under any active maintenance window.

        If multiple windows overlap, the STRONGEST suppression wins:
        'both' > ('incidents' or 'alerts' combined) > single. Concretely, if any
        active window says 'both', or if both 'incidents' and 'alerts' appear
        across active windows, the result is 'both'.
        """
        try:
            windows = self.repo.find_active_for_server(server_id, application_id)
        except Exception as e:
            # Fail OPEN (no suppression) on lookup error — never block incident
            # processing because the maintenance lookup failed.
            logger.error(f"Maintenance lookup failed for server {server_id}: {e}")
            return None

        if not windows:
            return None

        types = {w.suppress_type for w in windows if w.suppress_type}
        if "both" in types or ({"incidents", "alerts"} <= types):
            return "both"
        if "incidents" in types:
            return "incidents"
        if "alerts" in types:
            return "alerts"
        return None

    def is_creation_suppressed(self, server_id: str, application_id: str = None) -> bool:
        """True if incident CREATION should be suppressed for this server now."""
        return self.active_suppress_type(server_id, application_id) in ("incidents", "both")

    def is_alerting_suppressed(self, server_id: str, application_id: str = None) -> bool:
        """True if NOTIFICATIONS and ESCALATION should be suppressed for this server now."""
        return self.active_suppress_type(server_id, application_id) in ("alerts", "both")

    # ---------------------------------------------------------- CRUD

    def create_window(self, data: Dict[str, Any]) -> MaintenanceWindow:
        """
        Create a maintenance window. Expects:
          application_id (required), server_id (None = all servers of the app),
          start_time, end_time (ISO strings or datetimes), description,
          suppress_type ('incidents'|'alerts'|'both'), name, created_by.
        """
        suppress_type = data.get("suppress_type", "both")
        if suppress_type not in VALID_SUPPRESS_TYPES:
            raise ValueError(
                f"invalid suppress_type {suppress_type!r}; "
                f"must be one of {sorted(VALID_SUPPRESS_TYPES)}"
            )

        start = self._parse_dt(data.get("start_time"))
        end = self._parse_dt(data.get("end_time"))
        if start is None or end is None:
            raise ValueError("start_time and end_time are required")
        if end <= start:
            raise ValueError("end_time must be after start_time")

        return self.repo.create({
            "name":           data.get("name"),
            "application_id": data.get("application_id"),
            "server_id":      data.get("server_id"),  # None => all servers
            "start_time":     start,
            "end_time":       end,
            "description":    data.get("description"),
            "suppress_type":  suppress_type,
            "created_by":     data.get("created_by"),
        })

    def delete_window(self, window_id: str):
        """Delete a maintenance window.

        Args:
            window_id: Window id.

        Returns:
            The deleted window, or None.
        """
        return self.repo.delete(window_id)

    def list_for_application(self, application_id: str) -> Dict[str, List[Dict[str, Any]]]:
        """
        Return windows for an application split into 'active' and 'past'.
          active = end_time >= now  (currently in effect OR scheduled future)
          past   = end_time <  now  (already ended)
        Each item is annotated with 'state' = 'in_effect' | 'scheduled' | 'ended'.
        """
        now = datetime.now(timezone.utc)
        rows = self.repo.find_for_application(application_id)
        active, past = [], []
        for w in rows:
            item = self._to_dict(w, now)
            (active if w.end_time >= now else past).append(item)
        return {"active": active, "past": past}

    # ---------------------------------------------------------- helpers

    @staticmethod
    def _parse_dt(v):
        """Parse a datetime or ISO-8601 text (a trailing Z is accepted); other values give None."""
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        return None

    @staticmethod
    def _to_dict(w: MaintenanceWindow, now: datetime) -> Dict[str, Any]:
        """A window as an API dict, with its state (scheduled, in_effect or ended) at `now`."""
        if w.start_time and w.start_time > now:
            state = "scheduled"
        elif w.end_time and w.end_time < now:
            state = "ended"
        else:
            state = "in_effect"
        return {
            "id":             w.id,
            "name":           w.name,
            "application_id": w.application_id,
            "server_id":      w.server_id,            # None => all servers
            "scope":          "all_servers" if w.server_id is None else "server",
            "start_time":     w.start_time.isoformat() if w.start_time else None,
            "end_time":       w.end_time.isoformat() if w.end_time else None,
            "description":    w.description,
            "suppress_type":  w.suppress_type,
            "created_by":     w.created_by,
            "created_at":     w.created_at.isoformat() if w.created_at else None,
            "state":          state,
        }
