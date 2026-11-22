# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
User Repository
"""
from sqlalchemy.orm import Session
from axara.community.database.models import User
from .base_repo import BaseRepository

class UserRepository(BaseRepository):
    """Queries and changes for users."""
    def __init__(self, db: Session):
        """Bind the repository to a session.

        Args:
            db: SQLAlchemy session.
        """
        super().__init__(db)
        self.model = User
    
    def find_by_username(self, username: str):
        """The user with this username, or None."""
        return self.db.query(User).filter(User.username == username).first()
