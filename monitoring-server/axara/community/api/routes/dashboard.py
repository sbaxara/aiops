# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Dashboard API Routes
Provides summary, server health, and time-series data for the React dashboard.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import datetime, timedelta, timezone
from typing import Optional
import logging

from axara.community.database import get_db
from axara.community.database.models import Metric, Server, Application, ApplicationServer, Incident

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# /dashboard/summary
# ---------------------------------------------------------------------------
@router.get("/summary")
def get_dashboard_summary(db: Session = Depends(get_db)):
    """KPI cards on the main Dashboard page."""
    try:
        total_servers  = db.query(func.count(Server.id)).scalar() or 0
        active_servers = db.query(func.count(Server.id)).filter(
            Server.status == "active"
        ).scalar() or 0

        open_incidents = db.query(func.count(Incident.id)).filter(
            Incident.status.in_(["open", "in_progress"])
        ).scalar() or 0

        critical_incidents = db.query(func.count(Incident.id)).filter(
            Incident.status.in_(["open", "in_progress"]),
            Incident.severity == "critical"
        ).scalar() or 0

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)

        # Use a plugin stand-in type when no monitor of the type reports yet, so the KPI
        # cards populate right after agent registration (all servers: no server_id).
        from axara.community.database.repositories.metric_repo import MetricRepository
        _metric_repo = MetricRepository(db)

        def avg_latest(metric_type: str) -> float:
            """Average of each server's latest value of a metric type in the window.

            Args:
                metric_type: Metric type (or its stand-in type when only that reports).

            Returns:
                The average, or 0 when no server reported.
            """
            effective_type = _metric_repo.resolve_effective_metric_type(metric_type, cutoff)
            subq = (
                db.query(
                    Metric.server_id,
                    func.max(Metric.timestamp).label("latest_ts")
                )
                .filter(Metric.metric_type == effective_type, Metric.timestamp >= cutoff)
                .group_by(Metric.server_id)
                .subquery()
            )
            result = db.query(func.avg(Metric.metric_value)).join(
                subq,
                (Metric.server_id == subq.c.server_id) &
                (Metric.timestamp == subq.c.latest_ts)
            ).filter(Metric.metric_type == effective_type).scalar()
            return round(float(result), 1) if result else 0.0

        return {
            "total_servers":      total_servers,
            "active_servers":     active_servers,
            "open_incidents":     open_incidents,
            "critical_incidents": critical_incidents,
            "avg_cpu":    avg_latest("cpu"),
            "avg_memory": avg_latest("memory"),
            "avg_disk":   avg_latest("disk"),
        }
    except Exception as e:
        logger.error(f"Dashboard summary error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# /dashboard/servers-health
# ---------------------------------------------------------------------------
@router.get("/servers-health")
def get_servers_health(db: Session = Depends(get_db)):
    """All servers with latest metric values and health status."""
    try:
        servers = (db.query(Server)
                   .filter((Server.status.is_(None)) | (Server.status != "decommissioned"))
                   .order_by(Server.hostname).all())
        cutoff  = datetime.now(timezone.utc) - timedelta(minutes=15)
        from axara.community.plugins.loader import get_registry
        _registry = get_registry()
        _excluded = _registry.health_summary_excluded_types()

        result = []
        for server in servers:
            # Resolve linked application via join table
            app_link = (
                db.query(ApplicationServer)
                .filter(ApplicationServer.server_id == server.id)
                .first()
            )
            application = (
                db.query(Application)
                .filter(Application.id == app_link.application_id)
                .first()
            ) if app_link else None

            rows = (
                db.query(Metric.metric_type, Metric.metric_value, Metric.status)
                .filter(Metric.server_id == server.id, Metric.timestamp >= cutoff)
                .order_by(Metric.metric_type, desc(Metric.timestamp))
                .all()
            )
            latest_metrics: dict = {}
            seen = set()
            # Plugin types that are not health signals are left out; a stand-in type is
            # shown under the type it stands in for (ServerHealthGrid reads
            # latest_metrics.cpu etc.). The first (most recent) row per name wins.
            # (round() is guarded against NULL so one null can't 500 the page.)
            for row in rows:
                if row.metric_type in _excluded:
                    continue
                base_type = _registry.metric_base_type(row.metric_type)
                if base_type not in seen:
                    seen.add(base_type)
                    latest_metrics[base_type] = {
                        "value":  round(row.metric_value, 1) if row.metric_value is not None else None,
                        "status": row.status,
                    }

            open_count = db.query(func.count(Incident.id)).filter(
                Incident.server_id == server.id,
                Incident.status.in_(["open", "in_progress"])
            ).scalar() or 0

            health = "healthy"
            if open_count > 0:
                has_critical = db.query(Incident).filter(
                    Incident.server_id == server.id,
                    Incident.status.in_(["open", "in_progress"]),
                    Incident.severity == "critical"
                ).first()
                health = "critical" if has_critical else "degraded"
            elif not latest_metrics:
                health = "unknown"

            result.append({
                "id":             server.id,
                "hostname":       server.hostname,
                "ip_address":     server.ip_address,
                "os_type":        server.os_type,
                "status":         server.status,
                "health":         health,
                "open_incidents": open_count,
                "latest_metrics": latest_metrics,
                "last_heartbeat": server.last_heartbeat.isoformat()
                                  if server.last_heartbeat else None,
                "application": {
                    "id":          application.id,
                    "name":        application.name,
                    "environment": application.environment,
                } if application else None,
            })

        return {"servers": result}
    except Exception as e:
        logger.error(f"Servers health error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# /dashboard/metrics-timeseries
# ---------------------------------------------------------------------------
@router.get("/metrics-timeseries")
def get_metrics_timeseries(
    type:      str           = Query(..., description="cpu | memory | disk | network"),
    hours:     int           = Query(24, description="Hours of history"),
    server_id: Optional[str] = Query(None, description="Filter to a single server"),
    db: Session = Depends(get_db)
):
    """
    Time-series datapoints for MetricsChart.

    For network metrics each datapoint contains separate 'sent' and 'recv'
    fields in B/s (bytes per second) sourced from metric_data.rates so the
    chart can render two lines. The frontend formats display units (B/s,
    KB/s, MB/s) based on the value's magnitude. All other types return a
    single 'value' field.
    """
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        # Use a plugin stand-in type when no monitor of the type reports yet (scoped to
        # server_id when given), so charts render right after agent registration.
        # Rendering branches on the requested `type` below; a stand-in type uses the
        # same data shape.
        from axara.community.database.repositories.metric_repo import MetricRepository
        effective_type = MetricRepository(db).resolve_effective_metric_type(
            type, cutoff, server_id
        )

        query = db.query(Metric).filter(
            Metric.metric_type == effective_type,
            Metric.timestamp   >= cutoff,
        )
        if server_id:
            query = query.filter(Metric.server_id == server_id)

        rows = query.order_by(Metric.timestamp.asc()).all()

        def _network_rates(m: Metric):
            """Extract per-second byte rates from metric_data, in B/s (canonical).

            The agent emits psutil-derived rates already in B/s; we pass them
            through unchanged. The frontend formatter chooses display units
            (B/s, KB/s, or MB/s) based on the value's magnitude.
            """
            rates = (m.metric_data or {}).get("rates", {})
            sent  = rates.get("bytes_sent_per_sec", 0) or 0
            recv  = rates.get("bytes_recv_per_sec", 0) or 0
            return round(sent, 2), round(recv, 2)

        if server_id:
            # Single server — one datapoint per row
            if type == "network":
                datapoints = []
                for m in rows:
                    sent_bps, recv_bps = _network_rates(m)
                    datapoints.append({
                        "timestamp": m.timestamp.isoformat(),
                        "sent":      sent_bps,
                        "recv":      recv_bps,
                        "status":    m.status,
                    })
            else:
                datapoints = [
                    {
                        "timestamp": m.timestamp.isoformat(),
                        "value":     round(m.metric_value, 1),
                        "status":    m.status,
                    }
                    for m in rows
                ]
        else:
            # All servers — bucket by minute and average
            from collections import defaultdict

            def worst_status(sts):
                """The worst of a set of statuses (fail, error, warning, else pass)."""
                for s in ("fail", "error", "warning"):
                    if s in sts:
                        return s
                return "pass"

            if type == "network":
                sent_buckets:   dict = defaultdict(list)
                recv_buckets:   dict = defaultdict(list)
                status_buckets: dict = defaultdict(list)
                for m in rows:
                    bucket = m.timestamp.replace(second=0, microsecond=0).isoformat()
                    s, r = _network_rates(m)
                    sent_buckets[bucket].append(s)
                    recv_buckets[bucket].append(r)
                    status_buckets[bucket].append(m.status)

                datapoints = [
                    {
                        "timestamp": bucket,
                        "sent":  round(sum(sent_buckets[bucket]) / len(sent_buckets[bucket]), 2),
                        "recv":  round(sum(recv_buckets[bucket]) / len(recv_buckets[bucket]), 2),
                        "status": worst_status(status_buckets[bucket]),
                    }
                    for bucket in sorted(sent_buckets)
                ]
            else:
                val_buckets:    dict = defaultdict(list)
                status_buckets: dict = defaultdict(list)
                for m in rows:
                    bucket = m.timestamp.replace(second=0, microsecond=0).isoformat()
                    val_buckets[bucket].append(m.metric_value)
                    status_buckets[bucket].append(m.status)

                datapoints = [
                    {
                        "timestamp": bucket,
                        "value":     round(sum(vals) / len(vals), 1),
                        "status":    worst_status(status_buckets[bucket]),
                    }
                    for bucket, vals in sorted(val_buckets.items())
                ]

        return {
            "metric_type": type,
            "server_id":   server_id,
            "hours":       hours,
            "datapoints":  datapoints,
        }

    except Exception as e:
        logger.error(f"Metrics timeseries error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))