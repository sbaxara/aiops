# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Base Monitor Class
All monitor implementations inherit from this base class.

JSON Standardization (D2): create_result() now emits the versioned v1.0 metric
ENVELOPE centrally, so every monitor produces a consistent, schema-valid shape:

    {
      schema_version, monitor_config_id, type, monitor_version,
      metric_name, value (nullable), unit, status,
      failure_reason (top-level, null on pass), timestamp, details
    }

Per-monitor responsibilities shrink to:
  * declare a class-level UNIT (e.g. "percent", "ms", "count", "boolean", "days",
    "mbps", "none"); and a __version__ (already present in each module),
  * pass failure_reason= to create_result instead of stuffing it into details,
  * (logic monitors only) build their richer details (top_processes, log matches…).
"""
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"


class BaseMonitor(ABC):
    """Abstract base class for all monitors."""

    # Default unit for this monitor's primary `value`. Subclasses override.
    # One of: percent | ms | count | boolean | days | mbps | none
    UNIT: str = "none"

    def __init__(self, config: Dict[str, Any]):
        """Read the common monitor settings.

        Args:
            config: Monitor definition from monitors.yaml (monitor_config_id, name, type,
                enabled, check_interval_seconds, thresholds and the nested config block).
        """
        self.config = config
        self.monitor_config_id = config['monitor_config_id']
        self.name = config['name']
        self.type = config['type']
        self.enabled = config.get('enabled', True)
        self.check_interval_seconds = config.get('check_interval_seconds', 300)

        # monitors.yaml stores thresholds under 'thresholds' (server response) but
        # older code used 'threshold_config' — accept both.
        self.threshold_config = config.get(
            'threshold_config',
            config.get('thresholds', {})
        )

        # Type-specific extra configuration (url, port, process_name, script_path, …)
        self.extra_config = config.get('config', {})

    @abstractmethod
    def check(self) -> Dict[str, Any]:
        """Execute the monitor check; return a v1.0 envelope via create_result()."""
        pass

    def is_enabled(self) -> bool:
        """Whether this monitor is enabled in its configuration."""
        return self.enabled

    def evaluate_threshold(self, value: float, thresholds: Dict[str, float]) -> str:
        """Evaluate value against warning/critical thresholds → 'pass'|'warning'|'fail'."""
        critical = thresholds.get('critical')
        warning = thresholds.get('warning')
        if critical is not None and value >= critical:
            return 'fail'
        elif warning is not None and value >= warning:
            return 'warning'
        else:
            return 'pass'

    # ------------------------------------------------------------ envelope build

    def _monitor_version(self) -> Optional[str]:
        """Read __version__ from the subclass's defining module (best-effort)."""
        try:
            import sys
            mod = sys.modules.get(type(self).__module__)
            return getattr(mod, "__version__", None)
        except Exception:
            return None

    def create_result(
        self,
        name: str,
        value: Any,
        status: str,
        details: Dict[str, Any] = None,
        unit: str = None,
        failure_reason: str = None,
    ) -> Dict[str, Any]:
        """
        Build a v1.0 metric envelope.

        Args:
            name:           metric_name (e.g. 'cpu_percent') — renamed field on the wire.
            value:          primary scalar (may be None, e.g. ssl all-details).
            status:         pass | warning | fail | error | timeout.
            details:        type-specific payload (must NOT contain failure_reason now).
            unit:           overrides the class UNIT if given.
            failure_reason: promoted to the envelope top level (None on pass).
        """
        details = dict(details or {})
        # failure_reason is a top-level envelope field now. If a caller still left a
        # copy inside details, lift it out so we never duplicate it on the wire.
        if failure_reason is None and 'failure_reason' in details:
            failure_reason = details.pop('failure_reason')
        else:
            details.pop('failure_reason', None)

        return {
            'schema_version':    SCHEMA_VERSION,
            'monitor_config_id': self.monitor_config_id,
            'type':              self.type,
            'monitor_version':   self._monitor_version(),
            'metric_name':       name,
            'value':             value,
            'unit':              unit if unit is not None else self.UNIT,
            'status':            status,
            'failure_reason':    failure_reason,
            'timestamp':         datetime.now(timezone.utc).isoformat(),
            'details':           details,
        }

    def handle_error(self, error: Exception) -> Dict[str, Any]:
        """Standardised error envelope. details carries {error, error_type} (the
        server-side error-key waiver accepts this sparse shape)."""
        logger.error(f"Monitor '{self.name}' error: {error}", exc_info=True)
        return self.create_result(
            name=f"{self.type}_error",
            value=0,
            status='error',
            unit='none',
            details={'error': str(error), 'error_type': type(error).__name__},
        )
