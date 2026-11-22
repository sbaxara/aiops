# Contributing to Axara AIOps Community edition

Thank you for helping. This repository holds the Community edition (GNU AGPL v3.0 or later):
the agent, the monitoring server and the dashboard.

## Before you start

- **Bugs and ideas**: open a GitHub issue. Include the version (shown in the dashboard sidebar,
  `python manage.py help`, or `python axara_agent.py version`), what you did, what you expected
  and the relevant log lines (server: `docker compose logs monitoring-server`; agent:
  `logs/agent.log`). Remove host names, keys and passwords from logs.
- **Security problems**: do **not** open a public issue. Report them privately to
  `<security contact - to be confirmed>`. We acknowledge reports within 5 working days.
- **Larger changes**: open an issue first so we can agree the approach before you write code.

## Contributor Licence Agreement

Before we can merge your first pull request you need to agree to the
[Contributor Licence Agreement](CLA.md). You keep the copyright in your contribution; the
agreement gives Axara Pty Ltd the rights it needs to distribute your contribution, including
under licences other than the AGPL. Individuals agree once; if you contribute as part of your
job, your employer may need to agree too (ask us for the corporate version).

## Development set-up

- Server: see [monitoring-server/README.md](monitoring-server/README.md#development-and-tests).
- Agent: see [monitoring-agent/README.md](monitoring-agent/README.md#development-and-tests).
- Dashboard: `cd monitoring-server/dashboard && npm install && npm run dev`.

## Code standard

- Every new source file starts with the notice header:
  ```python
  # Copyright (c) 2026 Axara Pty Ltd.
  # SPDX-License-Identifier: AGPL-3.0-or-later
  """What this file does, in two to four lines."""
  ```
  (`//` comments in JavaScript, `rem` in batch files.)
- Every Python function and class has a Google-style docstring (Args, Returns, Raises where
  they apply); JavaScript, shell and batch functions have a comment block above them.
- Check your files: `python monitoring-server/tools/doc_check.py <files>`.
- Keep changes focused; no unrelated reformatting.
- Never commit secrets, real host names, customer data or `.env` files.

## Tests

Run before opening a pull request:
```bash
cd monitoring-server && python -m pytest -q tests
cd monitoring-agent  && python -m pytest -q tests
cd monitoring-server/dashboard && npm run build
```
Add or update tests for the behaviour you change.

## Pull requests

1. Fork, create a branch from `main`, make your change with tests.
2. Describe what changed and why, and how you tested it.
3. A maintainer reviews it; we may ask for changes.
4. Once the CLA is agreed and checks pass, we merge.

Features that belong to the Enterprise edition (single sign-on, discovery and configuration
automation, topology and correlation, scheduled reports, notifications and escalation,
ServiceNow, SIEM forwarding, TLS protocol and cipher checks) are not accepted here.

## Licence

By contributing you agree that your contribution is licensed to users under the GNU AGPL v3.0 or
later, and to Axara Pty Ltd under the [CLA](CLA.md).
