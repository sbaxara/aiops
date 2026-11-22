# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Disk Monitor
Monitors disk usage across all partitions
"""
import psutil
import platform
from typing import Dict, Any, List
from .base_monitor import BaseMonitor
import logging

logger = logging.getLogger(__name__)

__version__ = "1.2.0"


class DiskMonitor(BaseMonitor):
    """
    Monitor disk utilization
    Tracks disk usage for all mounted partitions
    """

    UNIT = "percent"

    def _is_readonly(self, partition) -> bool:
        """
        Return True if the partition is mounted read-only.
        Checks the opts string for common read-only indicators across platforms:
          Linux:  'ro'       (e.g. "ro,relatime")
          macOS:  'rdonly'   (psutil maps MNT_RDONLY → 'rdonly')
                  'read-only' (shown in mount(8) output on some macOS versions)
        """
        opts = partition.opts.lower()
        opt_list = [o.strip() for o in opts.split(',')]
        return 'ro' in opt_list or 'rdonly' in opt_list or 'read-only' in opt_list

    def check(self) -> Dict[str, Any]:
        """
        Check disk utilization
        
        Returns:
            Result dict with disk metrics
        """
        try:
            ignore_readonly = bool(self.extra_config.get('ignore_readonly', True))

            # Get all disk partitions
            partitions = psutil.disk_partitions(all=False)
            
            partition_data = []
            max_usage_percent = 0
            critical_partition = None
            
            for partition in partitions:
                # Skip read-only partitions when ignore_readonly is enabled.
                # This avoids false positives on macOS CoreSimulator volumes and
                # Linux bind-mounted read-only filesystems which will always be
                # 100% full (they cannot be written to, so free space is 0).
                if ignore_readonly and self._is_readonly(partition):
                    logger.debug(
                        f"Skipping read-only partition {partition.mountpoint} "
                        f"(opts: {partition.opts})"
                    )
                    continue

                try:
                    usage = psutil.disk_usage(partition.mountpoint)
                    
                    partition_info = {
                        'device': partition.device,
                        'mountpoint': partition.mountpoint,
                        'fstype': partition.fstype,
                        'total_gb': usage.total / (1024 ** 3),
                        'used_gb': usage.used / (1024 ** 3),
                        'free_gb': usage.free / (1024 ** 3),
                        'percent': usage.percent
                    }
                    
                    partition_data.append(partition_info)
                    
                    # Track highest usage
                    if usage.percent > max_usage_percent:
                        max_usage_percent = usage.percent
                        critical_partition = partition.mountpoint
                        
                except (PermissionError, OSError) as e:
                    # Skip partitions we can't access
                    logger.debug(f"Cannot access partition {partition.mountpoint}: {e}")
                    continue
            
            # Evaluate against thresholds using max usage
            status = self.evaluate_threshold(
                max_usage_percent,
                self.threshold_config.get('disk_percent', {})
            )
            
            thresholds   = self.threshold_config.get('disk_percent', {})
            critical_val = thresholds.get('critical')
            warning_val  = thresholds.get('warning')
            consec       = self.config.get('consecutive_failures', 3)

            if status == 'fail' and critical_val is not None:
                failure_reason = (
                    f"Current {max_usage_percent}% in {self.name} exceeded consecutively "
                    f"{consec} critical threshold of {critical_val}% "
                    f"for partition {critical_partition}"
                )
            elif status == 'warning' and warning_val is not None:
                failure_reason = (
                    f"Current {max_usage_percent}% in {self.name} exceeded consecutively "
                    f"{consec} warning threshold of {warning_val}% "
                    f"for partition {critical_partition}"
                )
            else:
                failure_reason = None

            details = {
                'max_usage_percent': max_usage_percent,
                'critical_partition': critical_partition,
                'partitions': partition_data,
                'partition_count': len(partition_data),
                'ignore_readonly': ignore_readonly,
            }
            if failure_reason:
                details['failure_reason'] = failure_reason

            return self.create_result(
                name='disk_percent',
                value=max_usage_percent,
                status=status,
                details=details
            )
            
        except Exception as e:
            return self.handle_error(e)
