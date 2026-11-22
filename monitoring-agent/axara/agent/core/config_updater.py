# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Config Updater
Polls the central server for changes to monitors.yaml and downloads
the updated file when the server-side checksum differs from the local one.

Pattern mirrors ScriptUpdater exactly:
  1. Compute local SHA-256 of monitors/config/monitors.yaml
  2. GET /api/v1/config/monitors-yaml/{server_id}/checksum from server
  3. If checksums match — nothing to do
  4. If different  — download full content, verify checksum, backup, overwrite
  5. Return True so agent.py can raise MonitorConfigUpdated and restart cleanly
"""

import hashlib
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class ConfigUpdater:
    """
    Manages monitors.yaml version checking and updates.

    Called from the monitoring loop at the same cadence as ScriptUpdater
    (every agent check_interval_seconds seconds).  Returns True when the local
    monitors.yaml has been replaced, signalling the agent to restart its
    monitoring loop cleanly with the new configuration.
    """

    def __init__(self, agent):
        """
        Args:
            agent: MonitoringAgent instance — provides config_dir, server_id,
                   and api_sender (for base_url / api_key / timeout).
        """
        self.agent       = agent
        self.config_dir  = agent.config_dir
        self.monitors_file = self.config_dir / "monitors.yaml"
        self.backup_dir    = self.config_dir / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    # Helpers

    def _local_checksum(self) -> Optional[str]:
        """SHA-256 of the local monitors.yaml in binary mode. None if file absent."""
        if not self.monitors_file.exists():
            return None
        h = hashlib.sha256()
        with open(self.monitors_file, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return f"sha256:{h.hexdigest()}"

    def _content_checksum(self, content: str) -> str:
        """SHA-256 of a string as UTF-8 bytes."""
        return f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"

    def _get(self, path: str) -> Optional[dict]:
        """
        Thin HTTP GET wrapper that reuses the agent's base_url, api_key,
        and timeout settings — consistent with how APISender works.
        """
        try:
            sender      = self.agent.api_sender
            base_url    = sender.base_url.rstrip("/")
            headers     = {"Content-Type": "application/json"}
            api_key     = getattr(sender, "api_key", None)
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            timeout = getattr(sender, "timeout", 30)

            # Reuse the session's ssl_verify setting (False for self-signed certs).
            # requests.get() defaults to verify=True, ignoring session.verify,
            # which causes CERTIFICATE_VERIFY_FAILED on self-signed server certs.
            ssl_verify = getattr(getattr(sender, 'session', None), 'verify', True)
            response = requests.get(
                f"{base_url}{path}", headers=headers, timeout=timeout,
                verify=ssl_verify,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.warning(f"ConfigUpdater GET {path} failed: {e}")
            return None

    def _backup(self):
        """Keep a timestamped copy of monitors.yaml before overwriting."""
        if not self.monitors_file.exists():
            return
        ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst     = self.backup_dir / f"monitors.yaml.{ts}.bak"
        shutil.copy2(self.monitors_file, dst)
        logger.debug(f"Backed up monitors.yaml → {dst.name}")

    # Public API

    def check_and_update_config(self) -> bool:
        """
        Check whether the server's monitors.yaml differs from the local copy
        and, if so, download and install the new version.

        Single-request design: fetches content AND checksum in one HTTP call
        so there is no race window between a checksum probe and a content fetch.
        If the server regenerates the file between two requests (e.g. on
        heartbeat), the two-request approach would permanently loop because the
        locally-written content never matches the checksum from the first call.

        Returns:
            True  — monitors.yaml was updated; caller should restart the loop.
            False — no change, or check/download failed (safe to continue).
        """
        logger.info("Checking monitors.yaml version with central server…")

        try:
            local_checksum = self._local_checksum()

            # Single request: content + checksum atomically
            resp = self._get(
                f"/api/v1/config/monitors-yaml/{self.agent.server_id}"
            )
            if not resp:
                logger.warning(
                    "monitors.yaml check skipped — server unreachable"
                )
                return False

            server_checksum = resp.get("checksum", "")
            content         = resp.get("content", "")

            # Compare against local
            if server_checksum == local_checksum:
                logger.info("monitors.yaml is up to date")
                return False

            logger.info(
                f"monitors.yaml change detected"
            )

            # Verify content integrity
            actual_checksum = self._content_checksum(content)
            if actual_checksum != server_checksum:
                logger.error(
                    f"Checksum mismatch for monitors.yaml — aborting update\n"
                    f"  expected : {server_checksum}\n"
                    f"  computed : {actual_checksum}"
                )
                return False

            # Backup and install
            # Write as raw UTF-8 bytes so _local_checksum (binary read) produces
            # exactly the same hash as _content_checksum (string.encode).
            self._backup()
            self.monitors_file.write_bytes(content.encode("utf-8"))

            logger.info(
                "monitors.yaml updated successfully — "
                "monitoring loop will restart cleanly"
            )
            return True

        except Exception as e:
            logger.error(f"monitors.yaml update check failed: {e}", exc_info=True)
            return False
