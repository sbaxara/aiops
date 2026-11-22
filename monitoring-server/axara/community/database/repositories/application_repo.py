# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Application Repository
"""
from sqlalchemy.orm import Session
from axara.community.database.models import Application
from .base_repo import BaseRepository

class ApplicationRepository(BaseRepository):
    """Queries and changes for applications."""
    def __init__(self, db: Session):
        """Bind the repository to a session.

        Args:
            db: SQLAlchemy session.
        """
        super().__init__(db)
        self.model = Application
    
    def find_by_name(self, name: str):
        """The application with this name, or None."""
        return self.db.query(Application).filter(Application.name == name).first()
    
    def link_server(self, application_id: str, server_id: str):
        """Link a server to an application (idempotent) and commit.

        Args:
            application_id: Application id.
            server_id: Server id.

        Returns:
            The link row.
        """
        from axara.community.database.models import ApplicationServer
        link = ApplicationServer(application_id=application_id, server_id=server_id)
        self.db.merge(link)
        self.db.commit()
        return link
