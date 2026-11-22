#!/usr/bin/env python3
# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Ensure ./.env exists and carries strong secrets. Run from the monitoring-server
directory (the bootstrap scripts cd here first). Shared by bootstrap.sh and
bootstrap.bat so both behave identically.

  - creates .env from .env.example if missing (else an empty .env)
  - fills SECRET_KEY / MASTER_ENCRYPTION_KEY when empty or still a placeholder
  - sets DB_PASSWORD: random on a fresh install; stops if a database exists without one
  - restricts .env to its owner (mode 600 / Windows ACL), since the secrets are in clear text
Idempotent: existing real values are left untouched.
"""
import os
import re
import secrets
import shutil

ENV = ".env"
KEYS = ["SECRET_KEY", "MASTER_ENCRYPTION_KEY"]
PLACEHOLDER = re.compile(r"your-|change|placeholder|min-32", re.I)


def restrict_env_file(path) -> bool:
    """.env holds DB_PASSWORD, SECRET_KEY and MASTER_ENCRYPTION_KEY in clear text (Docker Compose
    must read them), so only the owner may read it. Linux/macOS: mode 600. Windows: inheritance
    removed; full control for SYSTEM, Administrators and the user running this (so `docker compose`
    still works from a non-elevated terminal). Best effort: warns and returns False on failure."""
    path = str(path)
    if not os.path.exists(path):
        return False
    try:
        if os.name != "nt":
            os.chmod(path, 0o600)
            return True
        import subprocess
        who = subprocess.run(["whoami"], capture_output=True, text=True).stdout.strip()
        cmd = ["icacls", path, "/inheritance:r", "/grant:r", "*S-1-5-18:(F)", "*S-1-5-32-544:(F)"]
        if who:
            cmd.append(f"{who}:(F)")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise OSError((r.stdout or r.stderr).strip())
        return True
    except Exception as e:                                 # never block an install on this
        print(f"  WARNING: could not restrict access to {path}: {e}")
        return False


def main() -> int:
    """Create .env (from .env.example when present) and fill in missing generated secrets.

    Returns:
        Exit status.
    """
    if not os.path.exists(ENV):
        if os.path.exists(".env.example"):
            shutil.copyfile(".env.example", ENV)
            print("Created .env from .env.example")
        else:
            open(ENV, "a").close()
            print("Created empty .env")

    s = open(ENV, encoding="utf-8").read()
    changed = False
    for k in KEYS:
        m = re.search(rf"(?m)^{k}=(.*)$", s)
        cur = m.group(1).strip() if m else None
        if cur is None or cur == "" or PLACEHOLDER.search(cur):
            val = secrets.token_urlsafe(48)
            if m:
                s = s[:m.start()] + f"{k}={val}" + s[m.end():]
            else:
                if s and not s.endswith("\n"):
                    s += "\n"
                s += f"{k}={val}\n"
            changed = True
            print(f"  generated {k}")
    # Database password. Postgres only applies it when the data directory is first
    # created, so: fresh install -> generate a random one; existing database without a
    # DB_PASSWORD line -> stop: the operator must set the password the database uses.
    m = re.search(r"(?m)^DB_PASSWORD=(.*)$", s)
    cur = m.group(1).strip() if m else None
    if cur is None or cur == "" or PLACEHOLDER.search(cur):
        pg = os.path.join("data", "postgres")
        existing_db = os.path.isdir(pg) and bool(os.listdir(pg))
        if existing_db:
            print("  ERROR: a database already exists in data/postgres but DB_PASSWORD is not set.\n"
                  "         Set DB_PASSWORD in .env to that database's password, then re-run.")
            if changed:
                open(ENV, "w", encoding="utf-8").write(s)
            return 1
        val = secrets.token_urlsafe(32)
        line = f"DB_PASSWORD={val}"
        if m:
            s = s[:m.start()] + line + s[m.end():]
        else:
            if s and not s.endswith("\n"):
                s += "\n"
            s += line + "\n"
        changed = True
        print("  generated DB_PASSWORD")
    if changed:
        open(ENV, "w", encoding="utf-8").write(s)
    else:
        print("  secrets already set")
    if restrict_env_file(ENV):
        print("  .env access restricted to its owner")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
