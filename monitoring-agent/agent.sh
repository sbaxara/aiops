#!/usr/bin/env bash
# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
# Unix twin of agent.bat — run the monitoring agent natively from its venv.
set -euo pipefail
export PYTHONWARNINGS="ignore:Unverified HTTPS request"
cd "$(dirname "${BASH_SOURCE[0]}")"
# Offline installs ship Playwright browsers alongside the agent.
if [ -d ms-playwright ]; then export PLAYWRIGHT_BROWSERS_PATH="$PWD/ms-playwright"; fi
# agent_config.yaml is created by the agent itself from config/bootstrap.yaml on first start.
# shellcheck disable=SC1091
source .venv/bin/activate
python axara_agent.py run
deactivate
