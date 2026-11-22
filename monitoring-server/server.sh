#!/usr/bin/env bash
# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
# Start / manage the monitoring-server stack (docker compose).
#   ./server.sh         start (up -d)
#   ./server.sh build   rebuild images then start
#   ./server.sh down    stop the stack
#   ./server.sh logs    follow server logs
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
case "${1:-up}" in
  build) docker compose up -d --build ;;
  down)  docker compose down ;;
  logs)  docker compose logs -f monitoring-server ;;
  up|*)  docker compose up -d ;;
esac
docker compose ps
