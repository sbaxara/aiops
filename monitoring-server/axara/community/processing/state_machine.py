# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
State Machine
Tracks consecutive failures/successes to prevent alert flapping
"""
from datetime import datetime
from typing import Dict, Any, Optional
import base64
import json
import logging
import os
import re
import time
from axara.community.database.models import MonitorState, LogMonitorState, MonitorConfig, IncidentComment
from axara.community.database.repositories.monitor_repo import MonitorRepository
from axara.community.database.repositories.incident_repo import IncidentRepository
from sqlalchemy.orm import Session
from axara.community.processing.deduplicator import AlertDeduplicator

logger = logging.getLogger(__name__)


class MonitorStateMachine:
    """
    State machine for monitor health tracking
    Prevents alert flapping by requiring consecutive failures/successes
    """
    
    STATES = {
        'HEALTHY': 'healthy',
        'DEGRADED': 'degraded',  # Some failures but not enough for incident
        'FAILED': 'failed'       # Threshold reached, incident created
    }
    
    def __init__(self, db: Session):
        """
        Initialize state machine
        
        Args:
            db: Database session
        """
        self.db = db
        self.monitor_repo = MonitorRepository(db)
        self.incident_repo = IncidentRepository(db)
        self.deduplicator = AlertDeduplicator(db)
    
    def process_metric_result(self, metric: Dict[str, Any]):
        """
        Process a metric result and update state machine
        
        Args:
            metric: Metric result dict from agent
        """
        monitor_config_id = metric.get('monitor_config_id')
        status = metric.get('status')
        metric_type = metric.get('type')
        
        if not monitor_config_id:
            logger.warning("Metric missing monitor_config_id")
            return
        
        # Get monitor configuration
        monitor_config = self.monitor_repo.find_by_id(monitor_config_id)
        if not monitor_config:
            logger.warning(f"Monitor config not found: {monitor_config_id}")
            return
        
        # Handle log monitors separately (pattern-specific tracking)
        if metric_type == 'log':
            self._process_log_metric(metric, monitor_config)
        else:
            self._process_standard_metric(metric, monitor_config)
    
    def _process_standard_metric(
        self,
        metric: Dict[str, Any],
        monitor_config: MonitorConfig
    ):
        """
        Process standard (non-log) metric
        
        Args:
            metric: Metric result
            monitor_config: Monitor configuration
        """
        # Get or create monitor state
        state = self.db.query(MonitorState).filter(
            MonitorState.monitor_config_id == monitor_config.id
        ).first()
        
        if not state:
            state = MonitorState(
                monitor_config_id=monitor_config.id,
                current_status=self.STATES['HEALTHY'],
                consecutive_failures=0
            )
            self.db.add(state)
            self.db.commit()
        
        # Update state based on metric status
        metric_status = metric.get('status')
        
        if metric_status in ['fail', 'error']:
            self._handle_failure(state, monitor_config, metric)
        elif metric_status == 'warning':
            if state.current_status == self.STATES['FAILED']:
                # Warning while incident is open — treat as failure, don't progress recovery
                self._handle_failure(state, monitor_config, metric)
            else:
                # Warning with no open incident — treat as success, don't create incident
                self._handle_success(state, monitor_config, metric)
        elif metric_status == 'pass':                          # ← missing
            self._handle_success(state, monitor_config, metric)
        else:
            logger.warning(f"Unknown metric status: {metric_status}")
    
    def _process_log_metric(
        self,
        metric: Dict[str, Any],
        monitor_config: MonitorConfig
    ):
        """
        Process log metric with pattern-specific tracking
        
        Args:
            metric: Log metric result
            monitor_config: Monitor configuration
        """
        details = metric.get('details', {})
        files_checked = details.get('files_checked', [])
        
        for file_result in files_checked:
            log_file = file_result['file']
            patterns = file_result.get('patterns', {})
            if not log_file or not patterns:
                logger.warning(f"Skipping malformed log file_result: {file_result}")
                continue
            
            for pattern_name, pattern_result in patterns.items():
                self._process_log_pattern_state(
                    monitor_config.id,
                    log_file,
                    pattern_name,
                    pattern_result,
                    monitor_config,
                    metric
                )
    
    def _process_log_pattern_state(
        self,
        monitor_config_id: str,
        log_file: str,
        pattern_name: str,
        pattern_result: Dict,
        monitor_config: MonitorConfig,
        metric: Dict
    ):
        """Update the state of one log pattern in one file and open or resolve its incident.

        Args:
            monitor_config_id: Log monitor.
            log_file: File the pattern was checked in.
            pattern_name: Pattern name.
            pattern_result: Count and threshold result for the pattern.
            monitor_config: The monitor config.
            metric: The full metric.
        """
        state = self.db.query(LogMonitorState).filter(
            LogMonitorState.monitor_config_id == monitor_config_id,
            LogMonitorState.log_file_path == log_file,
            LogMonitorState.pattern_name == pattern_name
        ).first()

        if not state:
            state = LogMonitorState(
                monitor_config_id=monitor_config_id,
                log_file_path=log_file,
                pattern_name=pattern_name,
                consecutive_failures=0
            )
            self.db.add(state)
            self.db.commit()

        threshold_breached = pattern_result.get('threshold_breached', False)

        if threshold_breached:
            state.last_failure_time = datetime.utcnow()
            state.last_error_count = pattern_result['count']

            if state.incident_id:
                # Already failed — reset recovery counter and add recheck comment
                state.consecutive_failures = 0
                self._add_failure_comment(state.incident_id, monitor_config, metric)
                self._trigger_recheck_comment(state.incident_id)
            else:
                # Count toward incident creation
                state.consecutive_failures += 1

                logger.info(
                    f"Log pattern failure: {log_file}/{pattern_name} "
                    f"({state.consecutive_failures}/{monitor_config.consecutive_failures})"
                )

                if state.consecutive_failures >= monitor_config.consecutive_failures:
                    state.consecutive_failures = 0   # reset for recovery counting
                    self._create_log_incident(
                        state, monitor_config, log_file,
                        pattern_name, pattern_result, metric
                    )
        else:
            # Success
            state.last_success_time = datetime.utcnow()

            if state.incident_id:
                # Count consecutive successes toward resolution
                state.consecutive_failures += 1

                logger.info(
                    f"Log pattern recovery: {log_file}/{pattern_name} "
                    f"({state.consecutive_failures}/{monitor_config.consecutive_failures})"
                )

                self._add_recovery_comment(
                    state.incident_id,
                    monitor_config,
                    metric,
                    state.consecutive_failures,
                    monitor_config.consecutive_failures
                )
                self._trigger_recheck_comment(state.incident_id)

                if state.consecutive_failures >= monitor_config.consecutive_failures:
                    self._resolve_incident(state.incident_id)
                    state.incident_id = None
                    state.consecutive_failures = 0
            else:
                # No incident open — reset any partial failure count
                state.consecutive_failures = 0

        self.db.commit()
    
    def _handle_failure(
        self,
        state: MonitorState,
        monitor_config: MonitorConfig,
        metric: Dict
    ):
        """Handle a failing result: count failures and open an incident once the threshold is reached
        (an already failed monitor gets a recheck comment instead).

        Args:
            state: Monitor state row.
            monitor_config: The monitor config.
            metric: The failing metric.
        """
        state.last_failure_time = datetime.utcnow()

        if state.current_status == self.STATES['FAILED']:
            # Already failed — reset recovery counter and add recheck comment
            state.consecutive_failures = 0
            if state.incident_id:
                self._add_failure_comment(state.incident_id, monitor_config, metric)
                self._trigger_recheck_comment(state.incident_id)
            else:
                # No incident linked — try to create one
                self._create_incident(state, monitor_config, metric)
        else:
            # HEALTHY or DEGRADED — count toward incident creation
            state.consecutive_failures += 1

            logger.info(
                f"Monitor failure: {monitor_config.name} "
                f"({state.consecutive_failures}/{monitor_config.consecutive_failures})"
            )

            if state.consecutive_failures >= monitor_config.consecutive_failures:
                state.current_status = self.STATES['FAILED']
                state.consecutive_failures = 0   # reset — will now count recovery successes
                self._create_incident(state, monitor_config, metric)
            else:
                state.current_status = self.STATES['DEGRADED']

        self.db.commit()

    def _handle_success(
        self,
        state: MonitorState,
        monitor_config: MonitorConfig,
        metric: Dict
    ):
        """Handle a passing result: count successes and resolve the incident once enough pass in a row.

        Args:
            state: Monitor state row.
            monitor_config: The monitor config.
            metric: The passing metric.
        """
        state.last_success_time = datetime.utcnow()

        if state.current_status == self.STATES['FAILED']:
            # Count consecutive successes toward resolution
            state.consecutive_failures += 1

            logger.info(
                f"Monitor recovery: {monitor_config.name} "
                f"({state.consecutive_failures}/{monitor_config.consecutive_failures})"
            )

            # Add a recovery progress comment so the ticket reflects each
            # consecutive success check.
            if state.incident_id:
                self._add_recovery_comment(
                    state.incident_id,
                    monitor_config,
                    metric,
                    state.consecutive_failures,
                    monitor_config.consecutive_failures
                )
            self._trigger_recheck_comment(state.incident_id)
            
            if state.consecutive_failures >= monitor_config.consecutive_failures:
                state.current_status = self.STATES['HEALTHY']
                state.consecutive_failures = 0
                if state.incident_id:
                    self._resolve_incident(state.incident_id)
                    state.incident_id = None
        else:
            # HEALTHY or DEGRADED — reset any partial failure count
            state.consecutive_failures = 0
            state.current_status = self.STATES['HEALTHY']

        self.db.commit()
    
    def _create_incident(
        self,
        state,       # MonitorState
        monitor_config,  # MonitorConfig
        metric: dict,
    ):
        """
        Create an incident using the AlertDeduplicator for atomic
        check-and-create. Eliminates the TOCTOU race condition during
        offline metric replay.

        Maintenance suppression (Batch 3): if the server is under a window with
        suppress_type 'incidents' or 'both', skip creation entirely. Failure
        tracking was already advanced by the caller, so when the window ends the
        next failure opens an incident on the very next consecutive-failure tick.
        Existing open incidents on this server are NOT affected by 'incidents' —
        only the creation of new ones.

        Licence: no new incidents in monitoring-only / blocked (same effect as suppression).
        """
        from axara.community.plugins.loader import get_registry
        if not get_registry().function_allowed("incidents"):
            logger.info(f"Incident creation skipped for monitor {monitor_config.name}: "
                        f"licence is in monitoring-only mode or blocked")
            return
        # Maintenance check — fails open (logs + proceeds) if the lookup errors.
        try:
            from axara.community.processing.maintenance_window import MaintenanceWindowManager
            mw_mgr = MaintenanceWindowManager(self.db)
            active = mw_mgr.active_suppress_type(
                monitor_config.server_id, monitor_config.application_id
            )
            if active in ("incidents", "both"):
                logger.info(
                    f"Incident creation SUPPRESSED for monitor {monitor_config.name} "
                    f"(server={monitor_config.server_id}) — server is under a "
                    f"maintenance window (suppress_type={active})."
                )
                return
        except Exception as e:
            logger.warning(
                f"Maintenance lookup failed; proceeding with incident creation: {e}"
            )

        incident_key = f"{monitor_config.id}:{monitor_config.server_id}:{metric['type']}"
    
        title       = self._generate_incident_title(monitor_config, metric)
        description = self._generate_incident_description(monitor_config, metric)
    
        # Save screenshot PNG before creating the incident.
        attachment_path = None
        details = metric.get('details') or {}
    
        b64 = details.get('screenshot_b64', '') or ''
        if not b64:
            stdout_raw = details.get('stdout', '') or ''
            for _line in reversed(stdout_raw.splitlines()):
                _line = _line.strip()
                if _line.startswith('{'):
                    try:
                        _parsed = json.loads(_line)
                        b64 = _parsed.get('screenshot_b64') or ''
                    except (json.JSONDecodeError, ValueError):
                        pass
                    break
    
        if b64 and not b64.startswith('screenshot_failed'):
            attachment_path = self._save_screenshot(b64, monitor_config)
            # Option 1: record screenshot_ref in details (the stored reference) and drop
            # the bulky b64 from details so it is never persisted in metric_data.
            if attachment_path:
                details['screenshot_ref'] = os.path.basename(attachment_path)
            details.pop('screenshot_b64', None)
    
        incident_data = {
            'monitor_config_id': monitor_config.id,
            'server_id':         monitor_config.server_id,
            'application_id':    monitor_config.application_id,
            'title':             title,
            'description':       description,
            'severity':          monitor_config.severity,
            'status':            'open',
            'assignee':          monitor_config.assignee_override
                                or monitor_config.application.primary_assignee,
            'attachment':        attachment_path,
        }
    
        # Atomic deduplicate-and-create with advisory lock
        result = self.deduplicator.deduplicate_and_create(
            incident_key=incident_key,
            incident_data=incident_data,
        )
    
        state.incident_id = result.incident.id
        self.db.commit()
    
        if result.was_existing:
            # Existing incident — add a failure comment
            self._add_failure_comment(result.incident.id, monitor_config, metric)
            self._trigger_recheck_comment(result.incident.id)
        else:
            # New incident — trigger orchestrator
            logger.info(f"Created incident: {result.incident.id} - {title}")
            self._trigger_orchestrator(result.incident.id, monitor_config.id)

    def _add_failure_comment(self, incident_id: str, monitor_config, metric: Dict):
        """Add a comment to an existing open incident on each repeated failure."""
        try:
            details = metric.get('details', {})
            lines = [
                f"Monitor re-checked: {monitor_config.name}",
                f"Result            : {metric.get('status', 'fail').upper()}",
            ]
            if details:
                # Skip bulky/binary keys — screenshot_b64 is saved as a file
                # attachment by the orchestrator, not embedded in comments.
                # stdout/stderr can be very long; include failure_reason instead.
                SKIP_KEYS = {'screenshot_b64', 'stdout', 'stderr'}
                lines.append("Details:")
                for k, v in details.items():
                    if k in SKIP_KEYS:
                        continue
                    # Truncate long values to keep comments readable in Jira
                    v_str = str(v)
                    if len(v_str) > 500:
                        v_str = v_str[:500] + '…'
                    lines.append(f"  {k}: {v_str}")

            comment = IncidentComment(
                incident_id  = incident_id,
                comment_type = "system",
                content      = "\n".join(lines),
                created_by   = "state_machine",
            )
            self.db.add(comment)
            self.db.commit()
            logger.info(f"Added failure re-check comment to incident {incident_id}")

        except Exception as e:
            logger.error(f"Failed to add failure comment to incident {incident_id}: {e}")
            self.db.rollback()

    def _add_recovery_comment(
        self,
        incident_id: str,
        monitor_config,
        metric: Dict,
        current_count: int,
        required_count: int
    ):
        """Add a comment on each consecutive success during the recovery window."""
        try:
            is_final = current_count >= required_count
            status_line = (
                f"Monitor check PASSED ({current_count}/{required_count}) — "
                + ("incident will now be resolved." if is_final
                   else f"{required_count - current_count} more consecutive pass(es) required to resolve.")
            )
            details = metric.get('details', {})
            # v1.0 envelope: failure_reason is top-level; fall back to details for safety.
            _fr = metric.get('failure_reason') or details.get('failure_reason')
            lines = [
                f"Monitor re-checked: {monitor_config.name}",
                f"Result            : {metric.get('status', 'pass').upper()}",
                f"Recovery progress : {status_line}",
            ]
            if _fr:
                lines.append(f"Previous failure  : {_fr}")
 
            comment = IncidentComment(
                incident_id  = incident_id,
                comment_type = "system",
                content      = "\n".join(lines),
                created_by   = "state_machine",
            )
            self.db.add(comment)
            self.db.commit()
            logger.info(
                f"Added recovery comment ({current_count}/{required_count}) "
                f"to incident {incident_id}"
            )
        except Exception as e:
            logger.error(f"Failed to add recovery comment to incident {incident_id}: {e}")
            self.db.rollback()

    def _trigger_recheck_comment(self, incident_id: str):
        """Add a recheck comment to an incident in a background thread."""
        import threading
        thread = threading.Thread(
            target=self._run_recheck_comment,
            args=(incident_id,),
            daemon=True
        )
        thread.start()

    def _run_recheck_comment(self, incident_id: str):
        """Add the recheck comment with its own database session (thread target)."""
        from axara.community.database import SessionLocal
        from axara.community.incident_management.orchestrator import IncidentOrchestrator
        db = SessionLocal()
        try:
            IncidentOrchestrator(db).process_recheck_comment(incident_id)
        except Exception as e:
            logger.error(f"Recheck comment failed for incident {incident_id}: {e}", exc_info=True)
        finally:
            db.close()

    def _create_log_incident(
        self,
        state,       # LogMonitorState
        monitor_config,  # MonitorConfig
        log_file: str,
        pattern_name: str,
        pattern_result: dict,
        metric: dict,
    ):
        """
        Create a log-pattern incident using the AlertDeduplicator for atomic
        check-and-create.
        """
        from axara.community.plugins.loader import get_registry
        if not get_registry().function_allowed("incidents"):
            logger.info(f"Incident creation skipped for monitor {monitor_config.name}: "
                        f"licence is in monitoring-only mode or blocked")
            return
        incident_key = f"{monitor_config.id}:{log_file}:{pattern_name}"
    
        title = f"Log Errors: {pattern_name} in {log_file}"
        description = (
            f"Log file: {log_file}\n"
            f"Pattern: {pattern_name}\n"
            f"Error count: {pattern_result['count']} "
            f"(threshold: {pattern_result['threshold']})\n\n"
            f"Recent errors:\n"
            f"{self._format_log_matches(pattern_result.get('matches', [])[:5])}"
        )
    
        incident_data = {
            'monitor_config_id': monitor_config.id,
            'server_id':         monitor_config.server_id,
            'application_id':    monitor_config.application_id,
            'title':             title,
            'description':       description,
            'severity':          monitor_config.severity,
            'status':            'open',
            'assignee':          monitor_config.assignee_override
                                or monitor_config.application.primary_assignee,
        }
    
        # Atomic deduplicate-and-create with advisory lock
        result = self.deduplicator.deduplicate_and_create(
            incident_key=incident_key,
            incident_data=incident_data,
        )
    
        state.incident_id = result.incident.id
        self.db.commit()
    
        if result.was_existing:
            self._add_failure_comment(result.incident.id, monitor_config, metric)
            self._trigger_recheck_comment(result.incident.id)
        else:
            logger.info(f"Created log incident: {result.incident.id} - {title}")
            self._trigger_orchestrator(result.incident.id, monitor_config.id)

    def _resolve_incident(self, incident_id: str):
        """Mark an open incident resolved and start the resolution steps (ticket update, memory)."""
        incident = self.incident_repo.find_by_id(incident_id)

        if incident and incident.status in ('open', 'in_progress', 'investigating'):
            self.incident_repo.update(incident_id, {
                'status':      'resolved',
                'resolved_at': datetime.utcnow()
            })
            logger.info(f"Auto-resolved incident: {incident_id}")
            self._trigger_resolution(incident_id)
    
    def _generate_incident_title(
        self,
        monitor_config: MonitorConfig,
        metric: Dict
    ) -> str:
        """Generate incident title"""
        return f"{monitor_config.name} Failed on {monitor_config.server.hostname}"
    
    def _generate_incident_description(self, monitor_config: MonitorConfig, metric: Dict) -> str:
        """
        Build a clean, human-readable incident description.

        Strips screenshot_b64 (saved as a file attachment), raw stdout/stderr
        JSON blobs (which duplicate failure_reason), and reformats failure_reason
        to remove the embedded JSON so the key error text is readable on its own.
        """
        details = metric.get('details', {})

        # Format thresholds — handles both nested {warning/critical} and flat {max_value}
        threshold_lines = []
        for key, value in (monitor_config.threshold_config or {}).items():
            if isinstance(value, dict):
                parts = []
                if 'warning'  in value: parts.append(f"warning > {value['warning']}")
                if 'critical' in value: parts.append(f"critical > {value['critical']}")
                threshold_lines.append(f"{key}: {', '.join(parts)}")
            else:
                threshold_lines.append(f"{key}: max {value}")

        lines = [
            f"Application  : {monitor_config.application.name if monitor_config.application else 'Unknown'}",
            f"Server       : {monitor_config.server.hostname if monitor_config.server else 'Unknown'}",
            f"Monitor      : {monitor_config.name}",
            "",
            "Details:",
        ]

        if threshold_lines:
            lines.append(f"threshold    : {' | '.join(threshold_lines)}")

        # Keys to skip — screenshot saved as file; stdout/stderr duplicated in failure_reason
        # step/error/script_status are top-level detail keys promoted by
        # browser_monitor.py — shown formatted from stdout_parsed below,
        # so skip them in the raw key-value dump to avoid duplication.
        SKIP_KEYS = {'screenshot_b64', 'stdout', 'stderr',
                     'step', 'error', 'script_status'}

        # Read step/error from top-level detail keys first (set by updated
        # browser_monitor.py). Fall back to parsing stdout JSON for older agents.
        stdout_parsed: dict = {}
        if details.get('step') or details.get('error'):
            # Top-level keys present — use them directly
            stdout_parsed = {
                'step':   details.get('step', ''),
                'error':  details.get('error', ''),
                'status': details.get('script_status', ''),
            }
        else:
            # Fallback: parse stdout JSON (may fail if stdout was truncated)
            raw_stdout = details.get('stdout', '')
            if raw_stdout:
                for line in reversed(raw_stdout.splitlines()):
                    line = line.strip()
                    if line.startswith('{'):
                        try:
                            stdout_parsed = json.loads(line)
                        except (json.JSONDecodeError, ValueError):
                            pass
                        break

        # v1.0 envelope: failure_reason is a top-level field. Surface it into this
        # formatter (which iterates details) so the comment still includes it.
        details = dict(details)
        if metric.get('failure_reason') and 'failure_reason' not in details:
            details['failure_reason'] = metric.get('failure_reason')

        for key, value in details.items():
            if key in SKIP_KEYS:
                continue

            if key == 'failure_reason' and isinstance(value, str):
                # Strip everything after the em dash in failure_reason.
                # The em dash may be followed by a raw stdout JSON blob, a
                # '[Browser Test]' print line, or other noise. All of it is
                # redundant because step/error are appended from stdout_parsed.
                em_dash_pos = value.find(' — ')  # em dash
                clean = value[:em_dash_pos] if em_dash_pos != -1 else value
                lines.append(f"failure_reason: {clean}")
            else:
                lines.append(f"{key}: {value}")

        # Append parsed error details from stdout JSON as structured lines
        if stdout_parsed:
            step  = stdout_parsed.get('step')
            error = stdout_parsed.get('error', '')
            if step:
                lines.append(f"step  : {step}")
            if error:
                # Indent continuation lines for readability
                error_lines = error.splitlines()
                lines.append(f"error : {error_lines[0]}")
                for cont in error_lines[1:]:
                    lines.append(f"        {cont}")

        return "\n".join(lines)
    
    def _save_screenshot(
        self,
        b64_data: str,
        monitor_config: MonitorConfig,
    ) -> Optional[str]:
        """
        Decode a base64 PNG string and save it to the attachments volume.

        Returns the absolute file path on success, None on failure.
        Filename format: attachment_<app>_<host>_<epoch_ms>.png
        """
        attachments_dir = os.getenv('ATTACHMENTS_DIR', '/app/attachments')
        try:
            os.makedirs(attachments_dir, exist_ok=True)
            app_name  = monitor_config.application.name if monitor_config.application else 'unknown'
            hostname  = monitor_config.server.hostname  if monitor_config.server  else 'unknown'
            app_safe  = re.sub(r'[^\w-]', '_', app_name)
            host_safe = re.sub(r'[^\w-]', '_', hostname)
            ts_ms     = int(time.time() * 1000)
            filename  = f'attachment_{app_safe}_{host_safe}_{ts_ms}.png'
            file_path = os.path.join(attachments_dir, filename)
            png_bytes = base64.b64decode(b64_data)
            with open(file_path, 'wb') as fh:
                fh.write(png_bytes)
            logger.info(f'Screenshot saved: {file_path}')
            return file_path
        except Exception as e:
            logger.error(f'Failed to save screenshot: {e}')
            return None

    def _format_dict(self, d: Dict, indent: int = 0) -> str:
        """Format dictionary for display"""
        lines = []
        for key, value in d.items():
            if isinstance(value, dict):
                lines.append(f"{'  ' * indent}{key}:")
                lines.append(self._format_dict(value, indent + 1))
            else:
                lines.append(f"{'  ' * indent}{key}: {value}")
        return '\n'.join(lines)
    
    def _format_log_matches(self, matches: list) -> str:
        """Format log matches for display"""
        lines = []
        for match in matches[:5]:  # First 5 matches
            lines.append(f"[{match.get('timestamp')}] {match.get('message')}")
        return '\n'.join(lines)
    
    def _trigger_orchestrator(self, incident_id: str, monitor_config_id: str):
        """
        Run the incident orchestration pipeline in a background thread so the
        state machine (which is already running in a BackgroundTask) is not blocked.
        LLM inference can take 30-120s — we must not hold the DB session open.
        """
        import threading
        thread = threading.Thread(
            target=self._run_orchestrator,
            args=(incident_id, monitor_config_id),
            daemon=True
        )
        thread.start()

    def _run_orchestrator(self, incident_id: str, monitor_config_id: str):
        """Opens its own DB session — safe to run in a separate thread."""
        from axara.community.database import SessionLocal
        from axara.community.incident_management.orchestrator import IncidentOrchestrator
        db = SessionLocal()
        try:
            IncidentOrchestrator(db).process_incident(incident_id, monitor_config_id)
        except Exception as e:
            logger.error(
                f"Orchestrator failed for incident {incident_id}: {e}",
                exc_info=True
            )
        finally:
            db.close()

    def _trigger_resolution(self, incident_id: str):
        """Run resolution workflow in a background thread."""
        import threading
        thread = threading.Thread(
            target=self._run_resolution,
            args=(incident_id,),
            daemon=True
        )
        thread.start()

    def _run_resolution(self, incident_id: str):
        """Opens its own DB session — safe to run in a separate thread."""
        from axara.community.database import SessionLocal
        from axara.community.incident_management.orchestrator import IncidentOrchestrator
        db = SessionLocal()
        try:
            IncidentOrchestrator(db).process_resolution(incident_id)
        except Exception as e:
            logger.error(
                f"Resolution orchestrator failed for incident {incident_id}: {e}",
                exc_info=True
            )
        finally:
            db.close()
