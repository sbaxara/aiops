@echo off
rem Copyright (c) 2026 Axara Pty Ltd.
rem SPDX-License-Identifier: AGPL-3.0-or-later
set PYTHONWARNINGS=ignore:Unverified HTTPS request
cd /d "%~dp0"
if exist "%~dp0ms-playwright" set "PLAYWRIGHT_BROWSERS_PATH=%~dp0ms-playwright"
rem agent_config.yaml is created by the agent itself from config\bootstrap.yaml on first start.
call .venv\Scripts\activate
python axara_agent.py run
call .venv\Scripts\deactivate
