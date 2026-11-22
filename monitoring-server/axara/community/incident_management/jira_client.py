# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Jira Client
Integration with Jira Service Management using REST API v3.

Uses requests.Session with Basic Auth (email + API token).
All text content uses Atlassian Document Format (ADF) as required by API v3.

Config via environment variables:
    JIRA_URL          e.g. https://yourorg.atlassian.net
    JIRA_USER         your Atlassian account email
    JIRA_API_KEY      API token from id.atlassian.com
    JIRA_PROJECT_KEY  e.g. OPS
"""
import logging
import os
import requests
from typing import Dict, Any, Optional
from requests.exceptions import HTTPError, RequestException

logger = logging.getLogger(__name__)


def _adf(text: str) -> Dict[str, Any]:
    """
    Wrap plain text in minimal Atlassian Document Format (ADF).
    Required by Jira REST API v3 for description and comment body fields.
    """
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}]
            }
        ]
    }


class JiraClient:
    """
    Jira Service Management integration client.
    Creates, comments on, and resolves incidents as Jira issues.
    """

    def __init__(
        self,
        url: str = None,
        user: str = None,
        api_key: str = None,
        project_key: str = None
    ):
        """Create a Jira client; arguments not given come from JIRA_URL, JIRA_USER, JIRA_API_KEY
        and JIRA_PROJECT_KEY.

        Args:
            url: Jira base URL.
            user: Account e-mail.
            api_key: API token.
            project_key: Project for new issues (default OPS).
        """
        self.url         = (url      or os.getenv("JIRA_URL",         "")).rstrip("/")
        self.user        = user      or os.getenv("JIRA_USER")
        self.api_key     = api_key   or os.getenv("JIRA_API_KEY")
        self.project_key = project_key or os.getenv("JIRA_PROJECT_KEY", "OPS")

        self._session: Optional[requests.Session] = None
        self._enabled = bool(self.url and self.user and self.api_key)

        if not self._enabled:
            logger.warning("Jira credentials not configured — integration disabled")
        else:
            self._session = requests.Session()
            self._session.auth = (self.user, self.api_key)
            self._session.headers.update({"Content-Type": "application/json"})
            logger.info(f"Jira client configured: {self.url}")

    # Internal helpers
    def _api(self, path: str) -> str:
        """Full URL of a Jira REST v3 path."""
        return f"{self.url}/rest/api/3{path}"

    def _get(self, path: str) -> Dict:
        """GET a Jira REST path.

        Args:
            path: Path under /rest/api/3.

        Returns:
            Decoded JSON.

        Raises:
            requests.HTTPError: on an error status.
        """
        resp = self._session.get(self._api(path), timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: Dict) -> requests.Response:
        """POST JSON to a Jira REST path.

        Args:
            path: Path under /rest/api/3.
            payload: JSON body.

        Returns:
            The response.

        Raises:
            requests.HTTPError: on an error status.
        """
        resp = self._session.post(self._api(path), json=payload, timeout=30)
        resp.raise_for_status()
        return resp

    # Public interface
    def create_ticket(self, incident: Dict[str, Any]) -> Optional[str]:
        """
        Create a Jira issue for the incident.

        Returns the issue key (e.g. OPS-42) or None on failure.
        """
        if not self._enabled:
            return None

        priority_map = {
            "critical": "Highest",
            "high":     "High",
            "medium":   "Medium",
            "low":      "Low",
        }
        priority = priority_map.get(
            (incident.get("severity") or "medium").lower(), "Medium"
        )

        description_text = self._build_description(incident)

        payload = {
            "fields": {
                "project":           {"key": self.project_key},
                "summary":           (incident.get("title") or "Monitoring Alert")[:255],
                "description":       _adf(description_text),
                "issuetype":         {"name": "[System] Incident"},
                "customfield_10010": "8",
                "priority":          {"name": priority},
            }
        }

        # Add assignee if available (uses accountId in cloud Jira)
        if incident.get("assignee"):
            payload["fields"]["assignee"] = {"name": incident["assignee"]}

        try:
            resp = self._post("/issue", payload)
            key = resp.json()["key"]
            logger.info(f"Created Jira ticket {key} for incident {incident.get('id')}")
            return key
        except HTTPError as e:
            logger.error(
                f"Jira create_ticket HTTP error {e.response.status_code}: "
                f"{e.response.text[:500]}"
            )
            return None
        except RequestException as e:
            logger.error(f"Jira create_ticket failed: {e}")
            return None

    def link_issue(self, ticket_key: str, target_key: str, link_type: str = "Relates") -> bool:
        """
        Create a Jira issue link between two tickets (correlation, design ref
        §6.9.5) — used to express correlation without a fake "Correlated"
        status, since Jira has no such status value.

        API: POST /rest/api/3/issueLink. NOTE: the design reference document
        states PUT for this endpoint — that's incorrect; Jira's real
        issueLink creation endpoint is POST (PUT would 404 against a real
        Jira instance). Implementing the real API; flagging the doc for
        correction separately.

        "Relates" is an undirected link type in Jira's UI regardless of which
        issue is passed as inward vs outward, so no directionality decision
        is needed for the "no primary designated" case (§6.9.5's null-parent
        path) — both directions render identically to the user.

        Returns True on success (Jira returns 201 with no body on success).
        """
        if not self._enabled:
            return False

        payload = {
            "type": {"name": link_type},
            "inwardIssue": {"key": ticket_key},
            "outwardIssue": {"key": target_key},
        }
        try:
            self._post("/issueLink", payload)
            logger.info(f"Linked Jira ticket {ticket_key} <-> {target_key} ({link_type})")
            return True
        except HTTPError as e:
            logger.error(
                f"Jira link_issue HTTP error {e.response.status_code} "
                f"linking {ticket_key} <-> {target_key}: {e.response.text[:300]}"
            )
            return False
        except RequestException as e:
            logger.error(f"Jira link_issue failed linking {ticket_key} <-> {target_key}: {e}")
            return False

    def add_comment(self, ticket_key: str, comment: str) -> bool:
        """
        Add a comment to an existing Jira issue.

        Returns True on success.
        """
        if not self._enabled:
            return False

        payload = {"body": _adf(comment)}

        try:
            self._post(f"/issue/{ticket_key}/comment", payload)
            logger.info(f"Added comment to Jira ticket {ticket_key}")
            return True
        except HTTPError as e:
            logger.error(
                f"Jira add_comment HTTP error {e.response.status_code} "
                f"on {ticket_key}: {e.response.text[:200]}"
            )
            return False
        except RequestException as e:
            logger.error(f"Jira add_comment failed for {ticket_key}: {e}")
            return False

    def resolve_ticket(self, ticket_key: str, resolution: str = None) -> bool:
        """
        Transition issue to Done/Resolved and add a resolution comment.

        Fetches available transitions dynamically — does NOT use a hardcoded ID.
        Returns True on success.
        """
        if not self._enabled:
            return False

        # Add resolution comment first
        if resolution:
            self.add_comment(ticket_key, f"[AUTO-RESOLUTION] Resolving incident as the error has recovered.")

        # Fetch available transitions
        try:
            data = self._get(f"/issue/{ticket_key}/transitions")
            transitions = data.get("transitions", [])
        except (HTTPError, RequestException) as e:
            logger.error(f"Jira resolve_ticket: could not fetch transitions for {ticket_key}: {e}")
            return False

        # Find a Done / Resolved transition
        transition_id = None
        for t in transitions:
            name = t.get("name", "").lower()
            if "done" in name or "resolve" in name or "close" in name:
                transition_id = t["id"]
                logger.debug(f"Using transition '{t['name']}' (id={transition_id}) for {ticket_key}")
                break

        if not transition_id:
            logger.warning(
                f"No Done/Resolved transition found for {ticket_key}. "
                f"Available: {[t['name'] for t in transitions]}"
            )
            return False

        payload = {
            "transition": {"id": transition_id},
            "fields": {"resolution": {"name": "Done"}},
            "update": {
                "comment": [
                    {
                        "add": {
                            "body": _adf(
                                f"[AUTO-RESOLVED] Resolution: "
                                f"{resolution or 'Auto-resolved by monitoring system'}"
                            )
                        }
                    }
                ]
            }
        }

        try:
            resp = self._session.post(
                self._api(f"/issue/{ticket_key}/transitions"),
                json=payload,
                timeout=30,
            )
            # Jira returns 204 No Content on success
            if resp.status_code in (200, 204):
                logger.info(f"Resolved Jira ticket {ticket_key}")
                return True
            resp.raise_for_status()
            return True
        except HTTPError as e:
            logger.error(
                f"Jira resolve_ticket HTTP error {e.response.status_code} "
                f"on {ticket_key}: {e.response.text[:200]}"
            )
            return False
        except RequestException as e:
            logger.error(f"Jira resolve_ticket failed for {ticket_key}: {e}")
            return False

    def update_ticket(
        self,
        ticket_key: str,
        status: str = None,
        assignee: str = None,
        priority: str = None,
    ) -> bool:
        """Update fields and/or transition a Jira issue."""
        if not self._enabled:
            return False

        try:
            fields: Dict[str, Any] = {}
            if assignee:
                fields["assignee"] = {"name": assignee}
            if priority:
                fields["priority"] = {"name": priority}

            if fields:
                resp = self._session.put(
                    self._api(f"/issue/{ticket_key}"),
                    json={"fields": fields},
                    timeout=30,
                )
                resp.raise_for_status()

            if status:
                self._transition_to(ticket_key, status)

            logger.info(f"Updated Jira ticket {ticket_key}")
            return True

        except (HTTPError, RequestException) as e:
            logger.error(f"Jira update_ticket failed for {ticket_key}: {e}")
            return False

    def get_ticket_status(self, ticket_key: str) -> Optional[str]:
        """Return the current status name of a Jira issue."""
        if not self._enabled:
            return None
        try:
            data = self._get(f"/issue/{ticket_key}?fields=status")
            return data["fields"]["status"]["name"]
        except (HTTPError, RequestException, KeyError) as e:
            logger.error(f"Jira get_ticket_status failed for {ticket_key}: {e}")
            return None

    # Private helpers
    def _transition_to(self, ticket_key: str, target_status: str):
        """Transition issue to the named status if a matching transition exists."""
        try:
            data = self._get(f"/issue/{ticket_key}/transitions")
            for t in data.get("transitions", []):
                if t.get("name", "").lower() == target_status.lower():
                    self._session.post(
                        self._api(f"/issue/{ticket_key}/transitions"),
                        json={"transition": {"id": t["id"]}},
                        timeout=30,
                    ).raise_for_status()
                    logger.debug(f"Transitioned {ticket_key} → {target_status}")
                    return
            logger.warning(f"No transition to '{target_status}' found for {ticket_key}")
        except (HTTPError, RequestException) as e:
            logger.error(f"Jira _transition_to failed for {ticket_key}: {e}")

    def add_attachment(self, ticket_key: str, file_path: str) -> bool:
        """
        Attach a file to an existing Jira issue.

        Uses multipart/form-data upload as required by Jira REST API v3.
        The X-Atlassian-Token: no-check header is mandatory — Jira rejects
        attachment uploads without it (CSRF protection bypass for file uploads).

        Args:
            ticket_key:  Jira issue key e.g. OPS-42
            file_path:   Absolute path to the PNG file to attach

        Returns:
            True on success
        """
        if not self._enabled:
            return False

        import os
        if not os.path.isfile(file_path):
            logger.warning(f'Attachment file not found: {file_path}')
            return False

        filename = os.path.basename(file_path)
        url = self._api(f'/issue/{ticket_key}/attachments')

        # Jira requires a Content-Type: multipart/mixed for multipart uploads
        # — requests sets the correct boundary automatically.
        headers = {
            'X-Atlassian-Token': 'no-check',  # mandatory for attachment endpoint
        }

        try:
            with open(file_path, 'rb') as fh:
                resp = requests.post(url,
                                     auth=(self.user, self.api_key),
                                     files={'file': (filename, fh, 'image/png')},
                                     headers=headers,
                                     timeout=60,)

            resp.raise_for_status()
            logger.info(f'Attached {filename} to Jira ticket {ticket_key}')
            return True
        except requests.exceptions.HTTPError as e:
            logger.error(
                f'Jira add_attachment HTTP error {e.response.status_code} '
                f'on {ticket_key}: {e.response.text[:200]}'
            )
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f'Jira add_attachment failed for {ticket_key}: {e}')
            return False

    def _build_description(self, incident: Dict[str, Any]) -> str:
        """Build plain-text incident description (wrapped in ADF by caller)."""
        # Exclude screenshot_b64 from the text description — it is sent as
        # a file attachment via add_attachment() and would bloat the ticket.
        description = incident.get('description') or 'No description provided'
        lines = [description]

        if incident.get('similar_incidents'):
            lines += ['', 'Similar Historical Incidents:']
            for s in incident['similar_incidents'][:3]:
                score = s.get('similarity_score', 0)
                lines.append(f"  - {s.get('title')} (similarity: {score:.2f})")

        if incident.get('attachment'):
            lines += ['', f"Screenshot attached: {incident['attachment']}"]

        return '\n'.join(lines)