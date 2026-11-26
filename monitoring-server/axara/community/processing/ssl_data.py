# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""SSL certificate scan results for the SSL page (and any plugin reports that use them).

Reads the stored `ssl` metrics (one row per scan by the agent's ssl monitor) and summarises
the latest scan per server, keeping the scan history for the page.
"""
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from axara.community.database.models import Metric, Server


def get_ssl_data(db: Session, servers: List[Server],
                 start: datetime = None, end: datetime = None) -> Dict[str, Any]:
    """
    Fetch the latest SSL scan per server.
    Returns summary KPIs + per-server certificate lists.
    """
    if not servers:
        return {"summary": {}, "servers": []}

    # Default to all-time if no period supplied (e.g. called from API endpoint)
    if start is None:
        start = datetime(2000, 1, 1)
    if end is None:
        end = datetime.utcnow()

    server_ids = [s.id for s in servers]
    id_to_server = {str(s.id): s for s in servers}

    # All SSL scans within the report period — every scan is preserved as a
    # Metric row so history is already in the DB; we fetch all of them here.
    # Results are ordered newest-first per server so the dashboard/report
    # can show the latest scan prominently while also surfacing history.
    ssl_metrics = (
        db.query(Metric)
        .filter(
            Metric.server_id.in_(server_ids),
            Metric.metric_type == "ssl",
            Metric.timestamp >= start,
            Metric.timestamp <= end,
        )
        .order_by(Metric.server_id, Metric.timestamp.desc())
        .all()
    )

    # KPI totals (computed from the LATEST scan per server only to avoid
    # double-counting across historical scans)
    total_scanned = total_ssl = expired = critical = warning = healthy = 0
    weak_protocol = handshake_failed = 0
    ssl_servers = []

    # Group all scan metrics by server, preserving newest-first order
    from collections import defaultdict
    scans_by_server: Dict[str, list] = defaultdict(list)
    for m in ssl_metrics:
        scans_by_server[str(m.server_id)].append(m)

    for server_id_str, scans in scans_by_server.items():
        srv = id_to_server.get(server_id_str)

        # Use the latest scan for KPI totals
        latest_scan = scans[0]  # already ordered desc
        latest_data = latest_scan.metric_data or {}
        latest_certs = latest_data.get("certificates", [])

        total_scanned += latest_data.get("scanned_ports", 0)
        ssl_ports      = latest_data.get("ssl_ports", 0)
        total_ssl     += ssl_ports

        latest_summary = {
            "scanned_ports": latest_data.get("scanned_ports", 0),
            "ssl_ports":     ssl_ports,
            "no_ssl_ports":  sum(1 for c in latest_certs if not c.get("ssl")),
            "expired": 0, "critical": 0, "warning": 0,
            "healthy": 0, "weak_protocol": 0, "handshake_failed": 0,
        }
        for cert in latest_certs:
            if not cert.get("ssl"):
                continue
            if cert.get("error"):
                latest_summary["handshake_failed"] += 1; handshake_failed += 1
                continue
            days = cert.get("days_remaining")
            if days is not None:
                if days < 0:
                    latest_summary["expired"] += 1;  expired += 1
                elif days <= 7:
                    latest_summary["critical"] += 1; critical += 1
                elif days <= 30:
                    latest_summary["warning"] += 1;  warning += 1
                else:
                    latest_summary["healthy"] += 1;  healthy += 1
            if cert.get("protocol_status") == "fail":
                latest_summary["weak_protocol"] += 1; weak_protocol += 1

        # Build scan history entries (one per historical scan)
        scan_history = []
        for scan in scans:
            d = scan.metric_data or {}
            scan_history.append({
                "scanned_at":     scan.timestamp.isoformat() if scan.timestamp else None,
                "overall_status": scan.status,
                "certificates":   d.get("certificates", []),
                "summary": {
                    "scanned_ports": d.get("scanned_ports", 0),
                    "ssl_ports":     d.get("ssl_ports", 0),
                },
            })

        server_entry = {
            "server_id":      server_id_str,
            "hostname":       srv.hostname if srv else server_id_str,
            "ip_address":     srv.ip_address if srv else "",
            "scanned_at":     latest_scan.timestamp.isoformat() if latest_scan.timestamp else None,
            "overall_status": latest_scan.status,
            "certificates":   latest_certs,   # latest scan certs for dashboard
            "summary":        latest_summary,
            "scan_history":   scan_history,   # all historical scans
        }
        ssl_servers.append(server_entry)

    # Servers with no SSL data
    scanned_server_ids = set(scans_by_server.keys())
    for srv in servers:
        if str(srv.id) not in scanned_server_ids:
            ssl_servers.append({
                "server_id":    str(srv.id),
                "hostname":     srv.hostname,
                "ip_address":   srv.ip_address or "",
                "scanned_at":   None,
                "overall_status": None,
                "certificates": [],
                "summary":      None,
                "note":         "No SSL scan data",
            })

    return {
        "summary": {
            "total_ports_scanned": total_scanned,
            "ssl_ports":           total_ssl,
            "expired":             expired,
            "critical":            critical,
            "warning":             warning,
            "healthy":             healthy,
            "weak_protocol":       weak_protocol,
            "handshake_failed":    handshake_failed,
        },
        "servers": sorted(ssl_servers, key=lambda x: x["hostname"]),
    }
