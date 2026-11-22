# Axara AIOps monitoring server — Community edition

The server receives metrics and check results from Axara agents, turns failures into incidents,
asks a local LLM for a root cause and recommendations, remembers how past incidents were resolved,
and optionally opens Jira tickets. The web dashboard is served by the same stack.

Licensed under the [GNU AGPL v3.0 or later](LICENSE).

## Contents

- [What runs](#what-runs)
- [Requirements](#requirements)
- [Install from source (quick start)](#install-from-source-quick-start)
- [Build options: plain source or compiled](#build-options-plain-source-or-compiled)
- [Configuration](#configuration)
- [First steps in the dashboard](#first-steps-in-the-dashboard)
- [Day-to-day operation](#day-to-day-operation)
- [Upgrade](#upgrade)
- [Uninstall](#uninstall)
- [Development and tests](#development-and-tests)
- [Folder layout](#folder-layout)

## What runs

`docker compose` starts these containers on one host:

| Service | Image | Port (host) | Purpose |
|---------|-------|-------------|---------|
| `monitoring-server` | built from `Dockerfile` | 9090 (HTTPS) | API for agents and the dashboard, incident engine, LLM analysis, ITSM sync |
| `dashboard` | built from `dashboard/Dockerfile` | 443 (HTTPS), 80 | React web dashboard behind nginx |
| `postgres` | postgres:19 | 5432 | Database |
| `chromadb` | chromadb/chroma | 8000 | Incident memory (vector store) |
| `ollama` | ollama/ollama | 11434 | Local LLM (default model `gemma4:26b`) |
| `ollama-init` | ollama/ollama | – | Pulls the model once, then exits |

The server image runs Python 3.14. PostgreSQL 19 keeps its files in `data/postgres/19/docker`.

Ports 5432, 8000 and 11434 are published for convenience. In production, add the hardened overlay
so only 443 and 9090 are reachable (see [Configuration](#configuration)).

## Requirements

- Linux (x86-64) or Windows 10/11 / Server with Docker Desktop (WSL 2).
- **Docker Engine with Docker Compose v2.24 or later** (`docker compose version`).
- **Python 3.9+ on the host**, used only by the set-up script to create `.env` (no packages needed).
- **An NVIDIA GPU** with the NVIDIA Container Toolkit for the LLM. No GPU? Use the CPU overlay
  (below); analysis is then much slower, so consider a smaller model.
- Disk space for the model (the default model is a large download; it is pulled once on first start)
  and for PostgreSQL data.
- Internet access on first start to pull images and the model (or load them yourself beforehand).

## Install from source (quick start)

From the repository root:

**Linux**
```bash
cd axara-aiops-community          # the folder that holds bootstrap.sh
sudo bash bootstrap.sh            # asks for an admin password (min. 12 characters)
```

**Windows** (Command Prompt, Docker Desktop running)
```bat
cd axara-aiops-community
bootstrap.bat
```

No NVIDIA GPU? Before running bootstrap:
```bash
export COMPOSE_FILE=docker-compose.yml:docker-compose.cpu.yml     # Linux
set COMPOSE_FILE=docker-compose.yml;docker-compose.cpu.yml        # Windows
```

`bootstrap` is safe to re-run. It:

1. creates `monitoring-server/.env` from `.env.example` with a random `SECRET_KEY`,
   `MASTER_ENCRYPTION_KEY` and `DB_PASSWORD`, readable only by its owner;
2. builds the server image and creates a **self-signed TLS certificate** in
   `monitoring-server/certs/` if none is there (set `CERT_HOSTS=myserver,myserver.corp.local`
   first to choose the host names);
3. starts PostgreSQL, ChromaDB and Ollama and waits for the database;
4. creates the database schema (`python manage.py migrate`) and the first admin
   (`python manage.py seed-admin`; set `ADMIN_USERNAME` / `ADMIN_PASSWORD` to skip the prompt);
5. starts everything.

Then open **https://&lt;server&gt;/** and sign in as `admin`. The browser warns about the
self-signed certificate until you replace it (see [TLS certificate](#tls-certificate)).

The first start downloads the LLM model in the background. Check it with:
```bash
cd monitoring-server
docker compose logs -f ollama-init           # "pulled successfully" or "already present"
```

### Manual installation (what bootstrap does)

```bash
cd monitoring-server
python3 ensure_env.py                                         # .env with generated secrets
docker compose build monitoring-server dashboard
mkdir -p certs
docker compose run --rm --no-deps -v "$PWD/certs:/certs" -e CERT_HOSTS="$(hostname)" \
    monitoring-server python manage.py make-cert              # or copy your own server.crt/.key
docker compose up -d postgres chromadb ollama
docker compose run --rm -e ADMIN_PASSWORD='choose-a-strong-one' \
    monitoring-server bash -lc "python manage.py migrate && python manage.py seed-admin"
docker compose up -d
```

## Build options: plain source or compiled

The Community `Dockerfile` copies only `axara/community`; it never reads or needs Enterprise code.

| Build | Command (in `monitoring-server/`) | Result |
|-------|-----------------------------------|--------|
| Plain source (default) | `docker compose build monitoring-server` | Python source in the image; quick to build |
| Compiled core | `docker compose build --build-arg COMPILE=1 monitoring-server` | Core packages compiled to native modules with Nuitka; slower build, no application `.py` in the image except migrations, monitor templates and `manage.py` |

After building, restart with `docker compose up -d`. Both builds behave the same.

## Configuration

### `.env`

`monitoring-server/.env` is created for you. Keep it private; it holds the database password and
encryption keys. Common settings (see `.env.example` for all):

| Setting | Purpose |
|---------|---------|
| `JIRA_URL`, `JIRA_USER`, `JIRA_API_KEY`, `JIRA_PROJECT_KEY` | Open Jira tickets for incidents and pick up their resolution. Leave empty to run without tickets. |
| `LLM_MODEL` | Ollama model (default `gemma4:26b`). |
| `ALLOWED_ORIGINS` | Extra browser origins allowed to call the API, e.g. `https://monitoring.example.com`. |
| `API_DOCS_ENABLED=1` | Serve the API documentation at `/docs`. |

Apply changes with `docker compose up -d`. Do not change `MASTER_ENCRYPTION_KEY` by hand: stored
credentials are encrypted with it (see `python manage.py rotate-master-key`).

### TLS certificate

The server (port 9090) and the dashboard (port 443) both use `monitoring-server/certs/server.crt`
and `server.key`. Replace the self-signed pair with a certificate from your own CA whose names
include the host name agents and browsers use, then `docker compose restart monitoring-server dashboard`.
Agents need the issuing CA certificate (`--server-ca`).

### Compose overlays

| File | Use |
|------|-----|
| `docker-compose.hardened.yml` | Production: stops publishing PostgreSQL, ChromaDB and Ollama ports. |
| `docker-compose.cpu.yml` | Hosts without an NVIDIA GPU. |

Combine them with `COMPOSE_FILE`, for example on Linux:
```bash
export COMPOSE_FILE=docker-compose.yml:docker-compose.hardened.yml
```
(Windows separates the files with `;`.) Put the line in your shell profile or service unit so every
`docker compose` command uses the same files.

## First steps in the dashboard

1. **Applications** – create an application (agents can also create it at registration). The
   settings button on an application opens its maintenance windows (no incidents while one is in effect).
2. **User menu → Agent enrolment** – create an enrolment token (number of agents, days valid).
   The token is shown once; give it to whoever installs agents.
3. Install agents – see [`../monitoring-agent/README.md`](../monitoring-agent/README.md).
4. **Monitors** – each new agent gets one monitor of each type it supports. CPU, memory, disk,
   network and SSL certificate monitors start on with default thresholds; the others (URL, log,
   process, port, database, browser) start off until you fill in their settings. Agents pick up
   changes within a few minutes.
5. **Settings** – LLM and incident settings. **User menu → Manage Users** approves
   people who registered themselves.

## Day-to-day operation

Run in `monitoring-server/`:

| Task | Command |
|------|---------|
| Start / stop | `./server.sh` / `./server.sh down` (Windows: `server.bat`) or `docker compose up -d` / `docker compose down` |
| Rebuild and start | `./server.sh build` |
| Server log | `./server.sh logs` or `docker compose logs -f monitoring-server` |
| Status | `docker compose ps` and `curl -k https://localhost:9090/health` |
| Admin commands | `docker compose exec monitoring-server python manage.py help` |
| Reset admin password | `docker compose exec -e ADMIN_PASSWORD=... -e ADMIN_RESET_PASSWORD=1 monitoring-server python manage.py seed-admin` |
| Rebuild incident memory | `docker compose exec monitoring-server python manage.py rag-reindex` |
| Database backup | `docker compose exec -T postgres pg_dump -U monitoring monitoring > backup.sql` |

Data lives in `monitoring-server/data/` (PostgreSQL, ChromaDB, Ollama models, generated agent
settings), logs in `monitoring-server/logs/`.

## Upgrade

```bash
cd axara-aiops-community
# replace the source with the new version (git pull, or extract the new archive over it),
# keeping monitoring-server/.env, certs/ and data/
cd monitoring-server
docker compose build monitoring-server dashboard
docker compose run --rm monitoring-server python manage.py migrate
docker compose up -d
```

Take a database backup first. `manage.py migrate` applies any schema changes the new version
brings.

## Uninstall

```bash
cd monitoring-server
docker compose down --remove-orphans          # stop and remove the containers, keep data
```

To remove everything, including all monitoring data:
```bash
docker compose down --remove-orphans --rmi all       # also removes the stack's images
sudo rm -rf data logs certs .env                     # Windows: rmdir /s /q data logs certs & del .env
```
Remove agents from their hosts first (see the agent README), or remove them later in
**Servers → host → Remove agent**.

## Development and tests

```bash
cd monitoring-server
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest httpx
python -m pytest -q tests
```
Set `AXARA_TEST_PG_URL=postgresql://user:pass@host/dbname` to also run the PostgreSQL migration
tests against an empty database. Dashboard: `cd dashboard && npm install && npm run dev`
(proxies `/api` to `localhost:9090`).

Code standard: every source file starts with an SPDX licence header and every function has a
Google-style docstring (Python) or comment block (JavaScript, shell, batch);
`python tools/doc_check.py FILE...` checks it.

## Folder layout

```
axara/community/       server source (api, processing, incident_management, database, llm, ...)
  database/migrations/ database schema (Alembic)
  plugins/             extension points (registry); the Community edition registers Jira only
config/monitor_templates/   monitor scripts the server sends to agents
dashboard/             React dashboard (src/edition/community.js: Community edition module)
docker-compose*.yml    stack and overlays
Dockerfile             server image (Community)
manage.py              admin commands
ensure_env.py          creates .env with generated secrets
tests/                 test suite
```
