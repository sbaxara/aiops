# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Metrics API Routes
Handles metrics ingestion from monitoring agents
"""
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Request
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging
from sqlalchemy.orm import Session
from axara.community.database.models import Metric, Server, MonitorConfig
from axara.community.database.repositories.metric_repo import MetricRepository
from axara.community.database.repositories.server_repo import ServerRepository
from axara.community.processing.state_machine import MonitorStateMachine
# Versioned, typed metric envelope (JSON Standardization Design, D2).
# Placed alongside this router; adjust the path if relocated to a schemas/ package.
from axara.community.api.routes.metric_schemas import MetricEnvelope
from axara.community.api.agent_auth import require_own_server, verify_agent_key
from axara.community.api.routes.auth import require_admin
from axara.community.api.guards import require_user
from axara.community.database.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])


# Request/Response Models
# The permissive MetricData model is replaced by MetricEnvelope (metric_schemas.py),
# a versioned, typed, discriminated contract. schema_version is mandatory (greenfield);
# any non-conforming metric is rejected by FastAPI with HTTP 422 at ingest.
class MetricsSubmissionRequest(BaseModel):
    """A batch of metrics from one agent (each item is validated on its own in the handler)."""
    server_id: str
    timestamp: str
    # Accept raw dicts and validate each item INSIDE the handler (per-item) so one
    # malformed metric cannot 422 the whole batch. FastAPI validating
    # List[MetricEnvelope] here would reject the entire submission if any single
    # item fails — losing all the healthy metrics with it. Per-item validation
    # below stores the good ones and reports the bad ones.
    metrics: List[Dict[str, Any]]
    is_replay: bool = False


# Dependency to get database session
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


@router.post("/")
async def submit_metrics(
    request: MetricsSubmissionRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
    db: Session = Depends(get_db),
    agent: Server = Depends(verify_agent_key),
):
    """
    Submit metrics from monitoring agent
    
    This endpoint:
    1. Validates and stores metrics in database
    2. Triggers state machine evaluation (background)
    3. Returns immediately to agent
    """
    require_own_server(request.server_id, agent)       # a key only submits for its own server
    try:
        metric_repo = MetricRepository(db)
        server_repo = ServerRepository(db)
        server = agent

        # Plugin checks (any a plugin adds; none in Community).
        from axara.community.plugins.loader import get_registry
        get_registry().run_checks("agent_intake", str(server.id))
        _note_agent_clock(http_request, server, request, server_repo)

        # Update server heartbeat
        server_repo.update(request.server_id, {
            'last_heartbeat': datetime.utcnow()
        })
        
        # Store metrics then process all in ONE background task with its own session.
        # One task per submission (not per metric) prevents concurrent tasks from
        # sharing a session — SQLAlchemy sessions are not thread-safe and sharing
        # one across threads causes identity-map corruption where all tasks see the
        # same MonitorState object, resulting in duplicate incident comments.
        stored_count = 0
        rejected = []
        metrics_for_state_machine = []
        own_monitors = {str(r[0]) for r in db.query(MonitorConfig.id)
                        .filter(MonitorConfig.server_id == agent.id).all()}
        for idx, raw_metric in enumerate(request.metrics):
            # Per-item validation: a malformed metric is skipped and reported, not
            # fatal to the batch. This is what makes one bad metric (e.g. a null
            # monitor_config_id) unable to drop the healthy metrics alongside it.
            from pydantic import ValidationError
            try:
                metric_data = MetricEnvelope(**raw_metric)
            except ValidationError as ve:
                first = ve.errors()[:1]
                rejected.append({'index': idx, 'errors': first})
                logger.warning(f"Rejected metric[{idx}] (validation): {first}")
                continue
            if metric_data.monitor_config_id and str(metric_data.monitor_config_id) not in own_monitors:
                rejected.append({'index': idx, 'errors': [{'msg': 'monitor does not belong to this agent'}]})
                logger.warning(f"Rejected metric[{idx}]: monitor {metric_data.monitor_config_id} "
                               f"is not a monitor of server {agent.id}")
                continue
            try:
                # Option 1 (screenshot): keep screenshot_b64 in the dict handed to the
                # state machine (it saves the PNG), but strip it from the DB-persisted
                # details so the base64 blob never lands in metric_data.
                persisted_details = dict(metric_data.details or {})
                persisted_details.pop('screenshot_b64', None)

                metric_repo.create({
                    'server_id': request.server_id,
                    'monitor_config_id': metric_data.monitor_config_id,
                    'metric_type': metric_data.type,
                    # Renamed field: envelope carries metric_name (was 'name').
                    'metric_name': metric_data.metric_name,
                    'metric_value': metric_data.value,   # nullable (e.g. ssl no-value)
                    'status': metric_data.status,
                    'metric_data': persisted_details,
                    # Versioned envelope metadata persisted as JSONB (D2 §2).
                    'envelope': {
                        'schema_version':  metric_data.schema_version,
                        'monitor_version': metric_data.monitor_version,
                        'unit':            metric_data.unit,
                    },
                    'timestamp': datetime.fromisoformat(request.timestamp)
                })
                stored_count += 1
                # model_dump() (pydantic v2) yields type/status/details/monitor_config_id
                # which the state machine reads; failure_reason remains inside details
                # for now (monitors still emit it there until the Step 3 monitor rollout).
                metrics_for_state_machine.append(metric_data.model_dump())
            except Exception as e:
                logger.error(f"Failed to store metric[{idx}]: {e}")

        # Observation-only metric types (added by plugins) are stored above but are not
        # health checks: they never reach the state machine, so never create incidents.
        _NON_ALERTABLE = get_registry().non_alertable_metric_types()
        metrics_for_state_machine = [
            m for m in metrics_for_state_machine
            if m.get('type') not in _NON_ALERTABLE
        ]

        if metrics_for_state_machine and not request.is_replay:
            background_tasks.add_task(
                process_all_metrics_state,
                metrics_for_state_machine
            )
        elif request.is_replay:
            logger.info(
                f"Replay batch — stored for history, skipping state machine"
            )
        
        logger.info(
            f"Stored {stored_count}/{len(request.metrics)} metrics "
            f"from server {request.server_id}"
            f"{' (replay)' if request.is_replay else ''}"
        )
        
        return {
            "status": "success",
            "metrics_received": len(request.metrics),
            "metrics_stored": stored_count,
            "metrics_rejected": len(rejected),
            "rejected": rejected,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Metrics submission error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


def _note_agent_clock(http_request: Request, server, request: MetricsSubmissionRequest, server_repo):
    """Clock evidence for plugins: the metric timestamp of a live (not replayed) batch,
    only when the agent proved its identity with its Bearer key."""
    if request.is_replay or not request.timestamp:
        return
    auth = http_request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return
    try:
        owner = server_repo.find_by_api_key(auth.split(None, 1)[1].strip())
        if owner is None or str(owner.id) != str(server.id):
            return
        from axara.community.plugins.loader import get_registry
        get_registry().emit("agent_report", str(server.id), datetime.fromisoformat(request.timestamp))
    except Exception:
        logger.debug("agent clock evidence skipped", exc_info=True)


def process_all_metrics_state(metrics: List[Dict[str, Any]]):
    """
    Process all metrics from a single submission through the state machine.
    Opens its own DB session — never shares the request session across threads.
    Sequential processing within one session means MonitorState identity-map
    lookups are always correct and isolated per monitor_config_id.
    """
    from axara.community.database import SessionLocal
    db = SessionLocal()
    try:
        state_machine = MonitorStateMachine(db)
        for metric_data in metrics:
            try:
                state_machine.process_metric_result(metric_data)
            except Exception as e:
                logger.error(f"State machine error for metric {metric_data.get('monitor_config_id')}: {e}", exc_info=True)
    finally:
        db.close()


@router.get("/recent/{server_id}")
async def get_recent_metrics(
    server_id: str,
    limit: int = 100,
    hours: Optional[int] = None,
    metric_type: Optional[str] = None,
    db: Session = Depends(get_db),
    _user: User = Depends(require_user),
):
    """
    Get recent metrics for a server.
 
    Args:
        server_id:   Server UUID
        limit:       Max rows to return — ignored when hours is provided
        hours:       When set, return all metrics within the last N hours
                     (no row cap). Use this instead of limit for the report
                     page so the time-range filter is applied server-side.
        metric_type: Filter by metric type (optional)
    """
    try:
        from datetime import timedelta
 
        query = db.query(Metric).filter(Metric.server_id == server_id)
 
        if metric_type:
            # Resolve the requested type through a plugin stand-in type, so an explicit
            # ?metric_type=network finds data while no network monitor reports yet.
            from axara.community.database.repositories.metric_repo import MetricRepository
            _res_cutoff = datetime.utcnow() - timedelta(hours=hours if hours is not None else 1)
            metric_type = MetricRepository(db).resolve_effective_metric_type(
                metric_type, _res_cutoff, server_id
            )
            query = query.filter(Metric.metric_type == metric_type)
 
        if hours is not None:
            # Time-based window — return everything in the window, no row cap
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            query  = query.filter(Metric.timestamp >= cutoff)
            metrics = query.order_by(Metric.timestamp.desc()).all()
        else:
            # Default behaviour — return most recent N rows
            metrics = query.order_by(Metric.timestamp.desc()).limit(limit).all()

        # Batched lookup of monitor names so the report can show
        # "Website Login Monitor" rather than the technical "url_response_time_ms".
        # One query on the unique monitor_config_ids — no N+1.
        mc_ids = {m.monitor_config_id for m in metrics if m.monitor_config_id}
        monitor_names: dict = {}
        if mc_ids:
            for mc_id, mc_name in (
                db.query(MonitorConfig.id, MonitorConfig.name)
                  .filter(MonitorConfig.id.in_(mc_ids))
                  .all()
            ):
                monitor_names[mc_id] = mc_name
        
        # Build flat `latest` dict — most recent value per metric type.
        # A plugin stand-in type is shown under the core type it stands in for, so the
        # ServerDetail tiles (latest['cpu'] etc.) populate either way. The most recent row
        # for a name wins (rows are newest first).
        from axara.community.plugins.loader import get_registry
        _registry = get_registry()
        latest: dict = {}
        seen: set = set()
        for m in metrics:               # already ordered by timestamp DESC
            base_type = _registry.metric_base_type(m.metric_type)
            if base_type in seen:
                continue
            seen.add(base_type)
            # Guard against None metric_value — SSL and some custom monitors
            # store all data in metric_data and leave metric_value as NULL.
            # round(None, 1) raises TypeError and crashes the whole endpoint.
            v = m.metric_value
            latest[base_type]             = round(v, 1) if v is not None else None
            latest[f"{base_type}_status"] = m.status
            # Network: pull per-second rates out of metric_data['rates'].
            if base_type == "network" and m.metric_data:
                rates = (m.metric_data or {}).get("rates", {}) or {}
                sent = rates.get("bytes_sent_per_sec", 0) or 0
                recv = rates.get("bytes_recv_per_sec", 0) or 0
                latest["network_mb"] = round((sent + recv) / 1_000_000, 2)
 
        return {
            "server_id": server_id,
            "count":     len(metrics),
            "latest":    latest,
            "metrics": [
                {
                    "id":           m.id,
                    "type":         m.metric_type,
                    "name":         m.metric_name,
                    "monitor_name": monitor_names.get(m.monitor_config_id),
                    "value":        m.metric_value,
                    "status":       m.status,
                    "timestamp":    m.timestamp.isoformat() if m.timestamp else None,
                    "details":      m.metric_data,
                }
                for m in metrics
            ],
            # history alias filtered to cpu/memory/disk for MetricsChart
            "history": [
                {
                    "timestamp":   m.timestamp.isoformat() if m.timestamp else None,
                    "metric_type": m.metric_type,
                    "value":       round(m.metric_value, 1),
                    "status":      m.status,
                }
                for m in metrics
                if m.metric_type in ("cpu", "memory", "disk")
            ],
        }
        
    except Exception as e:
        logger.error(f"Failed to get metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/timeseries/{monitor_config_id}")
async def get_metric_timeseries(
    monitor_config_id: str,
    hours: int = 24,
    db: Session = Depends(get_db),
    _user: User = Depends(require_user),
):
    """
    Get time-series data for a specific monitor
    
    Args:
        monitor_config_id: Monitor config UUID
        hours: Number of hours of history
    """
    try:
        from datetime import timedelta
        
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        
        metrics = db.query(Metric).filter(
            Metric.monitor_config_id == monitor_config_id,
            Metric.timestamp >= cutoff
        ).order_by(Metric.timestamp.asc()).all()
        
        # Format for charting
        datapoints = [
            {
                'timestamp': m.timestamp.isoformat(),
                'value': m.metric_value,
                'status': m.status
            }
            for m in metrics
        ]
        
        return {
            "monitor_config_id": monitor_config_id,
            "hours": hours,
            "datapoints": datapoints
        }
        
    except Exception as e:
        logger.error(f"Failed to get timeseries: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary/{server_id}")
async def get_server_metrics_summary(
    server_id: str,
    db: Session = Depends(get_db),
    _user: User = Depends(require_user),
):
    """
    Get current metrics summary for a server
    Returns latest value for each metric type
    
    Args:
        server_id: Server UUID
    """
    try:
        from sqlalchemy import func
        
        # Get latest metric for each type
        subquery = db.query(
            Metric.metric_type,
            func.max(Metric.timestamp).label('max_timestamp')
        ).filter(
            Metric.server_id == server_id
        ).group_by(Metric.metric_type).subquery()
        
        latest_metrics = db.query(Metric).join(
            subquery,
            (Metric.metric_type == subquery.c.metric_type) &
            (Metric.timestamp == subquery.c.max_timestamp)
        ).filter(Metric.server_id == server_id).all()
        
        summary = {}
        for metric in latest_metrics:
            summary[metric.metric_type] = {
                'value': metric.metric_value,
                'status': metric.status,
                'timestamp': metric.timestamp.isoformat() if metric.timestamp else None,
                'details': metric.metric_data
            }
        
        return {
            "server_id": server_id,
            "summary": summary
        }
        
    except Exception as e:
        logger.error(f"Failed to get summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/cleanup")
async def cleanup_old_metrics(
    days: int = 30,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """
    Clean up old metrics (admin endpoint)
    
    Args:
        days: Delete metrics older than this many days
    """
    try:
        from datetime import timedelta
        
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        deleted_count = db.query(Metric).filter(
            Metric.timestamp < cutoff
        ).delete()
        
        db.commit()
        
        logger.info(f"Deleted {deleted_count} metrics older than {days} days")
        
        return {
            "status": "success",
            "deleted_count": deleted_count,
            "cutoff_date": cutoff.isoformat()
        }
        
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))