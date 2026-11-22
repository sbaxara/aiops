# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Incidents API Routes
Full CRUD + resolution + comments for the dashboard and external consumers.
"""
from fastapi import APIRouter, HTTPException, Depends, Query, BackgroundTasks, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc
from typing import Optional, List, Any, Dict
from datetime import datetime
import logging

from axara.community.database import SessionLocal
from axara.community.database.models import Incident, IncidentComment, Application, Server
from axara.community.database.repositories.incident_repo import IncidentRepository
from axara.community.api.guards import require_function

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])


# DB dependency
def get_db():
    """FastAPI dependency: a session closed after the request.

    Yields:
        SQLAlchemy session.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Pydantic schemas
class CommentOut(BaseModel):
    """An incident comment in API responses."""
    id: str
    comment_type: Optional[str]
    content: Optional[str]
    created_by: Optional[str]
    created_at: Optional[datetime]

    class Config:
        """Pydantic settings: read attributes from ORM objects."""
        from_attributes = True


class IncidentOut(BaseModel):
    """An incident in API responses (core fields; plugins add theirs through the registry)."""
    id: str
    incident_key: Optional[str]
    title: Optional[str]
    description: Optional[str]
    severity: Optional[str]
    status: Optional[str]
    assignee: Optional[str]
    llm_analysis: Optional[str]
    similar_incidents: Optional[Any]
    ticket_provider: Optional[str]
    ticket_ref: Optional[str]
    monitor_config_id: Optional[str]
    server_id: Optional[str]
    application_id: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    resolved_at: Optional[datetime]
    # Denormalised from relationships
    application_name: Optional[str] = None
    server_hostname: Optional[str] = None
    comments: List[CommentOut] = []

    class Config:
        """Pydantic settings: read attributes from ORM objects."""
        from_attributes = True


class IncidentCreate(BaseModel):
    """Request to create an incident (deduplicated on incident_key while open)."""
    incident_key: str
    monitor_config_id: Optional[str] = None
    server_id: Optional[str] = None
    application_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    severity: str = "medium"
    assignee: Optional[str] = None


class IncidentUpdate(BaseModel):
    """Fields an operator may change on an incident."""
    status: Optional[str] = None
    assignee: Optional[str] = None
    severity: Optional[str] = None


class ResolveRequest(BaseModel):
    """Request to resolve an incident, with optional resolution notes."""
    resolution_notes: Optional[str] = None


class CommentCreate(BaseModel):
    """Request to add a comment to an incident."""
    content: str
    comment_type: str = "manual"
    created_by: Optional[str] = "dashboard"


# Helper
def _enrich(incident: Incident, db: Session, *, extra: Optional[Dict[str, Any]] = None,
            detail: bool = False) -> Dict:
    """Incident as the dashboard expects it.

    Adds the application and server names, the comments, the ticket link (ticket_url,
    from the provider that opened the ticket) and the fields plugins provide through the
    registry (e.g. Enterprise correlation and escalation fields).

    Args:
        incident: Incident row with application, server (and comments) loaded.
        db: SQLAlchemy session.
        extra: Plugin fields already collected for this incident (list view batches them);
            collected here when None.
        detail: True for the single-incident view (plugins may add costlier fields).

    Returns:
        JSON-serialisable dict.
    """
    from axara.community.plugins.loader import get_registry
    reg = get_registry()
    d = {c.name: getattr(incident, c.name) for c in incident.__table__.columns}
    d["application_name"] = incident.application.name if incident.application else None
    d["server_hostname"]  = incident.server.hostname  if incident.server  else None
    # Serialize each comment to a plain dict — ORM objects cannot be JSON-serialised
    d["attachment"] = incident.attachment
    d["comments"] = [
        {
            "id":           c.id,
            "comment_type": c.comment_type,
            "content":      c.content,
            "created_by":   c.created_by,
            "created_at":   c.created_at.isoformat() if c.created_at else None,
        }
        for c in (incident.comments or [])
    ]
    provider = reg.ticket_provider(incident.ticket_provider) if incident.ticket_ref else None
    d["ticket_url"] = provider.ticket_url(incident.ticket_ref) if provider else None
    if extra is None:
        extra = reg.incident_fields(db, [incident.id], detail=detail).get(incident.id, {})
    for k, v in extra.items():
        d.setdefault(k, v)                      # plugin fields never overwrite core columns
    return d

def _load(incident_id: str, db: Session) -> Incident:
    """Load incident with all relationships or raise 404."""
    incident = (
        db.query(Incident)
        .options(
            joinedload(Incident.application),
            joinedload(Incident.server),
            joinedload(Incident.comments),
        )
        .filter(Incident.id == incident_id)
        .first()
    )
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


# Routes
@router.get("/", response_model=Dict[str, Any])
def list_incidents(
    status:         Optional[str] = Query(None, description="open|in_progress|resolved|closed"),
    severity:       Optional[str] = Query(None),
    application_id: Optional[str] = Query(None),
    server_id:      Optional[str] = Query(None),
    limit:          int           = Query(50, ge=1, le=500),
    offset:         int           = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    List incidents with optional filters.
    Returns { incidents, total, limit, offset }.
    """
    try:
        q = (
            db.query(Incident)
            .options(joinedload(Incident.application), joinedload(Incident.server))
        )

        if status:
            # Support comma-separated: ?status=open,in_progress
            statuses = [s.strip() for s in status.split(",")]
            q = q.filter(Incident.status.in_(statuses))
        if severity:
            q = q.filter(Incident.severity == severity)
        if application_id:
            q = q.filter(Incident.application_id == application_id)
        if server_id:
            q = q.filter(Incident.server_id == server_id)

        total = q.count()

        # Fetch paginated IDs first, then load full objects with joins.
        # Using joinedload directly with limit() causes SQLAlchemy to apply
        # LIMIT to the joined cartesian product rather than to the parent rows,
        # returning all rows instead of respecting the limit.
        # scalar_subquery() produces a proper select() construct for use in
        # IN() — subquery() produces a Subquery object which SQLAlchemy 1.4+
        # warns about when coercing into IN().
        id_subq = (
            q.order_by(desc(Incident.created_at))
            .offset(offset)
            .limit(limit)
            .with_entities(Incident.id)
            .scalar_subquery()
        )
        incidents = (
            db.query(Incident)
            .options(
                joinedload(Incident.application),
                joinedload(Incident.server),
            )
            .filter(Incident.id.in_(id_subq))
            .order_by(desc(Incident.created_at))
            .all()
        )

        # Plugin fields for the whole page in one call per provider (not one per row).
        from axara.community.plugins.loader import get_registry
        extra = get_registry().incident_fields(db, [i.id for i in incidents])

        return {
            "incidents": [_enrich(i, db, extra=extra.get(i.id, {})) for i in incidents],
            "total":     total,
            "limit":     limit,
            "offset":    offset,
        }
    except Exception as e:
        logger.error(f"list_incidents error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{incident_id}", response_model=Dict[str, Any])
def get_incident(incident_id: str, db: Session = Depends(get_db)):
    """Get a single incident with full details, analysis, and comments."""
    try:
        return _enrich(_load(incident_id, db), db, detail=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_incident error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/", response_model=Dict[str, Any], status_code=201,
             dependencies=[Depends(require_function("incidents"))])
def create_incident(
    body: IncidentCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Create a new incident and trigger the orchestration pipeline
    (LLM analysis, ITSM ticket creation, notifications) in the background.
    """
    try:
        # Deduplicate — if an open incident with this key already exists, return it
        existing = (
            db.query(Incident)
            .filter(
                Incident.incident_key == body.incident_key,
                Incident.status.in_(["open", "in_progress"]),
            )
            .first()
        )
        if existing:
            logger.info(f"Deduplication hit for key '{body.incident_key}', returning {existing.id}")
            return _enrich(existing, db)

        incident = Incident(
            incident_key       = body.incident_key,
            monitor_config_id  = body.monitor_config_id,
            server_id          = body.server_id,
            application_id     = body.application_id,
            title              = body.title,
            description        = body.description,
            severity           = body.severity,
            status             = "open",
            assignee           = body.assignee,
            created_at         = datetime.utcnow(),
        )
        db.add(incident)
        db.commit()
        db.refresh(incident)

        logger.info(f"Created incident {incident.id}: {incident.title}")

        # Trigger orchestration pipeline asynchronously
        background_tasks.add_task(
            _run_orchestrator, incident.id, body.monitor_config_id
        )

        return _enrich(_load(incident.id, db), db)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create_incident error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{incident_id}", response_model=Dict[str, Any])
def update_incident(
    incident_id: str,
    body: IncidentUpdate,
    db: Session = Depends(get_db),
):
    """Update incident status, assignee or severity."""
    try:
        incident = _load(incident_id, db)
        updates  = {k: v for k, v in body.model_dump().items() if v is not None}

        if not updates:
            return _enrich(incident, db)

        updates["updated_at"] = datetime.utcnow()
        for k, v in updates.items():
            setattr(incident, k, v)
        db.commit()
        db.refresh(incident)

        logger.info(f"Updated incident {incident_id}: {updates}")
        return _enrich(incident, db)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update_incident error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{incident_id}/resolve", response_model=Dict[str, Any])
def resolve_incident(
    incident_id: str,
    body: ResolveRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Mark an incident as resolved and trigger ITSM resolution + RAG storage.
    """
    try:
        incident = _load(incident_id, db)

        if incident.status == "resolved":
            return _enrich(incident, db)

        incident.status      = "resolved"
        incident.resolved_at = datetime.utcnow()
        incident.updated_at  = datetime.utcnow()
        if body.resolution_notes and body.resolution_notes.strip():
            incident.resolution_notes = body.resolution_notes.strip()
        db.commit()
        db.refresh(incident)

        logger.info(f"Resolved incident {incident_id}")

        background_tasks.add_task(
            _run_resolution, incident_id, body.resolution_notes
        )

        return _enrich(_load(incident_id, db), db)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"resolve_incident error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{incident_id}/comments", response_model=List[Dict[str, Any]])
def list_comments(incident_id: str, db: Session = Depends(get_db)):
    """List all comments for an incident."""
    try:
        _load(incident_id, db)  # validates existence
        comments = (
            db.query(IncidentComment)
            .filter(IncidentComment.incident_id == incident_id)
            .order_by(IncidentComment.created_at.asc())
            .all()
        )
        return [
            {
                "id":           c.id,
                "comment_type": c.comment_type,
                "content":      c.content,
                "created_by":   c.created_by,
                "created_at":   c.created_at,
            }
            for c in comments
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"list_comments error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{incident_id}/comments", response_model=Dict[str, Any], status_code=201)
def add_comment(
    incident_id: str,
    body: CommentCreate,
    db: Session = Depends(get_db),
):
    """Add a manual comment to an incident."""
    try:
        _load(incident_id, db)  # validates existence

        comment = IncidentComment(
            incident_id  = incident_id,
            comment_type = body.comment_type,
            content      = body.content,
            created_by   = body.created_by,
            created_at   = datetime.utcnow(),
        )
        db.add(comment)
        db.commit()
        db.refresh(comment)

        logger.info(f"Added {body.comment_type} comment to incident {incident_id}")
        return {
            "id":           comment.id,
            "comment_type": comment.comment_type,
            "content":      comment.content,
            "created_by":   comment.created_by,
            "created_at":   comment.created_at,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"add_comment error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{incident_id}/attachments", response_model=Dict[str, Any], status_code=201)
async def upload_attachment(
    incident_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Upload a file and attach it to an incident.

    Saves the file to the ATTACHMENTS_DIR volume, updates
    incident.attachment with the absolute path, and returns
    the updated incident.
    """
    import os
    try:
        incident = _load(incident_id, db)

        attachments_dir = os.getenv('ATTACHMENTS_DIR', '/app/attachments')
        os.makedirs(attachments_dir, exist_ok=True)

        # Sanitise filename — keep only safe characters
        safe_name = os.path.basename(file.filename or 'attachment.png')
        safe_name = safe_name.replace(' ', '_')
        file_path = os.path.join(attachments_dir, safe_name)

        contents = await file.read()
        with open(file_path, 'wb') as fh:
            fh.write(contents)

        incident.attachment = file_path
        incident.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(incident)

        logger.info(f'Attached {safe_name} to incident {incident_id}')
        return _enrich(_load(incident_id, db), db)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'upload_attachment error: {e}', exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{incident_id}/attachments/download")
def download_attachment(incident_id: str, db: Session = Depends(get_db)):
    """
    Stream the incident's attached PNG file to the browser.
    Used by the Incident Detail page download link.
    """
    import os
    try:
        incident = _load(incident_id, db)
        if not incident.attachment:
            raise HTTPException(status_code=404, detail='No attachment on this incident')
        if not os.path.isfile(incident.attachment):
            raise HTTPException(status_code=404, detail='Attachment file not found on disk')
        filename = os.path.basename(incident.attachment)
        return FileResponse(
            path=incident.attachment,
            media_type='image/png',
            filename=filename,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'download_attachment error: {e}', exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# Background task helpers
def _run_orchestrator(incident_id: str, monitor_config_id: Optional[str]):
    """Run in background thread — opens its own DB session."""
    from axara.community.database import SessionLocal
    from axara.community.incident_management.orchestrator import IncidentOrchestrator
    db = SessionLocal()
    try:
        IncidentOrchestrator(db).process_incident(incident_id, monitor_config_id)
    except Exception as e:
        logger.error(f"Background orchestrator failed for {incident_id}: {e}", exc_info=True)
    finally:
        db.close()


def _run_resolution(incident_id: str, resolution_notes: Optional[str]):
    """Run in background thread — opens its own DB session."""
    from axara.community.database import SessionLocal
    from axara.community.incident_management.orchestrator import IncidentOrchestrator
    db = SessionLocal()
    try:
        IncidentOrchestrator(db).process_resolution(incident_id, resolution_notes)
    except Exception as e:
        logger.error(f"Background resolution failed for {incident_id}: {e}", exc_info=True)
    finally:
        db.close()