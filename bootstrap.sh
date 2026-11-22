#!/usr/bin/env bash
# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# One-shot environment bootstrap for the AIOps monitoring server.
#   - ensures .env exists and has strong SECRET_KEY / MASTER_ENCRYPTION_KEY
#   - creates a self-signed TLS certificate when certs/server.crt is missing
#   - brings up datastores, waits for Postgres
#   - applies the database migrations (python manage.py migrate)
#   - seeds the first admin account
#   - starts the server + dashboard
# Idempotent: safe to re-run. Admin password via ADMIN_PASSWORD env or prompt.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$SCRIPT_DIR/monitoring-server"
cd "$SERVER_DIR"

command -v docker >/dev/null 2>&1 || { echo "ERROR: docker not found"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "ERROR: docker compose v2 required"; exit 1; }

# --- .env + secrets (shared helper, identical behaviour to bootstrap.bat) --
PY="$(command -v python3 || command -v python)"
"$PY" ensure_env.py

# --- admin credentials ----------------------------------------------------
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_EMAIL="${ADMIN_EMAIL:-${ADMIN_USERNAME}@local}"
if [ -z "${ADMIN_PASSWORD:-}" ]; then
  read -rsp "Set a password for admin user '${ADMIN_USERNAME}': " ADMIN_PASSWORD; echo
  [ -n "$ADMIN_PASSWORD" ] || { echo "ERROR: admin password required"; exit 1; }
fi
export ADMIN_USERNAME ADMIN_EMAIL ADMIN_PASSWORD ADMIN_RESET_PASSWORD="${ADMIN_RESET_PASSWORD:-}"

# --- TLS certificate (self-signed unless you supply certs/server.crt + .key) --
if [ ! -f certs/server.crt ] || [ ! -f certs/server.key ]; then
  echo "Creating a self-signed TLS certificate in monitoring-server/certs (replace it with your own for production)…"
  mkdir -p certs
  docker compose run --rm --no-deps -v "$SERVER_DIR/certs:/certs" \
    -e CERT_HOSTS="${CERT_HOSTS:-$(hostname)}" monitoring-server python manage.py make-cert
fi

# --- datastores first -----------------------------------------------------
echo "Starting datastores (postgres, chromadb, ollama)…"
docker compose up -d postgres chromadb ollama

DB_USER="$(grep -E '^DB_USER=' .env | head -1 | cut -d= -f2- || true)"; DB_USER="${DB_USER:-monitoring}"
printf "Waiting for Postgres"
for i in $(seq 1 60); do
  if docker compose exec -T postgres pg_isready -U "$DB_USER" >/dev/null 2>&1; then echo " ready"; break; fi
  printf "."; sleep 2
  if [ "$i" -eq 60 ]; then echo " timeout waiting for Postgres"; exit 1; fi
done

# --- migrate + seed admin in a one-off app container ----------------------
echo "Applying migrations and seeding admin…"
docker compose run --rm \
  -e ADMIN_USERNAME -e ADMIN_EMAIL -e ADMIN_PASSWORD -e ADMIN_RESET_PASSWORD \
  monitoring-server bash -lc "python manage.py migrate && python manage.py seed-admin"

# --- start everything -----------------------------------------------------
echo "Starting server + dashboard…"
docker compose up -d

cat <<DONE

✅ Environment ready.
   Admin user : ${ADMIN_USERNAME}
   Dashboard  : https://localhost  (or your configured host / ALLOWED_ORIGINS)

Air-gapped note: ensure the LLM model is present in Ollama —
   docker compose exec ollama ollama list      # expect gemma4:26b
If missing, import it on this host (offline) then re-check.
DONE
