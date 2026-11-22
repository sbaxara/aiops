# Axara AIOps agent — Community edition

The agent runs on each host you want to monitor (Windows or Linux). It registers with the Axara
monitoring server once, using an enrolment token, then:

- downloads its monitor settings from the server and runs the enabled monitors on their schedule;
- sends the results over HTTPS to the server, queuing them on disk while the server is unreachable;
- keeps its monitor scripts up to date (the server pushes new versions, checked by SHA-256).

Licensed under the [GNU AGPL v3.0 or later](LICENSE).

## Contents

- [Monitors](#monitors)
- [Choose how to run it](#choose-how-to-run-it)
- [Before you start](#before-you-start)
- [Option A: run as Python scripts](#option-a-run-as-python-scripts)
- [Option B: build and install the compiled agent](#option-b-build-and-install-the-compiled-agent)
- [Configuration files](#configuration-files)
- [Browser monitors](#browser-monitors)
- [Upgrade](#upgrade)
- [Uninstall](#uninstall)
- [Troubleshooting](#troubleshooting)
- [Development and tests](#development-and-tests)

## Monitors

| Monitor | Checks |
|---------|--------|
| `cpu`, `memory`, `disk`, `network` | Usage against warning and critical thresholds |
| `url` | HTTP status, expected body text, response time |
| `log` | Pattern matches in log files, with counts and context |
| `database` | Connection and basic health (PostgreSQL, MySQL, MongoDB) |
| `process` | Minimum number of matching processes |
| `port` | A TCP port (IPv4 or IPv6) is listening, with the processes that own it |
| `browser` | A scripted Playwright journey through a web application |
| `ssl` | Certificate expiry on the host's TLS ports (expired or within the critical days = fail, within the warning days = warning) |

Monitors are switched on and configured in the dashboard (**Monitors**). The scripts live in
`axara/agent/monitors/` and the server keeps them updated.

## Choose how to run it

| | **A. Python scripts** | **B. Compiled executable** |
|---|---|---|
| Host needs | Python 3.11+ and the packages in `requirements.txt` | Nothing (Python is built in) |
| Runs as | `python axara_agent.py run` (or `agent.sh` / `agent.bat`); you add it to systemd / Task Scheduler | Windows service `AxaraAgent` or systemd unit `axara-agent`, registered by `install` |
| Build step | None | `release/agent_build/build_agent.py` on a build machine |

Both run the same code and talk to the server the same way.

## Before you start

1. **Server URL** – for example `https://monitoring.example.com:9090`. The agent must reach port 9090.
2. **Server CA certificate** – the certificate that signed the server's TLS certificate. With the
   default self-signed set-up this is `monitoring-server/certs/server.crt` from the server. Copy
   it to the host (for example as `server.crt`).
3. **Enrolment token** – in the dashboard: user menu → **Agent enrolment** → create. Save the token
   in a file (for example `enrol-token.txt`) and delete the file after installing. A token can
   enrol a set number of agents for a set number of days. A host that already has a registered
   agent is refused; remove the old one first.

## Option A: run as Python scripts

**Linux**
```bash
cd monitoring-agent
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

python axara_agent.py install --no-service \
    --server-url https://monitoring.example.com:9090 \
    --server-ca /path/to/server.crt \
    --enrol-token-file /path/to/enrol-token.txt \
    --app-name my-application --environment production
rm /path/to/enrol-token.txt

./agent.sh                        # runs in the foreground; Ctrl+C stops it
```

**Windows** (Command Prompt)
```bat
cd monitoring-agent
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

python axara_agent.py install --no-service --server-url https://monitoring.example.com:9090 --server-ca C:\path\server.crt --enrol-token-file C:\path\enrol-token.txt --app-name my-application --environment production
del C:\path\enrol-token.txt

agent.bat
```

`install` writes the server URL, CA and application to `config/bootstrap.yaml`, copies the CA to
`config/server-ca.crt` and registers the agent. `--no-service` is required in this mode: services
are registered only by the compiled agent.

**Start at boot (Linux, systemd)** – create `/etc/systemd/system/axara-agent.service`:
```ini
[Unit]
Description=Axara AIOps agent
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/opt/axara/monitoring-agent
ExecStart=/opt/axara/monitoring-agent/.venv/bin/python axara_agent.py run
Restart=always
RestartSec=30
User=root

[Install]
WantedBy=multi-user.target
```
then `sudo systemctl daemon-reload && sudo systemctl enable --now axara-agent`.
(Root is needed for some process, port and log information; use another user if you don't need them.)

**Start at boot (Windows)** – as Administrator:
```bat
schtasks /create /tn "AxaraAgent" /sc onstart /ru SYSTEM /tr "C:\axara\monitoring-agent\agent.bat"
```

Useful commands (inside the virtual environment): `python axara_agent.py status`,
`python axara_agent.py selftest`, `python axara_agent.py version`.

## Option B: build and install the compiled agent

### Build (once, on a build machine)

From the repository root, with Python 3.11+ (3.12 recommended, matching the release builds; the
Python you run the build with becomes the agent's built-in Python on Windows):

| Target | Build machine | Command |
|--------|---------------|---------|
| Windows x64 | Windows | `py -3.12 release\agent_build\build_agent.py --os windows --version 1.0.0 --out dist` |
| Linux x86-64 | Linux or Windows with Docker | `python3 release/agent_build/build_agent.py --os linux --version 1.0.0 --out dist` |

Nuitka downloads its own C compiler on Windows the first time. The Linux build runs in a
manylinux container (glibc 2.17), so the agent runs on RHEL/CentOS 7+, Ubuntu 18.04+ and Debian 9+.
Add `--no-browser` to skip the Chromium package.

The output in `dist/` is:
- `axara-agent-<version>-<os>-x64.zip` (Windows) or `.tar.gz` (Linux): the agent folder;
- `chromium-<os>-x64.zip|.tar.gz`: Chromium for browser monitors (optional).

### Install on a host

Copy the package (and the server CA and token file) to the host, extract it, and from the extracted
`axara-agent` folder:

**Linux** (as root)
```bash
./axara-agent install --server-url https://monitoring.example.com:9090 \
    --server-ca /path/to/server.crt --enrol-token-file /path/to/enrol-token.txt \
    --app-name my-application --environment production
./axara-agent status
```

**Windows** (Command Prompt as Administrator)
```bat
axara-agent.exe install --server-url https://monitoring.example.com:9090 --server-ca C:\path\server.crt --enrol-token-file C:\path\enrol-token.txt --app-name my-application --environment production
axara-agent.exe status
```

This registers the agent and adds a service that starts at boot (Windows service `AxaraAgent`,
restarted on failure; systemd unit `axara-agent`). Other options: `--with-browser [DIR]` installs
Chromium from the extracted `chromium-<os>-x64` folder; `--no-service` configures only.

## Configuration files

| File | Written by | Purpose |
|------|-----------|---------|
| `config/bootstrap.yaml` | you / `install` | Server URL, CA (`ssl_verify`), application name and environment. Read at registration and at every start. |
| `config/agent_config.yaml` | the agent | Created at registration: server id and agent API key. Keep it private. |
| `config/monitors.yaml` | the agent | Monitor settings downloaded from the server. Do not edit; change monitors in the dashboard. |
| `config/server-ca.crt` | `install` | Copy of the server CA. |

`ssl_verify` is the CA file path after `install --server-ca`. `false` turns certificate checking off
and should only be used for a first test. The agent always uses its own CA setting, even when
`REQUESTS_CA_BUNDLE` is set on the host. HTTPS proxies set in `HTTPS_PROXY` are used.

## Browser monitors

A browser monitor runs a Playwright script you write (see `release/agent_build/example_journey.py`).
Put scripts in `scripts/browser/` and set the monitor's script path in the dashboard. Scripts read
URLs and credentials from environment variables on the host, never from the script.

- Python mode: `pip install -r requirements.txt` includes Playwright; install the browser once with
  `python -m playwright install chromium` (needs internet), or point `PLAYWRIGHT_BROWSERS_PATH` at a copy.
- Compiled mode: `install --with-browser` installs the Chromium package built with the agent.

## Upgrade

- **Python mode**: stop the agent, replace the source files (keep `config/`, `queue/` and `.venv`),
  run `pip install -r requirements.txt`, start it again.
- **Compiled mode**: stop the service (`systemctl stop axara-agent` / `sc stop AxaraAgent`), extract
  the new package over the folder (your `config/` is kept: packages contain example files only),
  start the service again.

Monitor scripts are updated by the server automatically; no action is needed for those.

## Uninstall

**Compiled agent** (as root / Administrator, in the agent folder):
```bash
./axara-agent uninstall --purge
```
This removes the agent from the server (its monitors are archived, its key revoked, recorded in
the audit log), removes the service and deletes the agent folder. If the server cannot be reached
nothing changes; add `--force` to remove it from the host anyway, then remove it in the dashboard
(**Servers → host → Remove agent**). `uninstall` without `--purge` only removes the service.

**Python mode**:
1. In the dashboard: **Servers → host → Remove agent** (archives its monitors, revokes its key).
2. Stop the agent and remove what you added to start it
   (`sudo systemctl disable --now axara-agent && sudo rm /etc/systemd/system/axara-agent.service`,
   or `schtasks /delete /tn AxaraAgent /f`).
3. Delete the `monitoring-agent` folder.

## Troubleshooting

| Symptom | Check |
|---------|-------|
| `certificate verify failed` | `--server-ca` must be the CA that signed the server certificate, and the server URL host name must be in that certificate. |
| `401 … enrolment token` | Token expired, revoked or used up: create a new one. |
| `409 hostname_registered` | The host already has an agent: remove it in **Servers**, then install again. |
| No data in the dashboard | Are the monitors enabled? Agents check for changes every few minutes. Log: `logs/` in the agent folder. |
| `selftest` fails to load a monitor | Missing Python package (Python mode): `pip install -r requirements.txt`. |

## Development and tests

```bash
cd monitoring-agent
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
python -m pytest -q tests
```

Layout:
```
axara_agent.py        launcher: run, install, uninstall, status, selftest (compiled entry point)
axara/agent/core/     agent loop, registration, collector, sender, updaters, monitor loader,
                      optional extensions (extensions.py)
axara/agent/monitors/ monitor scripts (kept as source; updated by the server)
axara/agent/utils/    helpers
config/               bootstrap.yaml and the example agent_config
agent.sh, agent.bat   run from a source checkout with .venv
tests/                test suite
```
