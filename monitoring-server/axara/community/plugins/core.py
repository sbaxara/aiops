# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The core's own registrations: the Jira ticket provider and the ITSM sync job.

Jira is the core's ticketing tool (always available, no licence feature). The ITSM sync
job polls every active ticket provider for tickets resolved in the ticketing tool and
resolves the matching AIOps incidents (incident_management.itsm_sync).
"""
import logging
import os
from typing import Any, Dict, Optional

from axara.community.plugins.registry import Registry, TicketProvider

logger = logging.getLogger(__name__)


def _adf_text(node: Any) -> str:
    """Plain text from an Atlassian Document Format node (Jira Cloud comments).

    Args:
        node: ADF dict, a list of nodes, or a plain string (Jira Server/Data Center).

    Returns:
        The concatenated text, paragraphs separated by newlines.
    """
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(t for t in (_adf_text(n) for n in node) if t)
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        inner = node.get("content") or []
        sep = "\n" if node.get("type") in ("doc",) else ""
        return sep.join(_adf_text(n) for n in inner)
    return ""


class JiraTicketProvider(TicketProvider):
    """Jira Cloud / Data Center through incident_management.jira_client.JiraClient."""

    name = "jira"
    DONE_STATUSES = ("done", "resolved", "closed")

    def __init__(self, client=None):
        """Create the provider.

        Args:
            client: A JiraClient; created from the JIRA_* environment variables when None.
        """
        if client is None:
            from axara.community.incident_management.jira_client import JiraClient
            client = JiraClient()
        self.client = client
        self._me: Optional[str] = None

    def ticket_url(self, ticket: str) -> Optional[str]:
        """Browse link from JIRA_URL. See TicketProvider.ticket_url."""
        base = os.environ.get("JIRA_URL", "").rstrip("/")
        return f"{base}/browse/{ticket}" if base and ticket else None

    def create_ticket(self, incident: Dict[str, Any]) -> Optional[str]:
        """Open a Jira issue. See TicketProvider.create_ticket."""
        return self.client.create_ticket(incident)

    def add_attachment(self, ticket: str, file_path: str) -> bool:
        """Attach a file to the issue. See TicketProvider.add_attachment."""
        return bool(self.client.add_attachment(ticket, file_path))

    def add_comment(self, ticket: str, text: str) -> bool:
        """Comment on the issue. See TicketProvider.add_comment."""
        return bool(self.client.add_comment(ticket, text))

    def resolve(self, ticket: str, notes: str) -> bool:
        """Transition the issue to done. See TicketProvider.resolve."""
        return bool(self.client.resolve_ticket(ticket, notes))

    def _my_account(self) -> Optional[str]:
        """Account id of the integration user, so its own comments are not taken as notes.

        Returns:
            The accountId (Jira Cloud) or name (Data Center), or None when unavailable.
        """
        if self._me is None:
            try:
                me = self.client._get("/myself")
                self._me = me.get("accountId") or me.get("name") or ""
            except Exception:
                logger.debug("Jira /myself failed", exc_info=True)
                self._me = ""
        return self._me or None

    def fetch_resolution(self, ticket: str) -> Optional[str]:
        """Resolution notes when the issue is done in Jira. See TicketProvider.fetch_resolution.

        The notes are the most recent comment not written by the integration user, plus
        the Jira resolution name.
        """
        if not getattr(self.client, "_enabled", False):
            return None
        try:
            data = self.client._get(f"/issue/{ticket}?fields=status,resolution,comment")
        except Exception as e:
            logger.warning("Jira status check failed for %s: %s", ticket, e)
            return None
        fields = data.get("fields") or {}
        status = fields.get("status") or {}
        category = ((status.get("statusCategory") or {}).get("key") or "").lower()
        if category != "done" and (status.get("name") or "").lower() not in self.DONE_STATUSES:
            return None
        me = self._my_account()
        comments = ((fields.get("comment") or {}).get("comments") or [])
        note = ""
        for c in reversed(comments):
            author = c.get("author") or {}
            if me and me in (author.get("accountId"), author.get("name")):
                continue
            note = _adf_text(c.get("body")).strip()
            if note:
                break
        resolution = ((fields.get("resolution") or {}).get("name") or "").strip()
        parts = [note, f"Jira {ticket} resolution: {resolution}" if resolution else ""]
        return "\n".join(p for p in parts if p) or f"Resolved in Jira ({ticket})"


def _start_itsm_sync(app) -> None:
    """Start-up hook: run the ITSM sync job in the leader worker only.

    Args:
        app: The FastAPI app (the job handle is kept on app.state).
    """
    from axara.community.common.scheduler_lock import acquire_scheduler_lock
    if not acquire_scheduler_lock("itsm_sync"):
        return
    from axara.community.incident_management.itsm_sync import ItsmSync
    app.state.itsm_sync = ItsmSync()
    app.state.itsm_sync.start()


def _stop_itsm_sync(app) -> None:
    """Shutdown hook: stop the ITSM sync job if this worker runs it.

    Args:
        app: The FastAPI app.
    """
    job = getattr(app.state, "itsm_sync", None)
    if job:
        job.stop()


def register(reg: Registry) -> None:
    """Register the core's extensions.

    Args:
        reg: The registry being built.
    """
    reg.add_ticket_provider(JiraTicketProvider)          # lazy: the client is created on first use
    reg.add_startup("itsm-sync", _start_itsm_sync)
    reg.add_shutdown("itsm-sync", _stop_itsm_sync)
