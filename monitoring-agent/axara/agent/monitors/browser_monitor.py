# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Browser Monitor
Runs a local Playwright or Selenium script and reports pass/fail
based on the script's exit code and output.

The script is expected to:
  - Exit with code 0 on success
  - Exit with non-zero code on failure
  - Write a brief result message to stdout

Required entry in monitors.yaml config section:
    config:
        script_path: "scripts/checkout_flow.py"  # relative to agent root or absolute
        timeout: 120                              # optional, default 120s
        python_executable: "python"              # optional; default: the agent's own runtime

Thresholds (optional):
    thresholds:
        duration_ms:
            warning: 5000
            critical: 10000

Runtime:
    The compiled Axara agent runs scripts with its built-in Python and Playwright
    (`axara-agent --run-script`); Chromium is installed with `axara-agent install --with-browser`.
    A source checkout runs them with the agent's Python (pip install playwright; playwright install chromium).
"""
import json
import os
import subprocess
import time
import platform
import sys
from pathlib import Path
from typing import Dict, Any
from .base_monitor import BaseMonitor
import logging

logger = logging.getLogger(__name__)

__version__ = "1.2.0"


class BrowserMonitor(BaseMonitor):
    """
    Monitor that executes a browser automation script locally and
    reports pass/fail based on exit code.
    """

    UNIT = "ms"

    def check(self) -> Dict[str, Any]:
        """Run the configured Playwright journey script and report its outcome.

        The script runs in a child process (the compiled agent's --run-script mode, or a Python
        interpreter) with the configured timeout; its JSON output becomes the result details.

        Returns:
            The monitor result (pass, fail, timeout or error).
        """
        script_path = self.extra_config.get('script_path')
        if not script_path:
            return self.create_result(
                name='browser_check',
                value=0,
                status='error',
                details={'error': "'script_path' not set in monitor config.config"}
            )

        timeout          = int(self.extra_config.get('timeout', 120))
        python_executable = self.extra_config.get('python_executable')
        agent_exe         = os.environ.get('AXARA_AGENT_EXE')   # set by the compiled agent

        # Resolve script path — support both absolute and relative paths.
        # Relative paths are resolved from the agent's folder: AXARA_AGENT_HOME (set by the
        # agent launcher), else three levels above this file (<agent>/axara/agent/monitors/).
        resolved = Path(script_path)
        if not resolved.is_absolute():
            agent_root = Path(os.environ.get('AXARA_AGENT_HOME') or Path(__file__).resolve().parents[3])
            resolved = agent_root / script_path

        if not resolved.exists():
            return self.create_result(
                name='browser_check',
                value=0,
                status='error',
                details={
                    'script_path':  str(script_path),
                    'resolved':     str(resolved),
                    'error':        f"Script not found: {resolved}",
                }
            )

        start = time.time()
        try:
            if python_executable:
                cmd = [python_executable, str(resolved)]
            elif agent_exe:
                cmd = [agent_exe, '--run-script', str(resolved)]
            else:
                cmd = [sys.executable, str(resolved)]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            duration_ms = round((time.time() - start) * 1000, 1)

            passed  = proc.returncode == 0
            # Parse stdout as JSON if possible (scripts that use our
            # result-dict pattern). Falls back to raw string output.
            stdout_raw = proc.stdout.strip()
            stdout_json: dict = {}
            try:
                # Scripts may print non-JSON lines before the JSON result
                # (e.g. the OTP print statement). Find the last JSON object.
                for line in reversed(stdout_raw.splitlines()):
                    line = line.strip()
                    if line.startswith('{'):
                        stdout_json = json.loads(line)
                        break
            except (json.JSONDecodeError, ValueError):
                pass
            output  = stdout_raw or proc.stderr.strip()

            if passed:
                status = self.evaluate_threshold(
                    duration_ms,
                    self.threshold_config.get('duration_ms', {})
                )
            else:
                status = 'fail'

            consec   = self.config.get('consecutive_failures', 3)
            dt_thresh = self.threshold_config.get('duration_ms', {})
            crit = dt_thresh.get('critical')
            warn = dt_thresh.get('warning')
            failure_reason = None
            if not passed:
                err_text = (proc.stderr.strip() or proc.stdout.strip())[:200]
                failure_reason = (
                    f"Browser journey in {self.name} exited with code "
                    f"{proc.returncode} consecutively {consec} times — {err_text}"
                )
            elif status == 'fail' and crit and duration_ms >= crit:
                failure_reason = (
                    f"Browser journey in {self.name} with duration {duration_ms}ms exceeded "
                    f"consecutively {consec} times critical threshold of {crit}ms"
                )
            elif status == 'warning' and warn and duration_ms >= warn:
                failure_reason = (
                    f"Browser journey in {self.name} with duration {duration_ms}ms exceeded "
                    f"consecutively {consec} warning threshold of {warn}ms"
                )

            # Build details dict.
            # screenshot_b64 is lifted from stdout_json to a TOP-LEVEL key so
            # state_machine.py can find it directly without parsing truncated JSON.
            # stdout is stored WITHOUT the base64 blob (replaced with a placeholder)
            # to keep the stored metric_data small and parseable downstream.
            stdout_clean = stdout_raw
            if stdout_json.get('screenshot_b64'):
                # Rebuild the JSON without screenshot_b64 so stored stdout is compact
                clean_dict = {k: v for k, v in stdout_json.items() if k != 'screenshot_b64'}
                import json as _json
                stdout_clean = _json.dumps(clean_dict)

            details = {
                'script_path':  str(resolved),
                'exit_code':    proc.returncode,
                'duration_ms':  duration_ms,
                # stdout stored without screenshot_b64 — stays well under any size limit
                'stdout':       stdout_clean[:2000],
                'stderr':       proc.stderr.strip()[:500],
                'platform':     platform.system(),
            }

            # Promote key fields from stdout JSON to top-level detail keys so
            # state_machine and llm_analyzer can read them without parsing stdout.
            if stdout_json.get('step'):
                details['step']   = stdout_json['step']
            if stdout_json.get('error'):
                details['error']  = stdout_json['error']
            if stdout_json.get('status'):
                details['script_status'] = stdout_json['status']

            # screenshot_b64 as top-level key — state_machine reads this directly
            screenshot = stdout_json.get('screenshot_b64')
            if screenshot and not screenshot.startswith('screenshot_failed'):
                details['screenshot_b64'] = screenshot

            # NOTE (Option 1): screenshot_b64 remains in details ON THE WIRE so the
            # server state machine can save the PNG. metrics.py strips it before DB
            # persistence and the stored row carries screenshot_ref instead.
            return self.create_result(
                name='browser_duration_ms',
                value=duration_ms,
                status=status,
                failure_reason=failure_reason,
                details=details
            )

        except subprocess.TimeoutExpired:
            duration_ms = round((time.time() - start) * 1000, 1)
            return self.create_result(
                name='browser_check',
                value=duration_ms,
                status='fail',
                details={
                    'script_path': str(resolved),
                    'error':       f"Script timed out after {timeout}s",
                    'duration_ms': duration_ms,
                }
            )
        except Exception as e:
            return self.handle_error(e)
