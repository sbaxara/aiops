# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
SSL Certificate Monitor (Community edition): certificate validity and expiry.

Scans all listening TCP ports on the local server, attempts a TLS handshake on each and
reports, per certificate, its subject, issuer, validity dates and days remaining.

Status per certificate (and for the scan, the worst of them):
    fail     the certificate has expired, or expires within the critical threshold
             (an incident is opened after the monitor's consecutive failures);
    warning  it expires within the warning threshold;
    pass     otherwise.
A handshake that fails on a TLS port is a fail. Protocol versions and cipher suites are not
checked here; a plugin can add a separate monitor for them.

Entry in monitors.yaml:
    config:
        timeout: 10                # TLS handshake timeout per port (seconds)
    thresholds:
        days_remaining:
            warning: 30
            critical: 7

Requires no extra packages: stdlib ssl, psutil (used by other monitors too) and
cryptography (to read certificates the handshake accepts without verification).
"""
import logging
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psutil

from .base_monitor import BaseMonitor

# cryptography parses the DER certificate from getpeercert(binary_form=True): getpeercert()
# returns {} with CERT_NONE, which is needed to reach self-signed and expired certificates.
try:
    from cryptography import x509 as _x509
    from cryptography.hazmat.backends import default_backend as _default_backend
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False

logger = logging.getLogger(__name__)

__version__ = "2.1.0"

# Ports probed at the same time.
PROBE_WORKERS = 16

# Handshake errors meaning "this port does not speak TLS" (not a failure).
_NO_TLS_MARKERS = (
    'WRONG_VERSION_NUMBER', 'UNKNOWN_PROTOCOL', 'HTTP_REQUEST', 'UNEXPECTED_EOF_WHILE_READING',
    'EOF occurred in violation of protocol', 'wrong version number', 'unknown protocol',
    'unexpected eof',
)
_RANK = {'pass': 0, 'warning': 1, 'fail': 2, 'error': 3}


def expiry_status(days_remaining: int, warn_days: int, crit_days: int) -> str:
    """Status of a certificate from its days remaining.

    Args:
        days_remaining: Whole days until expiry (negative when expired).
        warn_days: Warning threshold in days.
        crit_days: Critical threshold in days.

    Returns:
        'fail' when expired or within the critical threshold, 'warning' within the warning
        threshold, else 'pass'.
    """
    if days_remaining < 0 or days_remaining <= crit_days:
        return 'fail'
    if days_remaining <= warn_days:
        return 'warning'
    return 'pass'


class SSLMonitor(BaseMonitor):
    """Monitor the certificates on every listening TLS port of the local server."""

    UNIT = "days"
    # Run-time allowance for the whole scan (the collector's default is 30 s); `timeout` in
    # the monitor config is the TLS handshake timeout per port.
    RUN_TIMEOUT = 180

    def check(self) -> Dict[str, Any]:
        """Scan the listening ports and evaluate every certificate found.

        Returns:
            v1.0 envelope: value = the lowest days remaining (-1 when a TLS port failed the
            handshake and no certificate was read, 0 when no TLS port was found); details
            carry the per-port results.
        """
        try:
            timeout    = int(self.extra_config.get('timeout', 10))
            thresholds = self.threshold_config.get('days_remaining', {})
            warn_days  = int(thresholds.get('warning', 30))
            crit_days  = int(thresholds.get('critical', 7))
            scanned_at = datetime.now(timezone.utc).isoformat()

            listening = self._get_listening_ports()
            # Ports are probed in parallel: a host can have dozens of listening ports, and a
            # port that accepts TCP but never answers TLS costs a full timeout.
            with ThreadPoolExecutor(max_workers=max(1, min(PROBE_WORKERS, len(listening)))) as pool:
                certificates: List[Dict] = list(pool.map(
                    lambda p: self._probe_port(p[0], p[1], p[2], timeout, warn_days, crit_days),
                    listening))
            failure_lines: List[str] = [self._format_failure_line(r) for r in certificates
                                        if r.get('ssl') and r.get('status') in ('fail', 'warning')]

            overall = 'pass'
            for cert in certificates:
                if _RANK.get(cert.get('status', 'pass'), 0) > _RANK[overall]:
                    overall = cert['status']

            days_values = [c['days_remaining'] for c in certificates
                           if c.get('ssl') and c.get('days_remaining') is not None]
            if days_values:
                value = min(days_values)
            elif any(c.get('ssl') for c in certificates):
                value = -1
            else:
                value = 0

            details = {
                'scanned_at':    scanned_at,
                'scanned_ports': len(listening),
                'ssl_ports':     sum(1 for c in certificates if c.get('ssl')),
                'certificates':  certificates,
            }
            failure_reason = "; ".join(failure_lines) or None
            if failure_reason:
                details['failure_reason'] = failure_reason
            return self.create_result(name='ssl_scan', value=float(value), status=overall,
                                      details=details)
        except Exception as e:
            return self.handle_error(e)

    # ── Port discovery ────────────────────────────────────────────────────

    def _get_listening_ports(self) -> List[Tuple[str, int, str]]:
        """
        Return list of (connect_ip, port, bind_ip) for all listening TCP sockets.

        Returns:
            Sorted by port.
        connect_ip is the address to actually connect to:
          - specific IP  → use that IP
          - 0.0.0.0      → use 127.0.0.1
          - ::            → use ::1
          - 127.x.x.x    → use as-is
        Deduplicates by port when the same port is bound on multiple interfaces.
        """
        seen_ports = set()
        results    = []

        try:
            conns = psutil.net_connections(kind='inet')
        except psutil.AccessDenied:
            # Fall back to inet4 only if inet raises permission error
            conns = psutil.net_connections(kind='inet4')

        for conn in conns:
            if conn.status != psutil.CONN_LISTEN:
                continue
            if not conn.laddr:
                continue

            bind_ip = conn.laddr.ip
            port    = conn.laddr.port

            if port in seen_ports:
                continue
            seen_ports.add(port)

            # Determine which IP to actually connect to
            if bind_ip in ('0.0.0.0', ''):
                connect_ip = '127.0.0.1'
            elif bind_ip == '::':
                connect_ip = '::1'
            else:
                connect_ip = bind_ip

            results.append((connect_ip, port, bind_ip))

        return sorted(results, key=lambda x: x[1])

    # ── Per-port probe ────────────────────────────────────────────────────

    def _probe_port(self, connect_ip: str, port: int, bind_ip: str, timeout: int,
                    warn_days: int, crit_days: int) -> Dict[str, Any]:
        """Handshake with one port and evaluate its certificate.

        Args:
            connect_ip: Address to connect to.
            port: TCP port.
            bind_ip: Address the service listens on (reported).
            timeout: Socket timeout in seconds.
            warn_days: Warning threshold in days.
            crit_days: Critical threshold in days.

        Returns:
            Result dict: 'ssl' False for a port without TLS; otherwise the certificate
            fields, 'cert_status' and 'status'.
        """
        base = {'ip': bind_ip, 'port': port}
        try:
            with socket.create_connection((connect_ip, port), timeout=timeout):
                pass
        except (ConnectionRefusedError, socket.timeout, OSError):
            return {**base, 'ssl': False, 'status': 'pass', 'note': 'port not accepting connections'}

        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode    = ssl.CERT_NONE
            af = socket.AF_INET6 if ':' in connect_ip else socket.AF_INET
            raw_sock = socket.socket(af, socket.SOCK_STREAM)
            raw_sock.settimeout(timeout)
            raw_sock.connect((connect_ip, port))
            with ctx.wrap_socket(raw_sock, server_hostname=connect_ip) as tls_sock:
                der_cert = tls_sock.getpeercert(binary_form=True)
        except ssl.SSLError as e:
            if any(marker in str(e) for marker in _NO_TLS_MARKERS):
                return {**base, 'ssl': False, 'status': 'pass'}
            return {**base, 'ssl': True, 'cert_status': 'fail', 'status': 'fail',
                    'error': f"SSL handshake failed: {e}"}
        except (socket.timeout, ConnectionRefusedError, OSError):
            return {**base, 'ssl': False, 'status': 'pass'}

        try:
            return self._parse_certificate(base, der_cert, connect_ip, port, warn_days, crit_days)
        except Exception as parse_err:
            logger.warning(f"Certificate parse error on {connect_ip}:{port}: {parse_err}")
            return {**base, 'ssl': True, 'cert_status': 'fail', 'status': 'fail',
                    'error': f"Certificate parse error: {parse_err}"}

    def _parse_certificate(self, base: Dict, der_cert: Optional[bytes], connect_ip: str, port: int,
                           warn_days: int, crit_days: int) -> Dict[str, Any]:
        """Read subject, issuer and validity from a DER certificate and rate its expiry.

        Args:
            base: {'ip', 'port'} of the probed port.
            der_cert: Certificate bytes from the handshake.
            connect_ip: Address (for log messages).
            port: Port (for log messages).
            warn_days: Warning threshold in days.
            crit_days: Critical threshold in days.

        Returns:
            Result dict with subject, issuer, valid_from/valid_to (ISO), days_remaining,
            cert_status and status. Unreadable dates count as expired (days_remaining -1).
        """
        subject = issuer = 'unknown'
        valid_from = valid_to = None
        if der_cert and _CRYPTO_AVAILABLE:
            try:
                cert = _x509.load_der_x509_certificate(der_cert, _default_backend())
                subject = self._cn_from_name(cert.subject)
                issuer  = self._cn_from_name(cert.issuer)
                try:                                     # cryptography >= 42
                    valid_from, valid_to = cert.not_valid_before_utc, cert.not_valid_after_utc
                except AttributeError:                   # older: naive UTC datetimes
                    valid_from = cert.not_valid_before.replace(tzinfo=timezone.utc)
                    valid_to   = cert.not_valid_after.replace(tzinfo=timezone.utc)
            except Exception as parse_err:
                logger.warning(f"DER cert parse error on {connect_ip}:{port}: {parse_err}")
        elif der_cert:
            logger.warning("cryptography library not installed - cannot read certificate details")

        days_remaining = (valid_to - datetime.now(timezone.utc)).days if valid_to else -1
        status = expiry_status(days_remaining, warn_days, crit_days)
        return {
            **base,
            'ssl':            True,
            'subject':        subject,
            'issuer':         issuer,
            'valid_from':     valid_from.isoformat() if valid_from else None,
            'valid_to':       valid_to.isoformat() if valid_to else None,
            'days_remaining': days_remaining,
            'cert_status':    status,
            'status':         status,
        }

    # ── Helpers ───────────────────────────────────────────────────────────

    def _cn_from_name(self, name) -> str:
        """Extract CN from a cryptography x509.Name, falling back to full string."""
        try:
            from cryptography.x509.oid import NameOID
            cn = name.get_attributes_for_oid(NameOID.COMMON_NAME)
            if cn:
                return f"CN={cn[0].value}"
            # Fall back to full RFC4514 representation
            return name.rfc4514_string()
        except Exception:
            return str(name)

    def _format_failure_line(self, cert: Dict) -> str:
        """One line describing a failing or warning certificate.

        Args:
            cert: Result dict of one port.

        Returns:
            Human-readable line for the scan's failure_reason.
        """
        ip, port = cert.get('ip', '?'), cert.get('port', '?')
        if cert.get('error'):
            return f"{ip}:{port} {cert['error']}"
        days = cert.get('days_remaining')
        if days is not None and days < 0:
            return f"{ip}:{port} certificate EXPIRED {abs(days)} days ago"
        if cert.get('cert_status') == 'fail':
            return f"{ip}:{port} certificate expires in {days} days (critical threshold)"
        return f"{ip}:{port} certificate expires in {days} days (warning threshold)"
