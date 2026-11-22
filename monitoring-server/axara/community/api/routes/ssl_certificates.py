# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
SSL Certificates API Route
Provides a dedicated endpoint for the SSL certificate dashboard report.
Reads from the most recent ssl metric stored by the SSL monitor agent.
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from typing import Optional
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ssl-certificates", tags=["ssl-certificates"])


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


@router.get("/{server_id}")
async def get_ssl_certificates(
    server_id: str,
    db: Session = Depends(get_db)
):
    """
    Return the most recent SSL scan result for a server.

    Reads the latest 'ssl' type metric row from the metrics table and
    unpacks the metric_data JSON into a structured report response.
    """
    try:
        from axara.community.database.models import Metric, Server
        from axara.community.database.repositories.server_repo import ServerRepository

        # Verify server exists
        server_repo = ServerRepository(db)
        server = server_repo.find_by_id(server_id)
        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

        # Fetch most recent ssl metric for this server
        latest = (
            db.query(Metric)
            .filter(
                Metric.server_id   == server_id,
                Metric.metric_type == 'ssl',
            )
            .order_by(Metric.timestamp.desc())
            .first()
        )

        if not latest or not latest.metric_data:
            return {
                "server_id":    server_id,
                "hostname":     server.hostname,
                "scanned_at":   None,
                "certificates": [],
                "summary": {
                    "total_ports_scanned": 0,
                    "ssl_ports":           0,
                    "no_ssl_ports":        0,
                    "expired":             0,
                    "critical":            0,
                    "warning":             0,
                    "healthy":             0,
                    "handshake_failed":    0,
                    "weak_protocol":       0,
                },
                "message": "No SSL scan data available for this server yet."
            }

        data         = latest.metric_data
        certificates = data.get('certificates', [])

        # Build summary counts
        summary = {
            "total_ports_scanned": data.get('scanned_ports', 0),
            "ssl_ports":           data.get('ssl_ports',     0),
            "no_ssl_ports":        sum(1 for c in certificates if not c.get('ssl')),
            "expired":             0,
            "critical":            0,
            "warning":             0,
            "healthy":             0,
            "handshake_failed":    0,
            "weak_protocol":       0,
        }

        now = datetime.utcnow()
        critical_threshold = 7    # defaults — not stored in metric, use sensible values
        warning_threshold  = 30

        # Read thresholds from the metric_data if stored (future-proof)
        # Currently not stored but can be added to ssl_monitor later
        for cert in certificates:
            if not cert.get('ssl'):
                continue
            if cert.get('error') and not cert.get('days_remaining'):
                summary['handshake_failed'] += 1
                continue
            days = cert.get('days_remaining')
            if days is not None:
                if days < 0:
                    summary['expired'] += 1
                elif days < critical_threshold:
                    summary['critical'] += 1
                elif days < warning_threshold:
                    summary['warning'] += 1
                else:
                    summary['healthy'] += 1
            if cert.get('protocol_status') == 'fail':
                summary['weak_protocol'] += 1

        return {
            "server_id":       server_id,
            "hostname":        server.hostname,
            "ip_address":      server.ip_address,
            "scanned_at":      data.get('scanned_at'),
            "min_tls_version": data.get('min_tls_version'),
            "overall_status":  latest.status,
            "certificates":    certificates,
            "summary":         summary,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"SSL certificates fetch failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/")
async def get_all_ssl_certificates(
    db: Session = Depends(get_db),
    days: int = 0,       # 0 = latest scan only; >0 = history for past N days
):
    """
    Return SSL scan results for all active servers.
    Pass ?days=30 to include scan history for the past 30 days.
    Used by the dashboard SSL report page to fetch all servers in one call.
    """
    try:
        from axara.community.database.models import Server
        from axara.community.processing.ssl_data import get_ssl_data

        servers = db.query(Server).filter(Server.status == 'active').all()

        # Compute time window
        end   = datetime.utcnow()
        start = (end - timedelta(days=days)) if days > 0 else datetime(2000, 1, 1)

        ssl_data = get_ssl_data(db, servers, start=start, end=end)
        return {
            "servers": ssl_data["servers"],
            "count":   len(ssl_data["servers"]),
        }

    except Exception as e:
        logger.error(f"SSL all-servers fetch failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
