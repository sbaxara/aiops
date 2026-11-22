# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Process Monitor
Checks that a named process is running locally on this server.
Uses psutil — no SSH required, runs on both Windows and Linux.

Required entry in monitors.yaml config section:
    config:
        process_name: "nginx"       # substring match against process name or cmdline
        expected_min_count: 1       # optional, default 1
        match_cmdline: false        # optional — also search command-line args

Thresholds: not used (pass/fail based on count)
"""
import psutil
import platform
from typing import Dict, Any
from .base_monitor import BaseMonitor
import logging

logger = logging.getLogger(__name__)

__version__ = "1.0.0"


class ProcessMonitor(BaseMonitor):
    """
    Monitor that a specific process is running on this host.
    Counts matching processes and fails if count < expected_min_count.
    """

    UNIT = "count"

    def check(self) -> Dict[str, Any]:
        """Check that at least the expected number of processes match the configured name.

        Returns:
            The monitor result: pass when enough processes run, fail otherwise.
        """
        process_name = self.extra_config.get('process_name')
        if not process_name:
            return self.create_result(
                name='process_check',
                value=0,
                status='error',
                details={'error': "'process_name' not set in monitor config.config"}
            )

        expected_min  = int(self.extra_config.get('expected_min_count', 1))
        match_cmdline = self.extra_config.get('match_cmdline', False)

        matching = []
        try:
            for proc in psutil.process_iter(['pid', 'name', 'status', 'cmdline']):
                try:
                    name_match = process_name.lower() in proc.info['name'].lower()

                    cmdline_match = False
                    if match_cmdline and proc.info.get('cmdline'):
                        cmdline_str = ' '.join(proc.info['cmdline']).lower()
                        cmdline_match = process_name.lower() in cmdline_str

                    if name_match or cmdline_match:
                        matching.append({
                            'pid':    proc.info['pid'],
                            'name':   proc.info['name'],
                            'status': proc.info['status'],
                        })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    # Process disappeared or we lack permission — skip it
                    continue

        except Exception as e:
            return self.handle_error(e)

        count  = len(matching)
        passed = count >= expected_min
        status = 'pass' if passed else 'fail'

        consec = self.config.get('consecutive_failures', 3)
        failure_reason = None
        if not passed:
            failure_reason = (
                f"Current {count} instance(s) of {process_name} in {self.name} "
                f"exceeded consecutively {consec} times "
                f"critical threshold of minimum {expected_min} instance(s)"
            )

        details = {
            'process_name':      process_name,
            'expected_min_count': expected_min,
            'found_count':        count,
            'platform':           platform.system(),
            'processes':          matching[:10],
        }
        if failure_reason:
            details['failure_reason'] = failure_reason

        return self.create_result(
            name='process_count',
            value=count,
            status=status,
            details=details
        )
