# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Metrics Collector
Executes monitors in parallel, respecting each monitor's individual check_interval_seconds.
Supports hot-reload of monitors.yaml without agent restart.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from typing import Dict, Any, List, Optional
from pathlib import Path
import hashlib
import logging
import time
import yaml
from datetime import datetime

logger = logging.getLogger(__name__)


class MetricsCollector:
    """
    Collects metrics by executing monitors in parallel.
    Each monitor runs on its own check_interval_seconds schedule.
    """

    def __init__(self, max_workers: int = 10, timeout: int = 30):
        """Create the collector.

        Args:
            max_workers: Monitors run in parallel on this many threads.
            timeout: Default per-monitor timeout in seconds (a monitor's config.timeout overrides it).
        """
        self.max_workers = max_workers
        self.timeout = timeout
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.monitor_configs = {}

        # Track last execution time per monitor key (monotonic clock)
        # Initialised to 0 so every monitor runs on the first collection pass
        self._last_run: Dict[str, Optional[float]] = {}   # None: not run yet (due now)
        # Monitors still running from an earlier pass (not started again until they finish)
        self._running: set = set()

        # Hot-reload state — uses both mtime AND content hash.
        # mtime alone can miss updates when a file is rewritten within the
        # same second (common in Docker volumes and CI/CD pipelines).
        self._monitors_file: Optional[Path] = None
        self._monitors_file_mtime: float = 0.0
        self._monitors_file_hash: Optional[str] = None

    # Loading

    def load_monitor_configs(self, configs: List[Dict], monitors_file: Optional[Path] = None):
        """
        Create monitor instances from a list of config dicts.
        Optionally record the source file path for hot-reload.

        Args:
            configs:        List of monitor config dicts (from monitors.yaml)
            monitors_file:  Path to monitors.yaml — if supplied, hot-reload is enabled
        """
        if monitors_file:
            self._monitors_file = Path(monitors_file)
            self._monitors_file_mtime = self._monitors_file.stat().st_mtime
            self._monitors_file_hash = self._file_hash(self._monitors_file)

        self._build_monitors(configs)

    def _file_hash(self, path: Path) -> str:
        """SHA-256 of a file's content. Used alongside mtime for change detection."""
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
        return h.hexdigest()

    def _build_monitors(self, configs: List[Dict]):
        """Instantiate monitor classes from config list."""
        from . import monitor_loader
        m = monitor_loader.load()               # monitor scripts are loaded from disk
        CPUMonitor, MemoryMonitor, DiskMonitor, NetworkMonitor = (
            m.CPUMonitor, m.MemoryMonitor, m.DiskMonitor, m.NetworkMonitor)
        LogMonitor, DatabaseMonitor, UrlMonitor, ProcessMonitor = (
            m.LogMonitor, m.DatabaseMonitor, m.UrlMonitor, m.ProcessMonitor)
        PortMonitor, BrowserMonitor, SSLMonitor = m.PortMonitor, m.BrowserMonitor, m.SSLMonitor

        monitor_classes = {
            'cpu':      CPUMonitor,
            'memory':   MemoryMonitor,
            'disk':     DiskMonitor,
            'network':  NetworkMonitor,
            'log':      LogMonitor,
            'database': DatabaseMonitor,
            'url':      UrlMonitor,
            'process':  ProcessMonitor,
            'port':     PortMonitor,
            'browser':  BrowserMonitor,
            'ssl':      SSLMonitor,
        }
        # Scripts that name their own type (MONITOR_TYPE), e.g. monitors added later by the server.
        for mtype, cls in monitor_loader.extra_monitor_classes().items():
            monitor_classes.setdefault(mtype, cls)

        new_configs = {}
        for config in configs:
            if not config.get('enabled', True):
                continue

            monitor_type = config.get('type')
            monitor_class = monitor_classes.get(monitor_type)

            if not monitor_class:
                logger.warning(
                    f"Unknown monitor type '{monitor_type}' — skipping. "
                    f"Known types: {list(monitor_classes.keys())}"
                )
                continue

            try:
                key = f"{config['name']}"
                new_interval = int(config.get('check_interval_seconds', 300))
                new_configs[key] = {
                    'monitor_config_id': config['monitor_config_id'],
                    'config':            config,
                    'instance':          monitor_class(config),
                    # Per-monitor interval in seconds (default 300)
                    'check_interval_seconds': new_interval,
                }
                logger.info(
                    f"Loaded monitor: {key} "
                    f"(interval={new_interval}s)"
                )
            except Exception as e:
                logger.error(f"Failed to create monitor '{monitor_type}': {e}", exc_info=True)

        # Detect interval changes and reset last_run so the monitor fires
        # immediately under the new schedule. This ensures that shortening
        # the interval (e.g. 300→60) takes effect on the very next pass
        # rather than waiting out the remainder of the old interval.
        for key in new_configs:
            old_data = self.monitor_configs.get(key)
            if old_data:
                old_interval = old_data.get('check_interval_seconds', 300)
                new_interval = new_configs[key]['check_interval_seconds']
                if old_interval != new_interval:
                    logger.info(
                        f"Monitor '{key}' interval changed: "
                        f"{old_interval}s → {new_interval}s — "
                        f"resetting schedule (will run on next pass)"
                    )
                    self._last_run[key] = None  # force immediate execution

            if key not in self._last_run:
                self._last_run[key] = None  # run immediately on first pass

        # Remove stale last_run entries for removed monitors
        for key in list(self._last_run.keys()):
            if key not in new_configs:
                del self._last_run[key]

        self.monitor_configs = new_configs
        logger.info(f"Monitor registry updated: {len(self.monitor_configs)} active monitors")

    # Hot-reload

    def _check_for_reload(self):
        """
        If monitors.yaml has been modified since last load, reload it.
        Called at the start of every collect_all_metrics() pass.

        Uses both file mtime AND content hash to detect changes reliably.
        mtime alone can miss updates when the file is rewritten within the
        same second (1-second resolution on many filesystems, especially
        inside Docker volumes).
        """
        if not self._monitors_file or not self._monitors_file.exists():
            return

        try:
            current_mtime = self._monitors_file.stat().st_mtime

            # Fast path: if mtime hasn't changed, skip the hash check
            if current_mtime == self._monitors_file_mtime:
                return

            # mtime changed — compute content hash to confirm real change
            current_hash = self._file_hash(self._monitors_file)
            if current_hash == self._monitors_file_hash:
                # File was touched but content is identical — update mtime
                # tracker so we don't re-hash on every pass
                self._monitors_file_mtime = current_mtime
                return

            logger.info(
                f"monitors.yaml changed (hash {self._monitors_file_hash[:12]}… "
                f"→ {current_hash[:12]}…), reloading..."
            )
            with open(self._monitors_file, 'r') as f:
                data = yaml.safe_load(f)

            self._build_monitors(data.get('monitors', []))
            self._monitors_file_mtime = current_mtime
            self._monitors_file_hash = current_hash
            logger.info("Hot-reload complete")

        except Exception as e:
            logger.error(f"Hot-reload failed: {e}", exc_info=True)
            # Keep running with existing monitors — do not crash

    def has_monitor_type(self, monitor_type: str) -> bool:
        """Whether a monitor of this type is configured (enabled) on this agent.

        Args:
            monitor_type: Monitor type, e.g. 'ssl'.

        Returns:
            True when monitors.yaml holds one.
        """
        return any((d.get('config') or {}).get('type') == monitor_type for d in self.monitor_configs.values())

    # Collection

    def collect_all_metrics(self) -> List[Dict[str, Any]]:
        """
        Execute all monitors that are due based on their individual check_interval_seconds.
        Monitors not yet due are skipped silently.

        Returns:
            List of metric result dicts for monitors that ran this pass.
        """
        # Check if monitors.yaml has changed before deciding who is due
        self._check_for_reload()

        now = time.monotonic()
        due = {
            key: data
            for key, data in self.monitor_configs.items()
            # None = never run: due now. (0.0 would mean the host's boot time on the monotonic
            # clock, so a daily monitor would wait until the host had been up for a day.)
            if (self._last_run.get(key) is None
                or (now - self._last_run[key]) >= data['check_interval_seconds'])
            and key not in self._running
        }

        if not due:
            logger.debug("No monitors due this pass")
            return []

        logger.info(
            f"Running {len(due)}/{len(self.monitor_configs)} monitors due this pass: "
            f"{list(due.keys())}"
        )

        futures = {}
        results = []

        for key, data in due.items():
            future = self.executor.submit(
                self._execute_monitor_with_timeout, key, data
            )
            futures[future] = key
            self._running.add(key)
            future.add_done_callback(lambda _f, k=key: self._running.discard(k))

        # Per-monitor timeout: honour config.timeout when a monitor declares one
        # (same convention browser/ssl/database already use for their own internal
        # timeouts), falling back to the global default. Monitors that enumerate
        # hundreds of items need far longer than the 30s default — config.timeout
        # lets them set e.g. 120s without slowing the rest. The overall pass budget is the SUM of the due
        # monitors' timeouts so a slow monitor gets its full allowance instead of
        # being cut off by a tight shared budget.
        def _mon_timeout(key: str) -> int:
            """Run-time allowance in seconds for one monitor.

            In order: config.run_timeout, the monitor class's RUN_TIMEOUT (monitors that scan
            many ports set one), config.timeout, else the collector default.

            Args:
                key: Monitor key in monitor_configs.

            Returns:
                Timeout in seconds.
            """
            # monitor_configs[key]['config'] is the FULL monitor dict (see
            # _build_monitors); the per-monitor timeout lives in its NESTED 'config'
            # block (config.config.timeout), the same place browser/ssl/database put
            # their own timeout. Read that; fall back to the global default.
            entry = self.monitor_configs.get(key, {})
            full_cfg = entry.get('config') or {}          # the full monitor dict
            inner = full_cfg.get('config') or {}          # the nested config block
            raw = inner.get('run_timeout')
            if raw is None:
                raw = getattr(type(entry.get('instance')), 'RUN_TIMEOUT', None)
            if raw is None:
                raw = inner.get('timeout')
            try:
                return int(raw) if raw is not None else self.timeout
            except (TypeError, ValueError):
                return self.timeout

        overall_budget = max(sum(_mon_timeout(k) for k in due), self.timeout)

        try:
            for future in as_completed(futures, timeout=overall_budget):
                key = futures[future]
                mon_to = _mon_timeout(key)
                try:
                    result = future.result(timeout=mon_to)
                    results.append(result)
                    self._last_run[key] = now
                    logger.debug(f"Monitor '{key}' completed: {result['status']}")
                except TimeoutError:
                    logger.error(f"Monitor '{key}' timed out after {mon_to}s")
                    self._last_run[key] = now
                    results.append(self._create_timeout_result(self.monitor_configs[key]))
                except Exception as e:
                    logger.error(f"Monitor '{key}' failed: {e}")
                    self._last_run[key] = now
                    results.append(self._create_error_result(self.monitor_configs[key], e))
        except TimeoutError:
            logger.error("Overall collection timed out — returning partial results")
            # Unfinished monitors: report a timeout and wait for their interval before the
            # next run (they are not started again while the old run is still going).
            for future, key in futures.items():
                if not future.done():
                    logger.error(f"Monitor '{key}' did not finish within {_mon_timeout(key)}s")
                    self._last_run[key] = now
                    results.append(self._create_timeout_result(self.monitor_configs[key]))

        logger.info(f"Collection complete: {len(results)} results from {len(due)} due monitors")
        return results

    # Helpers

    def _execute_monitor_with_timeout(self, key: str, data: Dict) -> Dict[str, Any]:
        """Run one monitor's check() and fill in monitor_config_id and timestamp when missing.

        Args:
            key: Monitor key (for logging).
            data: Registry entry holding the monitor instance and its config.

        Returns:
            The monitor result, or an error result when check() raises.
        """
        try:
            result = data['instance'].check()
            if 'monitor_config_id' not in result:
                result['monitor_config_id'] = data['monitor_config_id']
            if 'timestamp' not in result:
                result['timestamp'] = datetime.utcnow().isoformat()
            return result
        except Exception as e:
            logger.error(f"Monitor '{key}' execution error: {e}", exc_info=True)
            return self._create_error_result(data, e)

    def _create_timeout_result(self, data: Dict) -> Dict[str, Any]:
        """A v1.0 result envelope reporting that a monitor timed out.

        Args:
            data: Registry entry of the monitor.

        Returns:
            The result with status 'timeout'.
        """
        # v1.0 envelope (JSON Standardization). The error key in details triggers
        # the server-side error-key waiver, so no typed-details validation is needed.
        config = data['config']
        return {
            'schema_version':    '1.0',
            'monitor_config_id': data['monitor_config_id'],
            'type':              config['type'],
            'monitor_version':   None,
            'metric_name':       f"{config['type']}_timeout",
            'value':             0,
            'unit':              'none',
            'status':            'timeout',
            'failure_reason':    'Monitor execution timed out',
            'timestamp':         datetime.utcnow().isoformat(),
            'details':           {'error': 'Monitor execution timed out',
                                  'error_type': 'TimeoutError',
                                  'timeout_seconds': self.timeout},
        }

    def _create_error_result(self, data: Dict, error: Exception) -> Dict[str, Any]:
        """A v1.0 result envelope reporting that a monitor raised an error.

        Args:
            data: Registry entry of the monitor.
            error: The exception raised.

        Returns:
            The result with status 'error' and the error in details.
        """
        # v1.0 envelope (JSON Standardization); error-key waiver applies.
        config = data['config']
        return {
            'schema_version':    '1.0',
            'monitor_config_id': data['monitor_config_id'],
            'type':              config['type'],
            'monitor_version':   None,
            'metric_name':       f"{config['type']}_error",
            'value':             0,
            'unit':              'none',
            'status':            'error',
            'failure_reason':    str(error),
            'timestamp':         datetime.utcnow().isoformat(),
            'details':           {'error': str(error), 'error_type': type(error).__name__},
        }

    def shutdown(self):
        """Stop the worker pool without waiting for running checks."""
        self.executor.shutdown(wait=False)
        logger.info("Metrics collector shutdown complete")
