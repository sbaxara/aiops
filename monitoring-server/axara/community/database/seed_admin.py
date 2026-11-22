#!/usr/bin/env python3
# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Idempotent first-admin seeder.

A fresh install has no users, and normal registration creates a *pending*
account that only an admin can approve — a chicken-and-egg problem. This seeds
(or promotes) the first admin directly.

    python manage.py seed-admin

Environment:
    ADMIN_USERNAME         default 'admin'
    ADMIN_EMAIL            default '<username>@local'
    ADMIN_PASSWORD         required
    ADMIN_RESET_PASSWORD   '1' to reset an existing admin's password

Safe to re-run: creates the admin if absent, otherwise ensures it is an
approved, active admin (and optionally resets the password).
"""
import os
import sys
from datetime import datetime, timezone


def main() -> int:
    """Create or update the admin user from ADMIN_USERNAME, ADMIN_EMAIL and ADMIN_PASSWORD.

    The password must be at least 12 characters and differ from the username.

    Returns:
        Exit status (2 when the password is missing or too weak).
    """
    username = os.getenv("ADMIN_USERNAME", "admin").strip()
    email = os.getenv("ADMIN_EMAIL", f"{username}@local").strip()
    password = os.getenv("ADMIN_PASSWORD", "")
    if not password:
        print("ERROR: ADMIN_PASSWORD is not set", file=sys.stderr)
        return 2
    if len(password) < 12 or password.lower() == username.lower():
        print("ERROR: admin password must be at least 12 characters and must not equal "
              "the username", file=sys.stderr)
        return 2

    import bcrypt
    from axara.community.database import SessionLocal
    from axara.community.database.models import User

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == username).first()
        if u is None:
            db.add(User(
                username=username, email=email, password_hash=pw_hash,
                role="admin", is_approved=True, status="active",
                created_at=datetime.now(timezone.utc),
            ))
            db.commit()
            print(f"Created admin user '{username}'.")
        else:
            changed = []
            if u.role != "admin":
                u.role = "admin"; changed.append("role")
            if not u.is_approved:
                u.is_approved = True; changed.append("is_approved")
            if u.status != "active":
                u.status = "active"; changed.append("status")
            if os.getenv("ADMIN_RESET_PASSWORD", "").strip().lower() in ("1", "true", "yes"):
                u.password_hash = pw_hash; changed.append("password")
            db.commit()
            print(f"Admin '{username}' already exists; "
                  + (f"updated {changed}." if changed else "no changes needed."))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
