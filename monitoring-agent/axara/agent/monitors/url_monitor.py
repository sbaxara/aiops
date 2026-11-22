# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
URL Monitor
Checks HTTP/HTTPS endpoint availability, status code, and optional body content.

Required entry in monitors.yaml config section:
    config:
        url: "https://example.com/health"
        method: "GET"               # optional, default GET
        expected_status: 200        # optional, default 200
        expected_body_contains: ""  # optional, omit to skip body check
        timeout: 10                 # optional, default 10s
        verify_ssl: true            # optional, default true

Thresholds (optional):
    thresholds:
        response_time_ms:
            warning: 1000
            critical: 3000
"""
import time
import requests
from typing import Dict, Any
from .base_monitor import BaseMonitor
import logging

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

class UrlMonitor(BaseMonitor):
    """
    Monitor HTTP/HTTPS endpoint availability and response.
    Checks status code, optional body content, and response time.
    """

    UNIT = "ms"

    def check(self) -> Dict[str, Any]:
        """Request the configured URL and check its status code, body and response time.

        Returns:
            The monitor result: pass, warning (slow) or fail.
        """
        url = self.extra_config.get('url')
        if not url:
            return self.create_result(
                name='url_check',
                value=0,
                status='error',
                details={'error': "'url' not set in monitor config.config"}
            )

        method             = self.extra_config.get('method', 'GET').upper()
        expected_status    = self.extra_config.get('expected_status', 200)
        expected_body      = self.extra_config.get('expected_body_contains')
        timeout            = self.extra_config.get('timeout', 10)
        verify_ssl         = self.extra_config.get('verify_ssl', True)

        start = time.time()
        try:
            resp = requests.request(
                method,
                url,
                timeout=timeout,
                verify=verify_ssl,
                allow_redirects=True,
            )
            response_time_ms = round((time.time() - start) * 1000, 1)

            status_ok = resp.status_code == expected_status
            body_ok   = True
            if expected_body:
                body_ok = expected_body in resp.text

            passed = status_ok and body_ok

            # Determine status: fail on wrong code/body, then check response time thresholds
            if not passed:
                status = 'fail'
            else:
                status = self.evaluate_threshold(
                    response_time_ms,
                    self.threshold_config.get('response_time_ms', {})
                )

            consec = self.config.get('consecutive_failures', 3)
            reasons = []
            if not status_ok:
                reasons.append(
                    f"HTTP status code {resp.status_code} in {self.name} did not match "
                    f"expected {expected_status} consecutively {consec} times"
                )
            if not body_ok:
                reasons.append(
                    f"Response body in {self.name} check failed expected body '{expected_body}' "
                    f"consecutively {consec} times"
                )
            if status in ('fail', 'warning') and passed:
                # Passed content checks but failed on response time threshold
                rt_thresholds = self.threshold_config.get('response_time_ms', {})
                crit = rt_thresholds.get('critical')
                warn = rt_thresholds.get('warning')
                if crit and response_time_ms >= crit:
                    reasons.append(
                        f"Response time {response_time_ms}ms in {self.name} exceeded consecutively "
                        f"{consec} times critical threshold of {crit}ms"
                    )
                elif warn and response_time_ms >= warn:
                    reasons.append(
                        f"Response time {response_time_ms}ms in {self.name} exceeded consecutively "
                        f"{consec} times warning threshold of {warn}ms"
                    )
            failure_reason = f"".join(reasons) if reasons else None

            details = {
                'url':              url,
                'method':           method,
                'http_status':      resp.status_code,
                'expected_status':  expected_status,
                'status_ok':        status_ok,
                'body_ok':          body_ok,
                'response_time_ms': response_time_ms,
                'response_length':  len(resp.text),
            }
            if failure_reason:
                details['failure_reason'] = failure_reason

            return self.create_result(
                name='url_response_time_ms',
                value=response_time_ms,
                status=status,
                details=details
            )

        except requests.exceptions.SSLError as e:
            msg = f"SSL certificate error for {url}: {e}"
            return self.create_result(
                name='url_check', value=0, status='fail',
                details={'url': url, 'error': str(e),
                         'failure_reason': f"{self.name}: {msg}"}
            )
        except requests.exceptions.Timeout:
            response_time_ms = round((time.time() - start) * 1000, 1)
            msg = f"Request timed out after {timeout}s for {url}"
            return self.create_result(
                name='url_check', value=response_time_ms, status='fail',
                details={'url': url, 'error': msg,
                         'failure_reason': f"{self.name}: {msg}"}
            )
        except requests.exceptions.TooManyRedirects:
            msg = f"Redirect loop detected for {url}"
            return self.create_result(
                name='url_check', value=0, status='fail',
                details={'url': url, 'error': msg,
                         'failure_reason': f"{self.name}: {msg}"}
            )
        except requests.exceptions.ConnectionError as e:
            cause = str(e)
            if 'NameResolutionError' in cause or 'getaddrinfo' in cause:
                error_msg = f"DNS resolution failed for {url}"
            elif 'Connection refused' in cause:
                error_msg = f"Connection refused: {url}"
            else:
                error_msg = f"Connection error: {cause}"
            logger.warning(f"URL check connection error: {error_msg}")
            return self.create_result(
                name='url_check', value=0, status='fail',
                details={'url': url, 'error': error_msg,
                         'failure_reason': f"{self.name}: {error_msg}"}
            )
        except Exception as e:
            return self.handle_error(e)
