@echo off
rem Copyright (c) 2026 Axara Pty Ltd.
rem SPDX-License-Identifier: AGPL-3.0-or-later
rem Start / manage the monitoring-server stack (docker compose).
rem   server.bat         start (up -d)
rem   server.bat build   rebuild images then start
rem   server.bat down    stop the stack
rem   server.bat logs    follow server logs
cd /d "%~dp0"
if /i "%1"=="build" (
  docker compose up -d --build
) else if /i "%1"=="down" (
  docker compose down
) else if /i "%1"=="logs" (
  docker compose logs -f monitoring-server
) else (
  docker compose up -d
)
docker compose ps
