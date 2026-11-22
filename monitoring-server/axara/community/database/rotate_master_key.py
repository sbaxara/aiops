#!/usr/bin/env python3
# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Re-encrypt every stored secret under the current MASTER_ENCRYPTION_KEY.

Procedure:
  1. In .env set MASTER_ENCRYPTION_KEY=<new> and MASTER_ENCRYPTION_KEY_PREVIOUS=<old>.
  2. Restart the server (both keys now decrypt; new writes use the new key).
  3. docker compose exec monitoring-server python manage.py rotate-master-key
  4. Remove MASTER_ENCRYPTION_KEY_PREVIOUS from .env and restart.
Idempotent; the run is recorded in the audit log.
"""
import os
import sys


def main() -> int:
    """Re-encrypt every stored secret under the current MASTER_ENCRYPTION_KEY.

    Values under the old key are read through MASTER_ENCRYPTION_KEY_PREVIOUS. The rotation
    is recorded in the audit log.

    Returns:
        Exit status (2 when no valid key is set).
    """
    from axara.community.common.audit import record
    from axara.community.common.crypto import encryption_available, rotate_config_secrets
    from axara.community.database import SessionLocal
    from axara.community.database.models import MonitorConfig

    if not encryption_available():
        print("ERROR: MASTER_ENCRYPTION_KEY not set or invalid", file=sys.stderr)
        return 2
    if not os.getenv("MASTER_ENCRYPTION_KEY_PREVIOUS"):
        print("NOTE: MASTER_ENCRYPTION_KEY_PREVIOUS is not set — values encrypted under an old key "
              "will fail to decrypt. Continuing (re-encrypts under the current key).")
    db = SessionLocal()
    rows = vals = 0
    try:
        for m in db.query(MonitorConfig).all():
            new, n = rotate_config_secrets(m.config)
            if n:
                m.config = new
                rows += 1
                vals += n
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"ERROR: rotation failed, nothing changed: {e}", file=sys.stderr)
        record("crypto.master_key_rotation", actor_type="system", outcome="failure",
               details={"error": str(e)[:300]})
        return 1
    finally:
        db.close()
    record("crypto.master_key_rotation", actor_type="system",
           details={"monitor_configs": rows, "values": vals})
    print(f"Re-encrypted {vals} secret value(s) across {rows} monitor config(s). "
          f"Now remove MASTER_ENCRYPTION_KEY_PREVIOUS and restart.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
