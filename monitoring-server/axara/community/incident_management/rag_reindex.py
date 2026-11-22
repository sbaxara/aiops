# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rebuild the similar-incident store (ChromaDB 'incidents') from resolved incidents in Postgres.

Run with `python manage.py rag-reindex` after upgrading to record version 2, or whenever the
store is lost. Uses the same rules as live storage (IncidentOrchestrator._store_in_rag):
incidents with neither a usable analysis nor resolution notes are skipped.
"""
import logging

logger = logging.getLogger(__name__)


def main() -> int:
    """Rebuild the incident memory from every resolved or closed incident.

    Returns:
        Exit status.
    """
    from axara.community.database import SessionLocal
    from axara.community.database.models import Incident
    from axara.community.incident_management.orchestrator import IncidentOrchestrator

    db = SessionLocal()
    try:
        orch = IncidentOrchestrator(db)
        orch.rag_engine.reset_collection()
        rows = (db.query(Incident)
                .filter(Incident.status.in_(("resolved", "closed")))
                .order_by(Incident.resolved_at)
                .all())
        stored = skipped = 0
        for inc in rows:
            if orch._store_in_rag(inc, check_licence=False):
                stored += 1
            else:
                skipped += 1
        print(f"RAG reindex: {stored} incidents stored, {skipped} skipped "
              f"(no usable analysis and no resolution notes), {len(rows)} resolved in total")
        return 0
    finally:
        db.close()
