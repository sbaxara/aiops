# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Network Monitor
Monitors network interface statistics
"""
import psutil
import time
from typing import Dict, Any
from .base_monitor import BaseMonitor
import logging

logger = logging.getLogger(__name__)

__version__ = "1.1.0"

class NetworkMonitor(BaseMonitor):
    """
    Monitor network interface statistics
    Tracks bytes sent/received, errors, and drops
    """

    UNIT = "mbps"
    
    def __init__(self, config: Dict[str, Any]):
        """Set up the counters kept between runs for rate calculation.

        Args:
            config: Monitor definition.
        """
        super().__init__(config)
        # Store previous counters for rate calculation
        self._last_counters = None
        self._last_timestamp = None
    
    def check(self) -> Dict[str, Any]:
        """
        Check network statistics
        
        Returns:
            Result dict with network metrics
        """
        try:
            # Get network I/O statistics
            net_io = psutil.net_io_counters(pernic=False)
            current_timestamp = time.time()
            
            # Calculate rates if we have previous data
            rates = {}
            if self._last_counters and self._last_timestamp:
                time_delta = current_timestamp - self._last_timestamp
                
                if time_delta > 0:
                    rates = {
                        'bytes_sent_per_sec': (net_io.bytes_sent - self._last_counters.bytes_sent) / time_delta,
                        'bytes_recv_per_sec': (net_io.bytes_recv - self._last_counters.bytes_recv) / time_delta,
                        'packets_sent_per_sec': (net_io.packets_sent - self._last_counters.packets_sent) / time_delta,
                        'packets_recv_per_sec': (net_io.packets_recv - self._last_counters.packets_recv) / time_delta
                    }
            
            # Store current counters for next check
            self._last_counters = net_io
            self._last_timestamp = current_timestamp
            
            # Get per-interface statistics
            per_nic = psutil.net_io_counters(pernic=True)
            interfaces = {}
            
            for nic_name, nic_stats in per_nic.items():
                interfaces[nic_name] = {
                    'bytes_sent_mb': nic_stats.bytes_sent / (1024 ** 2),
                    'bytes_recv_mb': nic_stats.bytes_recv / (1024 ** 2),
                    'packets_sent': nic_stats.packets_sent,
                    'packets_recv': nic_stats.packets_recv,
                    'errin': nic_stats.errin,
                    'errout': nic_stats.errout,
                    'dropin': nic_stats.dropin,
                    'dropout': nic_stats.dropout
                }
            
            # Check for high error/drop rates
            total_errors = net_io.errin + net_io.errout
            total_drops = net_io.dropin + net_io.dropout
            
            # Determine status based on errors and drops
            status = 'pass'
            if total_errors > self.threshold_config.get('max_errors', 1000):
                status = 'warning'
            if total_drops > self.threshold_config.get('max_drops', 1000):
                status = 'warning'
            
            consec = self.config.get('consecutive_failures', 3)
            reasons = []
            if total_errors > self.threshold_config.get('max_errors', 1000):
                reasons.append(
                    f"Network errors {total_errors} in {self.name} exceeded consecutively {consec} "
                    f"times threshold of {self.threshold_config.get('max_errors', 1000)} "
                    f"(in={net_io.errin}, out={net_io.errout})"
                )
            if total_drops > self.threshold_config.get('max_drops', 1000):
                reasons.append(
                    f"Packet drops {total_drops} in {self.name} exceeded consecutively {consec} "
                    f"timesthreshold of {self.threshold_config.get('max_drops', 1000)} "
                    f"(in={net_io.dropin}, out={net_io.dropout})"
                )
            failure_reason = f"".join(reasons) if reasons else None

            details = {
                'bytes_sent_gb': net_io.bytes_sent / (1024 ** 3),
                'bytes_recv_gb': net_io.bytes_recv / (1024 ** 3),
                'packets_sent': net_io.packets_sent,
                'packets_recv': net_io.packets_recv,
                'errors_in': net_io.errin,
                'errors_out': net_io.errout,
                'drops_in': net_io.dropin,
                'drops_out': net_io.dropout,
                'rates': rates,
                'interfaces': interfaces
            }
            if failure_reason:
                details['failure_reason'] = failure_reason

            return self.create_result(
                name='network_stats',
                value=round((rates.get('bytes_sent_per_sec', 0) + rates.get('bytes_recv_per_sec', 0)) / (1024 ** 2), 2),
                status=status,
                details=details
            )
            
        except Exception as e:
            return self.handle_error(e)
