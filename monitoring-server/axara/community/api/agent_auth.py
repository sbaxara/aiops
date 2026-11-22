# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Agent authentication: every agent-facing endpoint (after registration) takes the agent's
API key as a Bearer token. Kept free of route imports so config, metrics, scripts and plugin
routes can all depend on it without import cycles."""
from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from axara.community.database.models import Server
from axara.community.database.repositories.server_repo import ServerRepository

_bearer = HTTPBearer(auto_error=False)


def get_db():
    """Database session for one request, closed afterwards.

    Yields:
        SQLAlchemy session.
    """
    from axara.community.database import SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_agent_key(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
    db: Session = Depends(get_db),
) -> Server:
    """Validate the agent's Bearer key and return its Server row. 401 when the key is missing,
    unknown, or belongs to a removed (decommissioned) agent - its key is revoked on removal."""
    raw_key = credentials.credentials if credentials else None
    server = ServerRepository(db).find_by_api_key(raw_key) if raw_key else None
    if not server or server.status == "decommissioned":
        raise HTTPException(status_code=401, detail="Invalid or unrecognised agent API key",
                            headers={"WWW-Authenticate": "Bearer"})
    return server


def require_own_server(server_id: str, server: Server) -> None:
    """An agent may only act for its own server id."""
    if str(server_id) != str(server.id):
        raise HTTPException(status_code=403, detail="This agent key does not belong to that server")
