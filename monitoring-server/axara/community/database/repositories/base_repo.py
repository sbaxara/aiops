# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Base Repository
"""
from sqlalchemy.orm import Session

class BaseRepository:
    """Basic create, read, update and delete for one model (set self.model in the subclass)."""
    def __init__(self, db: Session):
        """Bind the repository to a session.

        Args:
            db: SQLAlchemy session.
        """
        self.db = db
    
    def find_by_id(self, id: str):
        """The row with this id, or None."""
        return self.db.query(self.model).filter(self.model.id == id).first()
    
    def find_all(self):
        """All rows of the model."""
        return self.db.query(self.model).all()
    
    def create(self, data: dict):
        """Insert a row and commit.

        Args:
            data: Column values.

        Returns:
            The new row.
        """
        instance = self.model(**data)
        self.db.add(instance)
        self.db.commit()
        self.db.refresh(instance)
        return instance
    
    def update(self, id: str, data: dict):
        """Update a row's columns and commit.

        Args:
            id: Row id.
            data: Column values to set.

        Returns:
            The row, or None when it does not exist.
        """
        instance = self.find_by_id(id)
        if instance:
            for key, value in data.items():
                setattr(instance, key, value)
            self.db.commit()
            self.db.refresh(instance)
        return instance
    
    def delete(self, id: str):
        """Delete a row and commit.

        Args:
            id: Row id.

        Returns:
            The deleted row, or None when it did not exist.
        """
        instance = self.find_by_id(id)
        if instance:
            self.db.delete(instance)
            self.db.commit()
        return instance
