# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Incident orchestrator: ticketing, LLM analysis, incident memory and notifications.

For each new incident it opens a ticket with the application's ticket provider, sends
notifications through the registered notifiers, finds similar resolved incidents (RAG),
runs the LLM analysis and adds it to the incident and the ticket. On resolution it resolves
the ticket and stores the incident in incident memory. Ticket providers, notifiers and the
licence gates come from the plugin registry (plugins.loader), so the core imports no
ticketing or notification product directly.
"""
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from axara.community.database.repositories.incident_repo import IncidentRepository
from axara.community.database.repositories.metric_repo import MetricRepository
from axara.community.incident_management.llm_analyzer import LLMAnalyzer
from axara.community.incident_management.rag_engine import RAGEngine
from axara.community.database.models import IncidentComment
from axara.community.plugins.loader import get_registry

logger = logging.getLogger(__name__)


class IncidentOrchestrator:
    """
    Orchestrates the complete incident management workflow:
    1. Create incident
    2. Retrieve similar incidents (RAG)
    3. Perform LLM analysis
    4. Create tickets (the application's ticket provider)
    5. Send notifications (registered notifiers)
    6. Store for future RAG
    """
    
    def __init__(self, db: Session):
        """
        Initialize incident orchestrator
        
        Args:
            db: Database session
        """
        self.db = db
        self.incident_repo = IncidentRepository(db)
        self.metric_repo = MetricRepository(db)
        
        # All components lazy — initialised on first use
        self._llm_analyzer = None
        self._rag_engine = None
        self.registry = get_registry()

    @property
    def llm_analyzer(self):
        """The LLM analyzer, created on first use from the current settings.

        Returns:
            LLMAnalyzer.
        """
        if self._llm_analyzer is None:
            # Late import avoids circular dependency.
            # Reads live values from _server_config so UI changes take effect
            # on the next incident — no restart required.
            from axara.community.api.routes.config import _server_config
            llm_cfg = _server_config.get('llm', {})
            self._llm_analyzer = LLMAnalyzer(
                ollama_url=llm_cfg.get('base_url'),
                model=llm_cfg.get('model', 'gemma4:26b'),
                request_timeout=llm_cfg.get('timeout', 120),
            )
        return self._llm_analyzer

    @property
    def rag_engine(self):
        """The incident-memory engine, created and initialised on first use.

        Returns:
            RAGEngine.
        """
        if self._rag_engine is None:
            from axara.community.api.routes.config import _server_config
            rag_cfg = _server_config.get('rag', {})
            self._rag_engine = RAGEngine(
                top_k=rag_cfg.get('top_k', 5),
                similarity_threshold=rag_cfg.get('similarity_threshold', 0.7),
            )
            self._rag_engine.initialize()
        return self._rag_engine

    def _get_itsm_provider(self, incident) -> str:
        """
        Determine which ITSM tool to use for this incident.

        Args:
            incident: Incident row.

        Returns:
            Application.itsm_provider (set on the server per application), else 'jira'.
        """
        if incident.application and incident.application.itsm_provider:
            return incident.application.itsm_provider.lower()
        return "jira"   # default

    def _ticketing(self, incident):
        """The incident's active ticket provider and its ticket reference.

        Args:
            incident: Incident row.

        An incident that already has a ticket stays with the tool that opened it, even if the
        application's ticketing setting changed since.

        Returns:
            (provider, ticket) — provider is None when the tool is unknown or not licensed;
            ticket is None when no ticket was opened yet.
        """
        provider = self.registry.ticket_provider(incident.ticket_provider or self._get_itsm_provider(incident))
        if provider is None:
            return None, None
        ticket = incident.ticket_ref if incident.ticket_provider == provider.name else None
        return provider, ticket
        
    def process_incident(
        self,
        incident_id: str,
        monitor_config_id: str = None,
    ) -> bool:
        """
        Process a newly created incident through the complete workflow.
    
        IMPORTANT: This method runs in a background thread with its own
        DB session (spawned by state_machine._trigger_orchestrator).
    
        Args:
            incident_id: Incident ID (already created in incidents table)
            monitor_config_id: Monitor config ID (for recent metrics context)
    
        Returns:
            True if processing completed successfully
        """
        try:
            # Get incident (already created by state_machine._create_incident)
            incident = self.incident_repo.find_by_id(incident_id)
            if not incident:
                logger.error(f"Incident {incident_id} not found")
                return False
    
            logger.info(f"Processing incident {incident_id}: {incident.title}")
    
            # Phase 1: Immediate actions (ticket + notification)
    
            # Step 1: Create external tickets FIRST (no LLM wait)
            self._create_external_tickets(incident)
    
            # Step 2: Send initial notification (incident opened)
            self._send_notifications(incident, event_type='opened')

            # Phase 2: LLM analysis (can take 30-120s) - off in licence monitoring-only/blocked
            if not self.registry.function_allowed("analysis"):
                logger.info(f"Incident {incident_id}: analysis skipped (licence restricted)")
                return True
    
            # Step 4: Get recent metrics for LLM context
            recent_metrics = []
            if monitor_config_id:
                recent_metrics = self._get_recent_metrics(monitor_config_id)
    
            # Step 5: RAG — find similar historical incidents
            similar_incidents = self._find_similar_incidents(incident)
    
            # Step 6: LLM analysis
            analysis = self._perform_llm_analysis(
                incident, recent_metrics, similar_incidents
            )
    
            # Step 7: Save LLM analysis to incidents table AND incident_comments
            self._update_incident_with_analysis(incident, analysis, similar_incidents)
    
            # Step 8: Add LLM analysis as comment to Jira/ServiceNow
            self._add_llm_comment(incident, analysis)
    
            # Step 9: Send follow-up notification (analysis available)
            self._send_notifications(incident, event_type='analysis_ready')
    
            logger.info(f"Incident {incident_id} processing complete")
            return True
    
        except Exception as e:
            logger.error(
                f"Error processing incident {incident_id}: {e}",
                exc_info=True,
            )
            return False
    
    def process_resolution(self, incident_id: str, resolution_notes: str = None) -> bool:
        """
        Process incident resolution — resolves in the correct ITSM tool only.
        """
        try:
            incident = self.incident_repo.find_by_id(incident_id)
            if not incident:
                logger.error(f"Incident {incident_id} not found")
                return False

            logger.info(f"Processing resolution for incident {incident_id}")
            notes    = resolution_notes or "Auto-resolved by monitoring system"

            provider, ticket = self._ticketing(incident)
            if provider is None:
                logger.info(f"Ticket resolve skipped for incident {incident_id}: "
                            f"no active ticket provider '{self._get_itsm_provider(incident)}'")
            elif ticket:
                provider.resolve(ticket, notes)

            # Send resolution notifications
            self._send_resolution_notifications(incident)

            # Keep the operator's resolution notes with the incident (RAG and reindex use them)
            if resolution_notes and not incident.resolution_notes:
                self.incident_repo.update(incident.id, {'resolution_notes': resolution_notes})
                incident = self.incident_repo.find_by_id(incident_id) or incident

            # Store in RAG for future similarity search (part of incident analysis)
            self._store_in_rag(incident, resolution_notes=resolution_notes)

            logger.info(f"Successfully processed resolution for incident {incident_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to process resolution for {incident_id}: {e}")
            return False
    
    def process_recheck_comment(self, incident_id: str) -> bool:
        """
        Post the most recent DB comment (written by state_machine) to the
        correct ITSM tool for an existing open incident.
        """
        try:
            incident = self.incident_repo.find_by_id(incident_id)
            if not incident:
                return False

            # Fetch the latest system comment written by state_machine
            comment = (
                self.db.query(IncidentComment)
                .filter(
                    IncidentComment.incident_id  == incident_id,
                    IncidentComment.comment_type == "system",
                    IncidentComment.created_by   == "state_machine",
                )
                .order_by(IncidentComment.created_at.desc())
                .first()
            )
            if not comment:
                return False

            provider, ticket = self._ticketing(incident)
            if provider is None or not ticket:
                return False
            provider.add_comment(ticket, comment.content)

            logger.info(f"Posted recheck comment to {provider.name} for incident {incident_id}")
            return True

        except Exception as e:
            logger.error(f"process_recheck_comment failed for {incident_id}: {e}", exc_info=True)
            return False
    
    def _get_recent_metrics(
        self,
        monitor_config_id: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get recent metrics for context
        
        Args:
            monitor_config_id: Monitor config ID
            limit: Number of recent metrics to retrieve
            
        Returns:
            List of metric dicts
        """
        try:
            from axara.community.database.models import Metric
            from datetime import datetime, timedelta
            
            cutoff = datetime.utcnow() - timedelta(hours=1)
            
            metrics = self.db.query(Metric).filter(
                Metric.monitor_config_id == monitor_config_id,
                Metric.timestamp >= cutoff
            ).order_by(Metric.timestamp.desc()).limit(limit).all()
            
            return [
                {
                    'metric_type':  m.metric_type,
                    'metric_value': m.metric_value,
                    'status':       m.status,
                    'timestamp':    m.timestamp.isoformat() if m.timestamp else None,
                    # metric_data contains type-specific details including
                    # failure_reason set by each monitor at evaluation time.
                    # Stored as 'details' so _build_analysis_prompt finds it.
                    'details':      m.metric_data or {},
                }
                for m in metrics
            ]
        except Exception as e:
            logger.error(f"Failed to get recent metrics: {e}")
            return []
    
    def _find_similar_incidents(self, incident) -> List[Dict[str, Any]]:
        """
        Find similar historical incidents using RAG
        
        Args:
            incident: Current incident
            
        Returns:
            List of similar incidents
        """
        try:
            incident_dict = {
                'title': incident.title,
                'description': incident.description,
                'severity': incident.severity,
                'application': {
                    'name': incident.application.name if incident.application else ''
                }
            }
            
            # Optional: filter by same application
            filter_criteria = {
                'application': incident.application.name if incident.application else None
            }
            
            similar = self.rag_engine.find_similar_incidents(
                incident_dict,
                filter_criteria,
                exclude_id=incident.id,
            )
            
            logger.info(f"Found {len(similar)} similar incidents for {incident.id}")
            return similar
            
        except Exception as e:
            logger.error(f"RAG search failed: {e}")
            return []
    
    def _perform_llm_analysis(
        self,
        incident,
        metrics: List[Dict],
        similar_incidents: List[Dict]
    ) -> Dict[str, Any]:
        """
        Perform LLM analysis on incident
        
        Args:
            incident: Incident
            metrics: Recent metrics
            similar_incidents: Similar historical incidents
            
        Returns:
            Analysis results
        """
        try:
            incident_dict = {
                'id': incident.id,
                'title': incident.title,
                'description': incident.description,
                'severity': incident.severity,
                'created_at': incident.created_at.isoformat() if incident.created_at else None,
                'server': {
                    'hostname': incident.server.hostname if incident.server else 'Unknown'
                },
                'application': {
                    'name': incident.application.name if incident.application else 'Unknown'
                }
            }
            
            analysis = self.llm_analyzer.analyze_incident(
                incident_dict,
                metrics,
                similar_incidents
            )
            
            logger.info(f"LLM analysis completed for incident {incident.id}")
            return analysis
            
        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")
            return {
                'root_cause': 'Analysis failed',
                'recommendations': ['Manual investigation required'],
                'confidence': 0.0
            }
    
    def _update_incident_with_analysis(self, incident, analysis, similar_incidents):
        """
        Save LLM analysis to the incidents table AND create an
        incident_comments record.
    
        This is called AFTER the Jira ticket already exists.
        The analysis goes into:
        1. incidents.llm_analysis column (for dashboard display)
        2. incident_comments table (for audit trail)
        """
        from axara.community.database.models import IncidentComment
    
        llm_text = analysis.get('raw_analysis', '')
        if not llm_text:
            llm_text = self._format_analysis(analysis)
    
        # Update incidents table
        update_data = {
            'llm_analysis': llm_text,
            # structured copy for the similar-incident store (no parsing of the rendered text)
            'llm_analysis_data': {
                'summary': analysis.get('summary'),
                'component': analysis.get('component'),
                'root_cause': analysis.get('root_cause'),
                'recommendations': list(analysis.get('recommendations') or []),
                'confidence': analysis.get('confidence'),
                'failed': bool(analysis.get('error')),
            },
        }
        if similar_incidents:
            update_data['similar_incidents'] = similar_incidents[:5]
    
        self.incident_repo.update(incident.id, update_data)
    
        # Also save as incident_comment for audit trail
        try:
            comment = IncidentComment(
                incident_id=incident.id,
                comment_type='llm_analysis',
                content=llm_text,
                created_by='llm_analyzer',
            )
            self.db.add(comment)
            self.db.commit()
            logger.info(f"Saved LLM analysis comment for incident {incident.id}")
        except Exception as e:
            logger.error(f"Failed to save LLM analysis comment: {e}")
            self.db.rollback()
    
        self.db.refresh(incident)
    
    def _create_external_tickets(self, incident):
        """Open a ticket with the application's ticket provider and attach any screenshot.

        Called BEFORE LLM analysis: the ticket carries the monitor details and thresholds;
        the analysis is added later as a separate note (_add_llm_comment).

        Args:
            incident: Incident row (refreshed afterwards so it carries the ticket reference).
        """
        provider, _ = self._ticketing(incident)
        if provider is None:
            logger.info(f"Ticket creation skipped for incident {incident.id}: no active ticket "
                        f"provider '{self._get_itsm_provider(incident)}'")
            return
        incident_dict = self._incident_to_dict(incident)
        try:
            ticket = provider.create_ticket(incident_dict)
            if ticket:
                self.incident_repo.update(incident.id, {"ticket_provider": provider.name, "ticket_ref": ticket})
                logger.info(f"Created {provider.name} ticket {ticket} for incident {incident.id}")
                if incident_dict.get('attachment'):
                    provider.add_attachment(ticket, incident_dict['attachment'])
        except Exception as e:
            logger.error(f"Failed to create {provider.name} ticket: {e}")
        self.db.refresh(incident)

    def _add_llm_comment(self, incident, analysis):
        """Add the LLM analysis to the incident's ticket.

        Args:
            incident: Incident row (its ticket must already exist).
            analysis: Analysis dict from the LLM analyzer.
        """
        comment_text = analysis.get('raw_analysis', incident.llm_analysis)
        if not comment_text:
            logger.warning(
                f"No LLM analysis to add as comment for incident {incident.id}"
            )
            return
        provider, ticket = self._ticketing(incident)
        if provider is None or not ticket:
            return
        try:
            provider.add_analysis_note(ticket, comment_text)
            logger.info(f"Added LLM analysis to {provider.name} ticket {ticket}")
        except Exception as e:
            logger.error(f"Failed to add {provider.name} comment: {e}")

    def _send_notifications(self, incident, event_type: str = 'opened'):
        """
        Send notifications through every registered notifier.

        Maps event_type to the notifier method:
          'opened'         → incident()
          'analysis_ready' → incident() with the LLM analysis in the dict
          'resolved'       → resolution()

        Maintenance suppression (Batch 3): if the incident's server is under a
        window with suppress_type 'alerts' or 'both', notifications are skipped.
        LLM/RAG and ticket updates still run upstream — only the outbound notify
        is silenced.

        Args:
            incident: Incident object
            event_type: 'opened' | 'analysis_ready' | 'resolved'
        """
        notifiers = self.registry.notifiers()
        if not notifiers:
            return
        if not self.registry.function_allowed("notifications"):
            logger.info(f"Notifications skipped for incident {incident.id}: "
                        f"licence is in monitoring-only mode or blocked")
            return
        # Maintenance check — fails open on lookup error.
        try:
            from axara.community.processing.maintenance_window import MaintenanceWindowManager
            mw_mgr = MaintenanceWindowManager(self.db)
            active = mw_mgr.active_suppress_type(
                incident.server_id, incident.application_id
            )
            logger.info(
                f"[mw-debug] _send_notifications: incident={incident.id} "
                f"server_id={incident.server_id} application_id={incident.application_id} "
                f"event={event_type} active_suppress_type={active!r}"
            )
            if active in ("alerts", "both"):
                logger.info(
                    f"Notification SUPPRESSED for incident {incident.id} "
                    f"(server={incident.server_id}, event={event_type}) — "
                    f"server is under a maintenance window (suppress_type={active})."
                )
                return
        except Exception as e:
            logger.warning(
                f"Maintenance lookup failed in _send_notifications; proceeding: {e}"
            )

        incident_dict = self._incident_to_dict(incident)
        incident_dict['event_type'] = event_type

        for notifier in notifiers:
            try:
                if event_type == 'resolved':
                    notifier.resolution(incident_dict)
                else:
                    notifier.incident(incident_dict)
            except Exception as e:
                logger.error(f"{notifier.name or 'notifier'} notification failed: {e}")

    def _send_resolution_notifications(self, incident):
        """
        Send resolution notifications.

        Maintenance suppression (Batch 3): skipped if the incident's server is
        under a window with suppress_type 'alerts' or 'both'.

        Args:
            incident: Incident
        """
        notifiers = self.registry.notifiers()
        if not notifiers:
            return
        if not self.registry.function_allowed("notifications"):
            logger.info(f"Notifications skipped for incident {incident.id}: "
                        f"licence is in monitoring-only mode or blocked")
            return
        try:
            from axara.community.processing.maintenance_window import MaintenanceWindowManager
            mw_mgr = MaintenanceWindowManager(self.db)
            active = mw_mgr.active_suppress_type(
                incident.server_id, incident.application_id
            )
            logger.info(
                f"[mw-debug] _send_resolution_notifications: incident={incident.id} "
                f"server_id={incident.server_id} application_id={incident.application_id} "
                f"active_suppress_type={active!r}"
            )
            if active in ("alerts", "both"):
                logger.info(
                    f"Resolution notification SUPPRESSED for incident {incident.id} "
                    f"(server={incident.server_id}) — server is under a maintenance "
                    f"window (suppress_type={active})."
                )
                return
        except Exception as e:
            logger.warning(
                f"Maintenance lookup failed in _send_resolution_notifications; proceeding: {e}"
            )

        incident_dict = self._incident_to_dict(incident)
        for notifier in notifiers:
            try:
                notifier.resolution(incident_dict)
            except Exception as e:
                logger.error(f"Failed to send {notifier.name or 'notifier'} resolution: {e}")

    def _store_in_rag(self, incident, resolution_notes: str = None, source: str = None,
                      check_licence: bool = True) -> bool:
        """
        Remember a resolved incident for future similar-incident search.

        Stored: the structured root cause and recommendations of the LLM analysis (only when
        the analysis succeeded) and how it was resolved (operator / ITSM notes, else an
        automatic-recovery note). Nothing is stored when there is neither a usable analysis
        nor resolution notes, so failed analyses never become "history".
        Returns True when a record was written.
        """
        try:
            if check_licence:
                if not self.registry.function_allowed("analysis"):
                    return False
            from axara.community.incident_management.rag_engine import analysis_fields, AUTO_RESOLUTION
            af = analysis_fields(getattr(incident, 'llm_analysis_data', None), incident.llm_analysis)
            notes = (resolution_notes or getattr(incident, 'resolution_notes', None) or '').strip()
            if not af['usable'] and not notes:
                logger.info(f"Incident {incident.id} not stored in RAG: no usable analysis and no resolution notes")
                return False
            resolution_info = {
                'root_cause': af['root_cause'],
                'recommendations': af['recommendations'],
                'confidence': af['confidence'],
                'resolution': notes or AUTO_RESOLUTION,
                'resolution_source': source or ('operator' if notes else 'auto'),
            }
            ok = self.rag_engine.store_incident(incident.id, self._incident_to_dict(incident), resolution_info)
            if ok:
                logger.info(f"Stored incident {incident.id} in RAG database")
            return ok
        except Exception as e:
            logger.error(f"Failed to store incident in RAG: {e}")
            return False
    
    def _ticket_url(self, incident) -> Optional[str]:
        """Web link to the incident's ticket.

        Args:
            incident: Incident row.

        Returns:
            URL, or None when there is no ticket or its tool has no base URL configured.
        """
        if not incident.ticket_ref:
            return None
        provider = self.registry.ticket_provider(incident.ticket_provider)
        return provider.ticket_url(incident.ticket_ref) if provider else None

    def _incident_to_dict(self, incident) -> Dict[str, Any]:
        """
        Convert incident SQLAlchemy model to dict
        
        Args:
            incident: Incident model
            
        Returns:
            Incident dict
        """
        return {
            'id': incident.id,
            'title': incident.title,
            'description': incident.description,
            'severity': incident.severity,
            'status': incident.status,
            'assignee': incident.assignee,
            'created_at': incident.created_at,
            'resolved_at': incident.resolved_at,
            'llm_analysis': incident.llm_analysis,
            'similar_incidents': incident.similar_incidents,
            'ticket_provider': incident.ticket_provider,
            'ticket_ref': incident.ticket_ref,
            'ticket_url': self._ticket_url(incident),
            'attachment': incident.attachment,
            'application': {
                'name': incident.application.name if incident.application else 'Unknown',
                'id': incident.application.id if incident.application else None
            },
            'server': {
                'hostname': incident.server.hostname if incident.server else 'Unknown',
                'id': incident.server.id if incident.server else None
            }
        }
