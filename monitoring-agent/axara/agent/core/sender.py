# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
API Sender
Handles communication with central server
"""
import json
import os
import requests
from pathlib import Path
from typing import Dict, Any, List, Optional
import logging
import time

logger = logging.getLogger(__name__)


class _Session(requests.Session):
    """requests session whose configured `verify` wins over REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE.

    Plain requests lets those environment variables replace session.verify, so on a host that
    sets them (common behind corporate proxies) the agent would ignore its own server CA and
    fail, or trust a different CA. Proxy settings from the environment still apply.
    """

    def merge_environment_settings(self, url, proxies, stream, verify, cert):
        """Merge as requests does, then restore the session's own verify setting.

        Args:
            url: Request URL.
            proxies: Request proxies.
            stream: Stream flag.
            verify: Request-level verify (None when not given).
            cert: Client certificate.

        Returns:
            Settings dict for the request.
        """
        settings = super().merge_environment_settings(url, proxies, stream, verify, cert)
        if verify is None:
            settings["verify"] = self.verify
        return settings


class APISender:
    """
    Sends metrics and communicates with central server
    """
    
    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        retry_attempts: int = 3,
        timeout: int = 30,
        ssl_verify: bool = True,
    ):
        """
        Initialize API sender
        
        Args:
            base_url: Central server URL
            api_key: Authentication API key
            retry_attempts: Number of retry attempts for failed requests
            timeout: Request timeout in seconds
            ssl_verify: Verify SSL certificates. Set False for self-signed certs.
                        Can also be a path to a CA bundle file.
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.retry_attempts = retry_attempts
        self.timeout = timeout
        # Set by send_metrics: True when the last failure was a permanent 4xx
        # (e.g. 422 validation) so replay_and_send can drop the bad payload.
        self.last_send_permanent = False
        self.session = _Session()
        self.session.verify = ssl_verify  # applies to every request in the session
        
        if api_key:
            self.session.headers.update({
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            })

        # Offline queue — buffers payloads when the server is unreachable
        # and replays them FIFO on the next successful connection.
        self.queue_dir      = Path(
            os.getenv('AGENT_QUEUE_DIR', './queue')
        )
        self.queue_max_files = int(
            os.getenv('AGENT_QUEUE_MAX_FILES', '1440')
        )
        self.queue_dir.mkdir(parents=True, exist_ok=True)
    
    # Offline queue

    def ping(self) -> bool:
        """
        Lightweight liveness check against GET /health.
        Uses a short timeout (5 s) so a dead server does not stall the loop.

        Returns:
            True if the server responded with HTTP 200.
        """
        try:
            resp = self.session.get(
                f'{self.base_url}/health',
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def _queue_sorted_files(self) -> List[Path]:
        """Return queue files sorted oldest-first by filename (epoch_ms prefix)."""
        return sorted(self.queue_dir.glob('*.json'))

    def queue_metrics(self, server_id: str, metrics: List[Dict[str, Any]]) -> None:
        """
        Persist a metric payload to the offline queue.

        Filename format: {epoch_ms}_{server_id[:8]}.json
        Alphabetical sort = chronological order = FIFO replay.

        If the queue is at capacity (AGENT_QUEUE_MAX_FILES), the oldest
        file is deleted before writing the new one (ring-buffer behaviour).
        """
        payload = {
            'server_id': server_id,
            'timestamp': metrics[0]['timestamp'] if metrics else None,
            'metrics':   metrics,
        }
        ts_ms     = int(time.time() * 1000)
        filename  = f'{ts_ms}_{server_id[:8]}.json'
        file_path = self.queue_dir / filename

        # Enforce cap — drop oldest entry if at limit
        existing = self._queue_sorted_files()
        while len(existing) >= self.queue_max_files:
            try:
                existing[0].unlink()
                logger.warning(
                    f'Queue cap ({self.queue_max_files}) reached — '
                    f'dropped oldest entry: {existing[0].name}'
                )
            except OSError:
                pass
            existing = self._queue_sorted_files()

        try:
            file_path.write_text(json.dumps(payload), encoding='utf-8')
            logger.info(
                f'Queued {len(metrics)} metric(s) → {filename} '
                f'(queue depth: {len(existing) + 1})'
            )
        except OSError as e:
            logger.error(f'Failed to write queue file {filename}: {e}')

    def replay_and_send(
        self,
        server_id: str,
        current_metrics: List[Dict[str, Any]],
    ) -> bool:
        """
        Replay all queued payloads FIFO then send the current batch.

        Flow:
          1. For each queued file (oldest first): send → delete on success.
          2. If a queued send fails: queue all remaining files' metrics plus
             the current batch and return False (server still down).
          3. If all queued files replayed: send current batch normally.
             If that fails: queue current batch and return False.

        Returns:
            True if the current batch was delivered successfully.
        """
        queued_files = self._queue_sorted_files()

        if queued_files:
            logger.info(
                f'Replaying {len(queued_files)} queued payload(s) before '
                f'sending current batch…'
            )

        for idx, qfile in enumerate(queued_files):
            try:
                payload = json.loads(qfile.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f'Skipping corrupt queue file {qfile.name}: {e}')
                qfile.unlink(missing_ok=True)
                continue

            ok = self.send_metrics(
                payload['server_id'],
                payload['metrics'],
                is_replay=True,
            )

            if ok:
                qfile.unlink(missing_ok=True)
                logger.debug(f'Replayed and deleted {qfile.name}')
            elif self.last_send_permanent:
                # Permanent rejection (4xx, e.g. 422 validation): this payload will
                # NEVER be accepted, so re-queuing it would block the queue forever.
                # Drop it and continue replaying the rest.
                logger.error(
                    f'Dropping permanently-rejected queue file {qfile.name} '
                    f'(server returned 4xx — payload is malformed and cannot be '
                    f'delivered). Continuing replay of remaining files.'
                )
                qfile.unlink(missing_ok=True)
                continue
            else:
                # Transient failure (server down / 5xx): keep everything queued
                # and give up for this pass — the server is unreachable/erroring.
                logger.warning(
                    f'Replay failed at {qfile.name} (transient) — '
                    f'queuing {len(queued_files) - idx} remaining file(s) '
                    f'and current batch'
                )
                self.queue_metrics(server_id, current_metrics)
                return False

        # All queued files replayed — now send the current batch
        if not self.send_metrics(server_id, current_metrics):
            # Only re-queue on transient failure; a permanent 4xx on the current
            # batch means it's malformed and re-queuing won't help (log and drop).
            if self.last_send_permanent:
                logger.error(
                    'Current batch permanently rejected (4xx) — not queuing. '
                    'Check the server validation error above.'
                )
                return False
            self.queue_metrics(server_id, current_metrics)
            return False

        return True

    # Registration

    def register_agent(self, registration_data: Dict, enrolment_token: Optional[str] = None) -> Optional[Dict]:
        """
        Register agent with central server. A new registration needs an enrolment token
        (created by an administrator in the dashboard); it is sent in a header and never logged.
        
        Args:
            registration_data: Registration payload
            enrolment_token:   Enrolment token for this registration
            
        Returns:
            Registration response or None if failed (reason in self.last_error)
        """
        url = f"{self.base_url}/api/v1/agents/register"
        self.last_error = None
        headers = {'X-Axara-Enrolment-Token': enrolment_token} if enrolment_token else {}
        
        try:
            logger.debug(f"Registering agent with central server...registration_data: {str(registration_data)}")
            response = self.session.post(
                url,
                json=registration_data,
                headers=headers,
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"Registration successful. Server ID: {result.get('server_id')}")
                return result
            else:
                try:
                    detail = response.json().get('detail')
                    message = detail.get('message') if isinstance(detail, dict) else detail
                except Exception:
                    message = response.text[:300]
                self.last_error = f"{response.status_code}: {message}"
                logger.error(f"Registration failed: {self.last_error}")
                return None
                
        except requests.exceptions.RequestException as e:
            self.last_error = f"server not reachable ({e})"
            logger.error(f"Registration request failed: {e}")
            return None
    
    def send_metrics(
        self,
        server_id: str,
        metrics: List[Dict[str, Any]],
        is_replay: bool = False,
    ) -> bool:
        """
        Send metrics to central server.

        Returns True on success (HTTP 200). On failure returns False.

        Failure handling distinguishes PERMANENT client errors (HTTP 4xx — e.g. a
        422 validation rejection that will never succeed on retry) from TRANSIENT
        errors (HTTP 5xx or connection failures — server down/overloaded). On a 4xx
        we do NOT retry (it's wasted) and we record it on self.last_send_permanent
        so replay_and_send can DROP the offending queued payload rather than
        re-queue it forever. On transient errors we retry with backoff as before.
        """
        url = f"{self.base_url}/api/v1/metrics/"

        payload = {
            'server_id': server_id,
            'timestamp': metrics[0]['timestamp'] if metrics else None,
            'metrics': metrics,
            'is_replay': is_replay,
        }
        # Reset per-call permanent-failure flag; replay_and_send reads it after.
        self.last_send_permanent = False
        logger.debug(f"Sending metric data: {payload}")
        for attempt in range(self.retry_attempts):
            try:
                response = self.session.post(
                    url,
                    json=payload,
                    timeout=self.timeout
                )

                if response.status_code == 200:
                    logger.debug(f"Metrics sent successfully ({len(metrics)} metrics)")
                    return True

                # Permanent client error (4xx): will not succeed on retry. Stop now
                # and signal permanence so the caller can drop this payload.
                if 400 <= response.status_code < 500:
                    self.last_send_permanent = True
                    logger.error(
                        f"Metrics rejected (HTTP {response.status_code}, permanent — "
                        f"not retrying): {response.text[:300]}"
                    )
                    return False

                # Transient (5xx) — fall through to retry/backoff.
                logger.warning(
                    f"Metrics send failed (attempt {attempt + 1}/{self.retry_attempts}): "
                    f"{response.status_code} - {response.text}"
                )

            except requests.exceptions.RequestException as e:
                logger.warning(
                    f"Metrics send error (attempt {attempt + 1}/{self.retry_attempts}): {e}"
                )

            # Wait before retry (exponential backoff)
            if attempt < self.retry_attempts - 1:
                wait_time = 2 ** attempt  # 1s, 2s, 4s
                logger.debug(f"Waiting {wait_time}s before retry...")
                time.sleep(wait_time)

        logger.error(f"Failed to send metrics after {self.retry_attempts} attempts")
        return False
    
    def check_script_versions(
        self,
        server_id: str,
        local_scripts: List[Dict]
    ) -> Optional[Dict]:
        """
        Check if monitor scripts need updating
        
        Args:
            server_id: Server UUID
            local_scripts: List of local script info
            
        Returns:
            Update info or None if failed
        """
        url = f"{self.base_url}/api/v1/agents/check-scripts"
        
        payload = {
            'server_id': server_id,
            'local_scripts': local_scripts
        }
        
        try:
            response = self.session.post(
                url,
                json=payload,
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"Script version check failed: {response.status_code}")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.warning(f"Script version check error: {e}")
            return None
    
    def download_script(
        self,
        script_name: str,
        version: str
    ) -> Optional[str]:
        """
        Download monitor script from server
        
        Args:
            script_name: Script filename
            version: Script version
            
        Returns:
            Script content or None if failed
        """
        url = f"{self.base_url}/api/v1/scripts/{script_name}/{version}"
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            
            if response.status_code == 200:
                return response.json().get('content')
            else:
                logger.error(f"Script download failed: {response.status_code}")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Script download error: {e}")
            return None
    
    def send_heartbeat(self, server_id: str) -> bool:
        """
        Send heartbeat to indicate agent is alive
        
        Args:
            server_id: Server UUID
            
        Returns:
            True if successful
        """
        url = f"{self.base_url}/api/v1/agents/heartbeat"
        
        try:
            response = self.session.post(
                url,
                json={'server_id': server_id},
                timeout=self.timeout
            )
            return response.status_code == 200
        except requests.exceptions.RequestException:
            return False
