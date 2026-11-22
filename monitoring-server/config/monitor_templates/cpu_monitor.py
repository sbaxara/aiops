# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
CPU Monitor
Monitors CPU utilization across all cores
"""
import psutil
import platform
from typing import Dict, Any
from .base_monitor import BaseMonitor
import logging

logger = logging.getLogger(__name__)

__version__ = "1.3.0"


class CPUMonitor(BaseMonitor):
    """
    Monitor CPU utilization
    Tracks overall CPU percentage and per-core metrics
    """

    UNIT = "percent"
    
    def check(self) -> Dict[str, Any]:
        """
        Check CPU utilization
        
        Returns:
            Result dict with CPU metrics
        """
        try:
            # Get overall CPU percentage (average across interval)
            # interval=1 means measure over 1 second
            cpu_percent = psutil.cpu_percent(interval=1)
            
            # Get per-CPU percentages
            per_cpu_percent = psutil.cpu_percent(interval=0.1, percpu=True)
            
            # Get load average (Unix-like systems only)
            load_avg = self._get_load_average()
            
            # Get CPU count
            cpu_count_logical = psutil.cpu_count(logical=True)
            cpu_count_physical = psutil.cpu_count(logical=False)
            
            # Evaluate against thresholds
            status = self.evaluate_threshold(
                cpu_percent, 
                self.threshold_config.get('cpu_percent', {})
            )
            
            thresholds   = self.threshold_config.get('cpu_percent', {})
            critical_val = thresholds.get('critical')
            warning_val  = thresholds.get('warning')
            consec       = self.config.get('consecutive_failures', 3)

            if status == 'fail' and critical_val is not None:
                # top_processes are emitted as STRUCTURED data in details (always),
                # so failure_reason no longer carries the " — top process(es): …" suffix.
                failure_reason = (
                    f"Current {cpu_percent}% in {self.name} exceeded consecutively "
                    f"{consec} times critical threshold of {critical_val}%"
                )
            elif status == 'warning' and warning_val is not None:
                failure_reason = (
                    f"Current {cpu_percent}% in {self.name} exceeded consecutively "
                    f"{consec} warning threshold of {warning_val}%"
                )
            else:
                failure_reason = None

            details = {
                'cpu_percent': cpu_percent,
                'per_cpu_percent': per_cpu_percent,
                'cpu_count_logical': cpu_count_logical,
                'cpu_count_physical': cpu_count_physical,
                'load_average': load_avg,
                'platform': platform.system()
            }
            # Structured top processes (always, not only on breach), flag-gated.
            if self.extra_config.get('top_processes_enabled', True):
                details['top_processes'] = self._top_cpu_processes_structured(
                    limit=int(self.extra_config.get('top_processes_count', 3))
                )

            return self.create_result(
                name='cpu_percent',
                value=cpu_percent,
                status=status,
                failure_reason=failure_reason,
                details=details
            )
            
        except Exception as e:
            return self.handle_error(e)
    
    def _top_cpu_processes_structured(self, limit: int = 3):
        """
        Return up to `limit` processes by CPU%, PID-keyed, as structured data:
            [{"pid": int, "name": str, "value_pct": float}, ...]
        Two-pass measurement (psutil CPU% is a rate, 0.0 on first read).
        """
        import time as _time
        try:
            live = []
            for p in psutil.process_iter(['name', 'pid']):
                try:
                    p.cpu_percent()
                    live.append(p)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            _time.sleep(0.2)
            procs = []
            for p in live:
                try:
                    cpu = p.cpu_percent()
                    if cpu and cpu > 0:
                        procs.append({'pid': p.pid, 'name': (p.name() or 'unknown'),
                                      'value_pct': round(float(cpu), 1)})
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            procs.sort(key=lambda x: x['value_pct'], reverse=True)
            return procs[:max(1, limit)]
        except Exception as e:
            logger.debug(f'Could not get structured top CPU processes: {e}')
            return []

    def _top_cpu_processes(self, threshold: float) -> str:
        """
        Return a concise string listing the minimum number of processes
        (up to 3) whose cumulative CPU% explains the threshold breach.

        Uses a two-pass measurement:
          1. Initialize per-process CPU counters (psutil always returns 0.0
             on the very first call because CPU% is a rate, not a snapshot).
          2. Sleep 0.2 s so the kernel accumulates delta tick counts.
          3. Read actual CPU% values — now non-zero for busy processes.
          4. Accumulate from highest to lowest until cumulative >= threshold.
          Format: 'python(88.2%), java(6.1%)'
        """
        import time as _time
        try:
            # Pass 1 — initialise counters (returns 0.0 for every process)
            live = []
            for p in psutil.process_iter(['name', 'pid']):
                try:
                    p.cpu_percent()   # sets internal reference point
                    live.append(p)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            _time.sleep(0.2)  # allow kernel to accumulate delta ticks

            # Pass 2 — read actual rates
            procs = []
            for p in live:
                try:
                    cpu  = p.cpu_percent()   # now returns real value
                    name = p.name()
                    if cpu > 0:
                        procs.append((name or 'unknown', cpu))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            top3 = sorted(procs, key=lambda x: x[1], reverse=True)[:3]
            if not top3:
                return ''

            # Include processes until cumulative CPU >= threshold (min 1, max 3)
            selected = []
            cumulative = 0.0
            for name, cpu in top3:
                selected.append((name, cpu))
                cumulative += cpu
                if cumulative >= threshold:
                    break

            return ', '.join(f'{n}({c:.1f}%)' for n, c in selected)
        except Exception as e:
            logger.debug(f'Could not get top CPU processes: {e}')
            return ''

    def _get_load_average(self) -> Dict[str, float]:
        """
        Get load average (Unix-like systems)
        Windows doesn't have load average concept
        
        Returns:
            Dict with 1min, 5min, 15min load averages
        """
        if platform.system() == 'Windows':
            return None
        
        try:
            load_1, load_5, load_15 = psutil.getloadavg()
            return {
                '1min': load_1,
                '5min': load_5,
                '15min': load_15
            }
        except Exception:
            return None
