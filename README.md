# Axara AIOps — Community edition

Axara AIOps is an on-premises monitoring platform that detects problems, opens incidents, and
uses a **local** large language model to suggest a root cause and fixes, learning from incidents
your team has already resolved. Nothing leaves your network: the LLM runs in your own Ollama
container.

The Community edition is free and open source under the
[GNU Affero General Public License v3.0 or later](LICENSE).

| Component | Folder | What it does |
|-----------|--------|--------------|
| **Monitoring server** | [`monitoring-server/`](monitoring-server/README.md) | API, incident engine, LLM analysis, incident memory, web dashboard. Runs as a Docker Compose stack. |
| **Agent** | [`monitoring-agent/`](monitoring-agent/README.md) | Runs on each host you monitor (Windows or Linux). Collects metrics and checks and sends them to the server. Runs as Python scripts or as a compiled executable. |

## How it fits together

```
 hosts                                 monitoring server (Docker Compose)
 ┌───────────────┐   HTTPS :9090      ┌──────────────────────────────────────────────┐
 │ axara agent   │ ─────────────────▶ │ API + incident engine ── PostgreSQL          │
 │  cpu, disk,   │  metrics, checks   │        │                ── ChromaDB (memory) │
 │  url, log, …  │ ◀───────────────── │        └─ LLM analysis ── Ollama (local LLM) │
 └───────────────┘  monitor settings  │ dashboard (nginx) :443 ◀── your browser      │
                                      └──────────────────────────────────────────────┘
                                                 │ optional
                                                 ▼
                                               Jira (tickets, resolution sync)
```

## Getting started

1. Install the server: [monitoring-server/README.md](monitoring-server/README.md).
2. Sign in to the dashboard, create an agent enrolment token (user menu → **Agent enrolment**).
3. Install an agent on each host: [monitoring-agent/README.md](monitoring-agent/README.md).
4. Turn on the monitors you want for each host in **Monitors**.

## What is in the Community edition

- The agent and every monitor: CPU, memory, disk, network, URL, log patterns, databases
  (PostgreSQL, MySQL, MongoDB), process, port, browser journeys (Playwright) and SSL certificate
  expiry.
- Monitoring core: metrics, monitor states, incidents with de-duplication, maintenance windows.
- LLM root-cause analysis with a local model, and incident memory (similar resolved incidents).
- Jira tickets, with a sync job that resolves the incident when the Jira ticket is resolved.
- Local accounts with roles, user approval, agent enrolment tokens, a tamper-evident audit log.
- No licence file, no agent limit.

An Enterprise edition (sold separately, with support) adds single sign-on, discovery and
configuration automation, topology and incident correlation, scheduled reports, notifications and
escalation, ServiceNow, SIEM forwarding and TLS protocol/cipher checks.

## Repository layout

```
LICENSE                AGPL-3.0 (also in each component)
bootstrap.sh / .bat    one-shot server set-up (see monitoring-server/README.md)
monitoring-server/     server, dashboard, database migrations, monitor templates
monitoring-agent/      agent source
release/agent_build/   build script for the compiled agent executable
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Contributions need the
[Contributor Licence Agreement](CLA.md). Third-party software and models are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Trademark

The Axara name and logo are trademarks of Axara Pty Ltd and are not licensed under the AGPL.
