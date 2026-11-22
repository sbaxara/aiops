# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Memory Monitor
Monitors system memory utilization
"""
import psutil
from typing import Dict, Any
from .base_monitor import BaseMonitor
import logging

logger = logging.getLogger(__name__)

__version__ = "1.2.0"


class MemoryMonitor(BaseMonitor):
    """
    Monitor memory utilization
    Tracks physical memory and swap usage
    """

    UNIT = "percent"
    
    def check(self) -> Dict[str, Any]:
        """
        Check memory utilization
        
        Returns:
            Result dict with memory metrics
        """
        try:
            # Get virtual memory stats
            mem = psutil.virtual_memory()
            
            # Get swap memory stats
            swap = psutil.swap_memory()
            
            # Calculate percentages
            memory_percent = mem.percent
            swap_percent = swap.percent
            
            # Evaluate against thresholds
            status = self.evaluate_threshold(
                memory_percent,
                self.threshold_config.get('memory_percent', {})
            )
            
            thresholds   = self.threshold_config.get('memory_percent', {})
            critical_val = thresholds.get('critical')
            warning_val  = thresholds.get('warning')
            consec       = self.config.get('consecutive_failures', 3)

            if status == 'fail' and critical_val is not None:
                # top_processes are emitted as STRUCTURED data in details (always),
                # so failure_reason no longer carries the " — top process(es): …" suffix.
                failure_reason = (
                    f"Current {memory_percent}% in {self.name} exceeded consecutively "
                    f"{consec} times critical threshold of {critical_val}%"
                )
            elif status == 'warning' and warning_val is not None:
                failure_reason = (
                    f"Current {memory_percent}% in {self.name} exceeded consecutively "
                    f"{consec} times warning threshold of {warning_val}%"
                )
            else:
                failure_reason = None

            # Convert bytes to MB for readability
            details = {
                'memory_percent': memory_percent,
                'total_mb': mem.total / (1024 ** 2),
                'available_mb': mem.available / (1024 ** 2),
                'used_mb': mem.used / (1024 ** 2),
                'free_mb': mem.free / (1024 ** 2),
                'swap_percent': swap_percent,
                'swap_total_mb': swap.total / (1024 ** 2),
                'swap_used_mb': swap.used / (1024 ** 2),
                'swap_free_mb': swap.free / (1024 ** 2)
            }
            if self.extra_config.get('top_processes_enabled', True):
                details['top_processes'] = self._top_memory_processes_structured(
                    limit=int(self.extra_config.get('top_processes_count', 3))
                )

            return self.create_result(
                name='memory_percent',
                value=memory_percent,
                status=status,
                failure_reason=failure_reason,
                details=details
            )
            
        except Exception as e:
            return self.handle_error(e)

    def _top_memory_processes_structured(self, limit: int = 3):
        """
        Return up to `limit` processes by memory%, PID-keyed, as structured data:
            [{"pid": int, "name": str, "value_pct": float}, ...]
        memory_percent is a snapshot, so a single pass suffices.
        """
        try:
            procs = []
            for p in psutil.process_iter(['name', 'pid']):
                try:
                    mem = p.memory_percent()
                    if mem and mem > 0.1:
                        procs.append({'pid': p.info['pid'],
                                      'name': (p.info['name'] or 'unknown'),
                                      'value_pct': round(float(mem), 1)})
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            procs.sort(key=lambda x: x['value_pct'], reverse=True)
            return procs[:max(1, limit)]
        except Exception as e:
            logger.debug(f'Could not get structured top memory processes: {e}')
            return []

    def _top_memory_processes(self, threshold: float) -> str:
        """
        Return a concise string listing up to 3 processes with the highest
        memory utilisation at the time of the threshold breach.

        Unlike CPU, memory_percent is a snapshot (no reference interval needed),
        but individual process RSS rarely sums to the total system usage because
        OS page caches and shared libraries are not attributed to any process.
        We therefore report the top N significant consumers (mem > 0.1%) up to 3,
        rather than stopping at cumulative >= threshold.
        Format: 'java(42.1%), chrome(38.7%), python(5.2%)'
        """
        try:
            procs = []
            for p in psutil.process_iter(['name', 'pid']):
                try:
                    # Call memory_percent() directly — avoids process_iter
                    # attrs cache which can return None on some Windows configs.
                    mem  = p.memory_percent()
                    name = p.info['name'] or 'unknown'
                    if mem and mem > 0.1:  # ignore trivially small consumers
                        procs.append((name, mem))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            top3 = sorted(procs, key=lambda x: x[1], reverse=True)[:3]
            if not top3:
                return ''

            return ', '.join(f'{n}({m:.1f}%)' for n, m in top3)
        except Exception as e:
            logger.debug(f'Could not get top memory processes: {e}')
            return ''
