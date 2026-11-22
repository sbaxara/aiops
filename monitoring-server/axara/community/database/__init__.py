# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Database engine and session factory.

The connection string comes from DATABASE_URL (set by Docker Compose from DB_PASSWORD in
.env). There is no built-in password: without DATABASE_URL the server tries a local
database as user `monitoring` with no password, which a configured Postgres refuses.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://monitoring@localhost:5432/monitoring")

engine = create_engine(
    DATABASE_URL,
    pool_size=10,        # up from default 5
    max_overflow=20,     # up from default 10
    pool_timeout=30,
    pool_pre_ping=True,  # detect stale connections
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: a session closed after the request.

    Yields:
        SQLAlchemy session.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

