# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Server Repository
"""
from sqlalchemy.orm import Session
from axara.community.database.models import Server
from .base_repo import BaseRepository
import hashlib
import secrets

class ServerRepository(BaseRepository):
    """Queries and changes for servers (agents), including agent API keys."""
    def __init__(self, db: Session):
        """Bind the repository to a session.

        Args:
            db: SQLAlchemy session.
        """
        super().__init__(db)
        self.model = Server
    
    def find_by_hostname(self, hostname: str):
        """The server with this hostname, or None."""
        return self.db.query(Server).filter(Server.hostname == hostname).first()
    
    def generate_api_key(self, server_id: str) -> str:
        """
        Generate a unique API key for agent authentication, hash it with
        SHA-256, and persist the hash on the Server row.  The raw token
        is returned once to the caller (agent) and never stored again.
        """
        raw_key = f"agent_{server_id}_{secrets.token_urlsafe(32)}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        server = self.db.query(Server).filter(Server.id == server_id).first()
        if server:
            server.api_key_hash = key_hash
            self.db.commit()

        return raw_key

    @staticmethod
    def hash_key(raw_key: str) -> str:
        """Return SHA-256 hex digest of a raw API key."""
        return hashlib.sha256(raw_key.encode()).hexdigest()

    def find_by_api_key(self, raw_key: str):
        """Look up the Server whose api_key_hash matches the given raw key."""
        key_hash = self.hash_key(raw_key)
        return self.db.query(Server).filter(
            Server.api_key_hash == key_hash
        ).first()
