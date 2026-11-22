@echo off
rem Copyright (c) 2026 Axara Pty Ltd.
rem SPDX-License-Identifier: AGPL-3.0-or-later
rem
rem Apply the database migrations (core + installed plugins) in a one-off server container.
rem Run from monitoring-server\ after `docker compose build`. The database password comes from
rem DB_PASSWORD in .env; nothing is hard-coded here. To create a new migration instead, see
rem README.md ("Database migrations").
docker compose up -d postgres || exit /b 1
docker compose run --rm monitoring-server python manage.py migrate
