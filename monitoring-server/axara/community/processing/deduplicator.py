# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Alert Deduplicator

Prevents duplicate incidents for the same monitor/server/metric combination.
Uses database-level SELECT ... FOR UPDATE to eliminate the TOCTOU race condition
that occurs when multiple background tasks process replayed offline metrics
concurrently.

The core problem:
  - Agent goes offline, queues N metric batches locally.
  - Agent reconnects, replays all N batches in rapid succession.
  - Server spawns N concurrent BackgroundTasks, each with its own DB session.
  - Multiple tasks can read the same MonitorState row simultaneously,
    all see consecutive_failures at threshold-1, all increment to threshold,
    and all call _create_incident() — bypassing the find_open_by_key() check
    because none has committed yet.

The fix:
  - Before creating an incident, acquire a PostgreSQL advisory lock keyed on
    the incident_key hash. This serialises all concurrent creation attempts
    for the same monitor, so only the first task creates; subsequent tasks
    find the existing incident.
  - Advisory locks are session-scoped and released on commit/rollback,
    so they don't persist across transactions.
"""
import hashlib
import logging
from typing import Optional, Dict, Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from axara.community.database.models import Incident
from axara.community.database.repositories.incident_repo import IncidentRepository

logger = logging.getLogger(__name__)


class AlertDeduplicator:
    """
    Alert deduplication using PostgreSQL advisory locks.

    Usage in state_machine.py:
        deduplicator = AlertDeduplicator(self.db)
        existing_or_new = deduplicator.deduplicate_and_create(
            incident_key=incident_key,
            incident_data={...},
        )
        if existing_or_new.was_existing:
            # Attach to existing incident
        else:
            # New incident — trigger orchestrator
    """

    def __init__(self, db: Session):
        """Bind the repository to a session.

        Args:
            db: SQLAlchemy session.
        """
        self.db = db
        self.incident_repo = IncidentRepository(db)

    def _advisory_lock_key(self, incident_key: str) -> int:
        """
        Convert an incident_key string to a 64-bit integer suitable for
        pg_advisory_xact_lock(). Uses the first 8 bytes of the SHA-256 hash.
        """
        digest = hashlib.sha256(incident_key.encode('utf-8')).digest()
        # Convert first 8 bytes to a signed 64-bit int (PostgreSQL bigint)
        return int.from_bytes(digest[:8], byteorder='big', signed=True)

    def find_open_incident(self, incident_key: str) -> Optional[Incident]:
        """
        Check for an existing active (non-resolved) incident with the
        given key. Does NOT acquire a lock — use deduplicate_and_create()
        for atomic check-and-create.
        """
        return self.db.query(Incident).filter(
            Incident.incident_key == incident_key,
            Incident.status.in_(['open', 'in_progress', 'investigating'])
        ).first()

    def deduplicate_and_create(
        self,
        incident_key: str,
        incident_data: Dict[str, Any],
    ) -> 'DeduplicationResult':
        """
        Atomically check for an existing open incident and create one if
        none exists. Uses a PostgreSQL transactional advisory lock to
        serialise concurrent attempts for the same incident_key.

        Args:
            incident_key: Deduplication key (monitor_config_id:server_id:type)
            incident_data: Dict of fields for incident_repo.create() if new

        Returns:
            DeduplicationResult with .incident and .was_existing
        """
        lock_key = self._advisory_lock_key(incident_key)

        try:
            # Acquire a transaction-scoped advisory lock.
            # pg_advisory_xact_lock blocks until the lock is available and
            # releases automatically on COMMIT or ROLLBACK.
            self.db.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": lock_key}
            )

            # Now we hold the lock — check again for an existing incident.
            # Any concurrent task that was also trying to create for this
            # same incident_key is blocked on the advisory lock above.
            existing = self.find_open_incident(incident_key)
            if existing:
                logger.info(
                    f"Deduplication hit: incident_key='{incident_key}' "
                    f"→ existing incident {existing.id}"
                )
                return DeduplicationResult(incident=existing, was_existing=True)

            # No existing incident — safe to create
            incident_data['incident_key'] = incident_key
            incident = self.incident_repo.create(incident_data)
            self.db.commit()

            logger.info(
                f"Deduplication miss: incident_key='{incident_key}' "
                f"→ new incident {incident.id}"
            )
            return DeduplicationResult(incident=incident, was_existing=False)

        except Exception as e:
            self.db.rollback()
            logger.error(
                f"Deduplication failed for incident_key='{incident_key}': {e}",
                exc_info=True
            )
            # Last-resort fallback: try to find an existing incident
            # (another concurrent task may have created it)
            try:
                existing = self.find_open_incident(incident_key)
                if existing:
                    return DeduplicationResult(
                        incident=existing, was_existing=True
                    )
            except Exception:
                pass
            raise


class DeduplicationResult:
    """Result of a deduplication check-and-create operation."""

    def __init__(self, incident: Incident, was_existing: bool):
        """Record the incident and whether it already existed.

        Args:
            incident: The incident.
            was_existing: True when an open incident was reused.
        """
        self.incident = incident
        self.was_existing = was_existing

    def __repr__(self):
        """Short description for logs."""
        status = "existing" if self.was_existing else "new"
        return f"<DeduplicationResult {status} incident={self.incident.id}>"
