#!/usr/bin/env python3
# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Server admin commands (works with the compiled production image and a source checkout).

  python manage.py migrate             create or upgrade the database schema (core + plugins)
  python manage.py seed-admin          create/reset the first admin (ADMIN_* env vars)
  python manage.py rotate-master-key   re-encrypt stored secrets under MASTER_ENCRYPTION_KEY
  python manage.py rag-reindex         rebuild the similar-incident store from resolved incidents
  python manage.py make-cert           create a self-signed TLS certificate (CERT_DIR, CERT_HOSTS)

Installed plugins may add their own commands; `python manage.py help` lists everything
available.
Compiled modules cannot be run with `python -m package.module`, hence this entry point.
"""
import sys

CORE = {
    "migrate": ("axara.community.database.migrate", "create or upgrade the database schema"),
    "seed-admin": ("axara.community.database.seed_admin", "create/reset the first admin"),
    "rotate-master-key": ("axara.community.database.rotate_master_key", "re-encrypt stored secrets"),
    "rag-reindex": ("axara.community.incident_management.rag_reindex", "rebuild the similar-incident store"),
    "make-cert": ("axara.community.common.selfsigned", "create a self-signed TLS certificate (CERT_DIR, CERT_HOSTS)"),
}


def _plugin_commands():
    """Commands registered by installed plugins.

    Returns:
        {name: registry entry}; empty when the registry cannot be built (e.g. no SECRET_KEY).
    """
    try:
        from axara.community.plugins.loader import get_registry
        return get_registry().commands()
    except Exception as e:                       # still offer the core commands
        print(f"(plugin commands unavailable: {e})", file=sys.stderr)
        return {}


def main(argv):
    """Run one admin command.

    Args:
        argv: sys.argv (argv[1] is the command).

    Returns:
        Process exit status.
    """
    cmd = argv[1] if len(argv) > 1 else "help"
    if cmd in CORE:
        import importlib
        return importlib.import_module(CORE[cmd][0]).main()
    plugin = _plugin_commands() if cmd not in ("help", "-h", "--help") else {}
    if cmd in plugin:
        return plugin[cmd].item(list(argv[2:]))
    print(__doc__)
    extra = _plugin_commands() if not plugin else plugin
    if extra:
        print("Plugin commands:")
        for name, e in sorted(extra.items()):
            print(f"  python manage.py {name:<18} {e.options.get('help', '')}")
    return 0 if cmd in ("help", "-h", "--help") else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
