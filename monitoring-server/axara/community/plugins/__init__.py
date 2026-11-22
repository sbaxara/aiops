# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Plugin interface between the Axara AIOps core and optional plugins.

registry  Registry, TicketProvider and Notifier: the extension points.
loader    get_registry(): builds the process-wide registry from the core, the built-in
          modules and installed `axara.plugins` entry points.
core      The core's own registrations (Jira ticket provider, ITSM sync job).
"""
