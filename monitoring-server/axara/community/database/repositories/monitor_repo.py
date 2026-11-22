# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Monitor Repository
"""
from sqlalchemy.orm import Session
from axara.community.database.models import MonitorConfig
from .base_repo import BaseRepository

class MonitorRepository(BaseRepository):
    """Queries and changes for monitor configs."""
    def __init__(self, db: Session):
        """Bind the repository to a session.

        Args:
            db: SQLAlchemy session.
        """
        super().__init__(db)
        self.model = MonitorConfig
    
    def find_or_create(self, application_id: str, server_id: str, monitor_type: str, defaults: dict):
        """Find existing monitor or create new one"""
        # Include subtype in lookup so that database monitors with different
        # subtypes (mongodb, mysql, postgres) are treated as distinct records.
        subtype = defaults.get('subtype')

        query = self.db.query(MonitorConfig).filter(
            MonitorConfig.application_id == application_id,
            MonitorConfig.server_id == server_id,
            MonitorConfig.type == monitor_type,
            MonitorConfig.subtype == subtype      # None == None is handled correctly by SQLAlchemy
        )

        monitor = query.first()

        if not monitor:
            monitor = self.create({
                'application_id': application_id,
                'server_id': server_id,
                'type': monitor_type,
                **defaults
            })

        return monitor