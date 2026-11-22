# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Incident Repository
"""
from sqlalchemy.orm import Session
from axara.community.database.models import Incident
from .base_repo import BaseRepository

class IncidentRepository(BaseRepository):
    """Queries and changes for incidents."""
    def __init__(self, db: Session):
        """Bind the repository to a session.

        Args:
            db: SQLAlchemy session.
        """
        super().__init__(db)
        self.model = Incident
    
    def find_open_by_key(self, incident_key: str):
        """Find open incident by deduplication key"""
        return self.db.query(Incident).filter(
            Incident.incident_key == incident_key,
            Incident.status.in_(['open', 'in_progress', 'investigating'])
        ).first()
    
    def find_all_open(self):
        """Find all open incidents"""
        return self.db.query(Incident).filter(
            Incident.status == 'open'
        ).all()
