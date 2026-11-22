# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Maintenance Window Repository
"""
from datetime import datetime, timezone
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session
from axara.community.database.models import MaintenanceWindow
from .base_repo import BaseRepository


class MaintenanceWindowRepository(BaseRepository):
    """Queries and changes for maintenance windows."""
    def __init__(self, db: Session):
        """Bind the repository to a session.

        Args:
            db: SQLAlchemy session.
        """
        super().__init__(db)
        self.model = MaintenanceWindow

    def find_for_application(self, application_id: str):
        """All windows owned by an application, newest start first."""
        return (
            self.db.query(MaintenanceWindow)
            .filter(MaintenanceWindow.application_id == application_id)
            .order_by(MaintenanceWindow.start_time.desc())
            .all()
        )

    def find_active_for_server(self, server_id: str, application_id: str = None):
        """
        Windows currently IN EFFECT that cover this server, i.e.
        start_time <= now <= end_time AND
        (server_id matches OR server_id IS NULL for the server's application).

        Used on the incident hot path; relies on an index on
        (server_id, start_time, end_time). application_id is required to match
        "all servers" windows (server_id NULL).
        """
        now = datetime.now(timezone.utc)
        scope = [MaintenanceWindow.server_id == server_id]
        if application_id is not None:
            scope.append(
                and_(
                    MaintenanceWindow.server_id.is_(None),
                    MaintenanceWindow.application_id == application_id,
                )
            )
        return (
            self.db.query(MaintenanceWindow)
            .filter(
                MaintenanceWindow.start_time <= now,
                MaintenanceWindow.end_time >= now,
                or_(*scope),
            )
            .all()
        )
