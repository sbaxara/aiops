# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Axara AIOps agent: collects metrics on a monitored host and sends them to the server.

`core` and `utils` are the agent itself (compiled into the native executable when built
with release/agent_build). `monitors` are plain Python scripts loaded from disk by
core.monitor_loader, so the server can update them without reinstalling the agent.
"""

__version__ = "1.0.0"
