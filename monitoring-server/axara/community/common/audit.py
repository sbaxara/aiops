# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tamper-evident audit trail.

record()        append one event (own DB session, never raises into the caller)
verify_chain()  walk the whole table and confirm the hash chain is intact
verify_rows()   pure verifier used by verify_chain() (unit-testable, no DB)
client_ip()     best-effort caller IP from a request-like object

Chain: each row's hash = SHA-256(canonical JSON of the row, which includes the
previous row's hash). The first row links to GENESIS ("0"*64). Changing,
inserting or removing any row breaks the chain from that row onward. The table
is also append-only at the DB layer (triggers in migration b8c9d0e1f2a3).

Writes are serialised across uvicorn workers with a transaction-scoped Postgres
advisory lock so two workers can't both link to the same prev_hash.

Secrets never enter the log: details are recursively redacted by key name, and
the HTTP middleware never captures request bodies.

After an event is stored it is passed to every audit sink plugins register (e.g. forwarding
to a log collector). The database remains the system of record.
"""
import hashlib
import ipaddress
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

GENESIS = "0" * 64
_LOCK_KEY = 0x61756474   # 'audt'
_SENSITIVE = ("password", "passwd", "secret", "token", "api_key", "apikey",
              "private_key", "client_secret", "authorization", "cookie",
              "samlresponse", "credential")
_HASHED_FIELDS = ("ts", "actor_type", "actor", "actor_id", "action",
                  "resource_type", "resource_id", "outcome", "source_ip",
                  "user_agent", "details", "prev_hash")


# ---------------------------------------------------------------- pure helpers

def _redact(obj: Any) -> Any:
    """Replace values of sensitive-looking keys (passwords, tokens, keys) with [REDACTED], recursively.

    Args:
        obj: Value to redact.

    Returns:
        The redacted copy.
    """
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if any(s in str(k).lower() for s in _SENSITIVE)
                    else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_redact(v) for v in obj]
    return obj


def _ts_str(ts: datetime) -> str:
    """Normalise to UTC so the hash is independent of the DB session timezone."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical(row: Dict[str, Any]) -> str:
    """Canonical JSON of the hashed fields of an audit row (stable key order).

    Args:
        row: Audit row.

    Returns:
        The JSON text that is hashed.
    """
    payload = {f: row.get(f) for f in _HASHED_FIELDS}
    payload["ts"] = _ts_str(row["ts"]) if isinstance(row.get("ts"), datetime) else row.get("ts")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      default=str, ensure_ascii=False)


def compute_hash(row: Dict[str, Any]) -> str:
    """SHA-256 of an audit row's canonical form (chained through prev_hash).

    Args:
        row: Audit row.

    Returns:
        Hex digest.
    """
    return hashlib.sha256(canonical(row).encode("utf-8")).hexdigest()


def verify_rows(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Verify an id-ordered stream of rows. The first row's prev_hash is taken as
    the anchor (GENESIS on an untouched table; allows future retention pruning)."""
    prev = None
    checked = 0
    for row in rows:
        if prev is not None and row["prev_hash"] != prev["hash"]:
            return {"ok": False, "checked": checked, "first_bad_id": row["id"],
                    "reason": "chain broken: prev_hash does not match previous row "
                              "(row inserted or removed)"}
        if compute_hash(row) != row["hash"]:
            return {"ok": False, "checked": checked, "first_bad_id": row["id"],
                    "reason": "content altered: hash does not match row contents"}
        prev = row
        checked += 1
    return {"ok": True, "checked": checked, "first_bad_id": None, "reason": None}


def _trusted_networks():
    """Networks whose X-Forwarded-For header is trusted (TRUSTED_PROXIES, default Docker bridges).

    Returns:
        List of ip_network objects; invalid entries are logged and skipped.
    """
    raw = os.getenv("TRUSTED_PROXIES", "172.16.0.0/12")   # default: Docker bridge networks
    nets = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                nets.append(ipaddress.ip_network(part, strict=False))
            except ValueError:
                logger.error(f"TRUSTED_PROXIES: ignoring invalid network {part!r}")
    return nets


def client_ip(request) -> Optional[str]:
    """Caller IP for the audit log.

    Forwarding headers are honoured ONLY when the socket peer is a trusted proxy
    (TRUSTED_PROXIES, default the Docker bridge range where the dashboard's nginx
    runs). Otherwise the socket peer is recorded — a client talking to the API
    directly could put anything in those headers.

    Behind our nginx, X-Real-IP is used (nginx overwrites it with $remote_addr).
    X-Forwarded-For is a fallback and its LAST hop is used: nginx appends with
    $proxy_add_x_forwarded_for, so earlier hops are client-supplied and spoofable."""
    try:
        peer = request.client.host if request.client else None
        trusted = False
        if peer:
            try:
                addr = ipaddress.ip_address(peer)
                trusted = any(addr in n for n in _trusted_networks())
            except ValueError:
                trusted = False
        if trusted:
            real = request.headers.get("x-real-ip")
            if real:
                return real.strip()[:64]
            xff = request.headers.get("x-forwarded-for")
            if xff:
                return xff.split(",")[-1].strip()[:64]
        return peer
    except Exception:
        return None


# ---------------------------------------------------------------- DB operations

def record(action: str, *, actor: Optional[str] = None, actor_id: Optional[str] = None,
           actor_type: str = "user", resource_type: Optional[str] = None,
           resource_id: Optional[str] = None, outcome: str = "success",
           source_ip: Optional[str] = None, user_agent: Optional[str] = None,
           details: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Append one audit event. Uses its own session so it is never rolled back by
    the caller's transaction, and never raises: an audit failure is logged as an
    error rather than breaking the user's request."""
    from sqlalchemy import text
    from axara.community.database import SessionLocal

    clean = json.loads(json.dumps(_redact(details or {}), default=str))
    row = {
        "actor_type": (actor_type or "user")[:20],
        "actor": (actor or None) and str(actor)[:255],
        "actor_id": (actor_id or None) and str(actor_id)[:64],
        "action": str(action)[:150],
        "resource_type": (resource_type or None) and str(resource_type)[:100],
        "resource_id": (resource_id or None) and str(resource_id)[:255],
        "outcome": (outcome or "success")[:20],
        "source_ip": (source_ip or None) and str(source_ip)[:64],
        "user_agent": (user_agent or None) and str(user_agent)[:300],
        "details": clean,
    }
    db = SessionLocal()
    try:
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
        last = db.execute(text("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1")).first()
        row["prev_hash"] = last[0] if last else GENESIS
        row["ts"] = datetime.now(timezone.utc)
        row["hash"] = compute_hash(row)
        new_id = db.execute(text("""
            INSERT INTO audit_log (ts, actor_type, actor, actor_id, action, resource_type,
                                   resource_id, outcome, source_ip, user_agent, details,
                                   prev_hash, hash)
            VALUES (:ts, :actor_type, :actor, :actor_id, :action, :resource_type,
                    :resource_id, :outcome, :source_ip, :user_agent,
                    CAST(:details AS jsonb), :prev_hash, :hash)
            RETURNING id
        """), {**row, "details": json.dumps(clean)}).scalar()
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"AUDIT WRITE FAILED for action={action}: {e}", exc_info=True)
        return None
    finally:
        db.close()

    try:
        from axara.community.plugins.loader import get_registry
        sinks = get_registry().audit_sinks()
    except Exception:
        sinks = []
    event = {**row, "id": new_id, "ts": _ts_str(row["ts"])}
    for sink in sinks:                               # sinks registered by plugins
        try:
            sink(event)
        except Exception:
            logger.debug("audit sink failed", exc_info=True)
    return new_id


def verify_chain(batch: int = 5000) -> Dict[str, Any]:
    """Recompute the whole chain from the database (id order, batched)."""
    from sqlalchemy import text
    from axara.community.database import SessionLocal

    cols = ("id",) + _HASHED_FIELDS + ("hash",)
    db = SessionLocal()
    try:
        def _rows():
            """Yield every audit row in id order, a batch at a time."""
            last_id = 0
            while True:
                chunk = db.execute(text(f"""
                    SELECT {', '.join(cols)} FROM audit_log
                     WHERE id > :last ORDER BY id LIMIT :n
                """), {"last": last_id, "n": batch}).fetchall()
                if not chunk:
                    return
                for r in chunk:
                    yield dict(zip(cols, r))
                last_id = chunk[-1][0]
        return verify_rows(_rows())
    finally:
        db.close()
