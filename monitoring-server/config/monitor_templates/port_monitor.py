# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Port Monitor
Checks that a TCP port is in LISTEN state on this server.
Uses psutil — no SSH required, works on Windows and Linux.

This replaces the SSH-based netstat_check.py approach. Since this agent
runs locally on the monitored server, psutil gives direct access to
socket state without any network hop.

Required entry in monitors.yaml config section:
    config:
        port: 8080                  # TCP port number to check
        host: "0.0.0.0"            # optional — filter by bind address, default any
        protocol: "tcp"            # optional — 'tcp' or 'tcp6', default 'tcp'

Thresholds: not used (pass/fail based on listening state)
"""
import psutil
import platform
import socket
from typing import Dict, Any, List
from .base_monitor import BaseMonitor
import logging

logger = logging.getLogger(__name__)

__version__ = "1.0.0"


class PortMonitor(BaseMonitor):
    """
    Monitor that a specific TCP port is in LISTEN state on this host.
    Fails if no matching socket is found in the LISTEN state.
    """

    UNIT = "boolean"

    def check(self) -> Dict[str, Any]:
        """Check that the configured port is listening on this host.

        Returns:
            The monitor result: pass when listening, fail otherwise.
        """
        port = self.extra_config.get('port')
        if not port:
            return self.create_result(
                name='port_check',
                value=0,
                status='error',
                details={'error': "'port' not set in monitor config.config"}
            )

        port     = int(port)
        protocol = self.extra_config.get('protocol', 'tcp').lower()
        host     = self.extra_config.get('host')  # None means any bind address

        try:
            # Select connection kinds to inspect
            kinds = [protocol]  # 'tcp' or 'tcp6'
            if protocol == 'tcp':
                kinds = ['tcp', 'tcp6']  # check both IPv4 and IPv6

            listening_sockets: List[Dict] = []

            for conn in psutil.net_connections(kind='inet'):
                # Only LISTEN state
                if conn.status != psutil.CONN_LISTEN:
                    continue

                # Match port
                if conn.laddr.port != port:
                    continue

                # Optionally filter by bind address
                if host and conn.laddr.ip not in ('', '0.0.0.0', '::', host):
                    continue

                listening_sockets.append({
                    'ip':   conn.laddr.ip,
                    'port': conn.laddr.port,
                    'pid':  conn.pid,
                })

            is_listening = len(listening_sockets) > 0
            status = 'pass' if is_listening else 'fail'

            # Attempt a live connection as secondary confirmation
            connection_ok = False
            if is_listening:
                connection_ok = self._test_connection(port)

            consec = self.config.get('consecutive_failures', 3)
            failure_reason = None
            if not is_listening:
                failure_reason = (
                    f"Port {port}/{protocol} in {self.name} is not in LISTEN state "
                    f"consecutively {consec} times"
                )
            elif not connection_ok:
                failure_reason = (
                    f"Port {port}/{protocol} in {self.name} is listening but "
                    f"connection test failed consecutively {consec} times"
                )

            details = {
                'port':             port,
                'protocol':         protocol,
                'is_listening':     is_listening,
                'connection_ok':    connection_ok,
                'platform':         platform.system(),
                'listening_sockets': listening_sockets,
            }
            if failure_reason:
                details['failure_reason'] = failure_reason

            return self.create_result(
                name='port_listening',
                value=1 if is_listening else 0,
                status=status,
                details=details
            )

        except psutil.AccessDenied:
            # On some systems listing all connections requires elevated privileges.
            # Fall back to a direct connection attempt.
            logger.warning(
                f"Access denied reading net_connections, "
                f"falling back to connection test for port {port}"
            )
            connection_ok = self._test_connection(port)
            status = 'pass' if connection_ok else 'fail'
            return self.create_result(
                name='port_listening',
                value=1 if connection_ok else 0,
                status=status,
                details={
                    'port':          port,
                    'connection_ok': connection_ok,
                    'note':          'Used connection test (access denied for socket list)',
                    'platform':      platform.system(),
                }
            )
        except Exception as e:
            return self.handle_error(e)

    def _test_connection(self, port: int, timeout: float = 3.0) -> bool:
        """
        Attempt a TCP connection to 127.0.0.1:<port>.
        Returns True if the connection succeeds (port is accepting connections).
        """
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=timeout):
                return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            return False
