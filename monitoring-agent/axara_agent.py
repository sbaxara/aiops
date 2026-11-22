# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Axara AIOps agent launcher: the entry point of the compiled agent (also runs from source)."""
import argparse
import datetime
import getpass
import hashlib
import json
import os
import platform
import re
import runpy
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

USAGE = """axara-agent [run]
       axara-agent install --server-url URL [--server-ca FILE] [--enrol-token-file FILE]
                           [--with-browser [DIR]] [--app-name NAME] [--environment ENV]
                           [--no-service] [--accept-eula]
       axara-agent uninstall [--purge [--force] [--yes]]
       axara-agent status | selftest | version
       axara-agent --run-script FILE [ARGS...]    (used by browser monitors)

The enrolment token (first install only) is created by an administrator in the dashboard
(Agent enrolment). Pass it with --enrol-token-file, the AXARA_ENROL_TOKEN environment
variable, or type it when asked; --enrol-token VALUE also works but leaves it in your
shell history."""

SERVICE_NAME = "AxaraAgent"                     # Windows service
SERVICE_DISPLAY = "Axara AIOps Agent"
SYSTEMD_UNIT = "axara-agent.service"            # Linux


def _compiled() -> bool:
    """Whether this is the Nuitka-compiled agent rather than Python source."""
    return "__compiled__" in globals()


IS_WINDOWS = os.name == "nt"
EXE = Path(sys.argv[0]).resolve() if _compiled() else None
BASE = EXE.parent if _compiled() else Path(__file__).resolve().parent
CONFIG = BASE / "config"
OS_TAG = f"{'windows' if IS_WINDOWS else 'linux'}-x64"


def say(msg: str):
    """Print a progress message.

    Args:
        msg: Message text.
    """
    print(f"[axara-agent] {msg}", flush=True)


def die(msg: str, code: int = 1):
    """Print an error and exit.

    Args:
        msg: Error text.
        code: Exit status.
    """
    print(f"[axara-agent] ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def version() -> str:
    """The agent version from the package's VERSION file, else the source version (axara.agent)."""
    v = BASE / "VERSION"
    if v.exists():
        return v.read_text(encoding="utf-8").strip()
    from axara.agent import __version__
    return __version__


# ------------------------------------------------------------------ runtime

def prepare_runtime():
    """Paths in the agent are relative to its folder; monitors are loaded from disk."""
    os.chdir(BASE)
    os.environ["AXARA_AGENT_HOME"] = str(BASE)        # core.monitor_loader finds the monitors here
    if str(BASE) not in sys.path:
        sys.path.insert(0, str(BASE))
    browsers = BASE / "ms-playwright"
    if browsers.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    if _compiled():
        os.environ["AXARA_AGENT_EXE"] = str(EXE)      # browser monitor runs scripts through us
    # Packages ship templates only, so unpacking a new version never overwrites settings.
    # agent_config.yaml is NOT seeded: the agent creates it from bootstrap.yaml on first start.
    for real, example in (("bootstrap.yaml", "bootstrap.example.yaml"),):
        if not (CONFIG / real).exists() and (CONFIG / example).exists():
            shutil.copyfile(CONFIG / example, CONFIG / real)
    for stream in ("stdout", "stderr"):                 # no console under a service manager
        if getattr(sys, stream) is None:
            setattr(sys, stream, open(os.devnull, "w"))


def cmd_run(_a=None):
    """Run the agent in the foreground (the 'run' command).

    Args:
        _a: Parsed arguments (unused).
    """
    prepare_runtime()
    from axara.agent.core.agent import main as agent_main
    agent_main()


def cmd_run_script(argv):
    """Run a browser journey script with the agent's bundled Python and Playwright (--run-script).

    Args:
        argv: Script path followed by its own arguments.
    """
    if not argv:
        die("--run-script needs a script path")
    script = os.path.abspath(argv[0])
    if not os.path.isfile(script):
        die(f"script not found: {script}", 2)
    prepare_runtime()
    sys.argv = [script] + list(argv[1:])
    runpy.run_path(script, run_name="__main__")


def cmd_selftest(_a=None):
    """Check that the monitors and the built-in runtime load on this host."""
    prepare_runtime()
    ok = True
    try:
        from axara.agent.core import monitor_loader
        monitor_loader.load()                            # all monitor classes
        names = sorted(p.stem for p in monitor_loader.monitors_dir().glob("*_monitor.py")
                       if p.stem != "base_monitor")
        say(f"monitors: {len(names)} loaded ({', '.join(n.replace('_monitor', '') for n in names)})")
    except Exception as e:                               # pragma: no cover - reported to the user
        ok = False
        say(f"monitors: FAILED to load: {e}")
    for mod in ("psutil", "yaml", "requests", "cryptography", "pymongo", "pymysql", "psycopg2"):
        try:
            __import__(mod)
        except Exception as e:
            ok = False
            say(f"module {mod}: FAILED ({e})")
    try:
        import playwright.sync_api                       # noqa: F401
        has_browser = (BASE / "ms-playwright").is_dir()
        say(f"browser monitors: runtime OK, Chromium {'installed' if has_browser else 'not installed (install --with-browser)'}")
    except Exception as e:
        ok = False
        say(f"browser monitors: FAILED ({e})")
    say("selftest passed" if ok else "selftest FAILED")
    return 0 if ok else 1


# ------------------------------------------------------------------ install

def _set_yaml_key(path: Path, section: str, key: str, value: str):
    """Set `key: value` in a top-level YAML `section:` block, keeping comments intact."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True) if path.exists() else []
    try:
        start = next(i for i, l in enumerate(lines) if re.match(rf"^{re.escape(section)}:\s*(#.*)?$", l))
    except StopIteration:
        lines += [f"{section}:\n", f"  {key}: {value}\n"]
        path.write_text("".join(lines), encoding="utf-8")
        return
    i, indent = start + 1, "  "
    while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
        m = re.match(rf"^(\s+){re.escape(key)}:(\s*\S[^#\n]*?)?(\s+#.*)?$", lines[i].rstrip("\n"))
        if m:
            lines[i] = f"{m.group(1)}{key}: {value}{m.group(3) or ''}\n"
            path.write_text("".join(lines), encoding="utf-8")
            return
        if lines[i].strip():
            indent = re.match(r"^(\s+)", lines[i]).group(1)
        i += 1
    lines.insert(start + 1, f"{indent}{key}: {value}\n")
    path.write_text("".join(lines), encoding="utf-8")


def _yaml_str(v: str) -> str:
    """Quote a value as a double-quoted YAML string.

    Args:
        v: Value to quote.

    Returns:
        The quoted string.
    """
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _is_admin() -> bool:
    """Whether the agent runs as Administrator (Windows) or root."""
    if IS_WINDOWS:
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0


def _accept_eula(a):
    """Show the EULA, require it to be accepted, and record the acceptance in EULA-ACCEPTED.json.

    A Community package has no EULA.md: the agent is licensed under the AGPL (LICENSE), which
    needs no acceptance, so nothing is asked or recorded.

    Args:
        a: Parsed install arguments (--accept-eula skips the prompt).
    """
    eula = BASE / "EULA.md"
    if not eula.exists():
        if not (BASE / "LICENSE").exists():
            die("Neither EULA.md nor LICENSE is in the agent package - do not install.")
        say("Licensed under the GNU AGPL v3 or later (see LICENSE).")
        return
    if not a.accept_eula:
        say(f"Read the End User Licence Agreement before continuing: {eula}")
        if input("Type ACCEPT to accept the EULA and continue: ").strip() != "ACCEPT":
            die("EULA not accepted - nothing was changed.")
    rec = {"product": "Axara AIOps agent", "version": version(),
           "eula_sha256": hashlib.sha256(eula.read_bytes()).hexdigest(),
           "accepted_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "accepted_by": getpass.getuser(), "host": socket.gethostname().lower(),
           "method": "--accept-eula" if a.accept_eula else "interactive"}
    (BASE / "EULA-ACCEPTED.json").write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")


def _install_browser(src):
    """Copy the bundled Chromium into the agent's ms-playwright folder for browser monitors.

    Args:
        src: Chromium folder, or empty to look next to the agent package.
    """
    candidates = [Path(src)] if src else [BASE.parent / f"chromium-{OS_TAG}", BASE / f"chromium-{OS_TAG}"]
    found = next((c for c in candidates if c.is_dir()), None)
    if not found:
        die("Chromium not found. Extract chromium-" + OS_TAG + " from the bundle next to the agent folder, "
            "or pass its folder: --with-browser <DIR>")
    dest = BASE / "ms-playwright"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(found, dest)
    if not IS_WINDOWS:
        for p in dest.rglob("*"):
            if p.is_file() and (p.name in ("chrome", "headless_shell", "chrome_crashpad_handler")
                                or p.suffix == ".sh"):
                p.chmod(0o755)
    say(f"Chromium installed for browser monitors ({dest})")


def cmd_install(a):
    """Install the agent: accept the EULA, write the server settings, register, and add the service.

    Args:
        a: Parsed install arguments.
    """
    if not a.no_service and not _is_admin():
        die("Run as " + ("Administrator" if IS_WINDOWS else "root (sudo)") +
            " to register the service, or pass --no-service.")
    _accept_eula(a)
    prepare_runtime()                                    # creates config files from templates
    boot = CONFIG / "bootstrap.yaml"
    if not boot.exists():
        die(f"{boot} is missing - the agent package is incomplete.")
    if not re.match(r"^https://[^\s/]+(:\d+)?/?$", a.server_url):
        die("--server-url must look like https://<server>:9090")
    _set_yaml_key(boot, "central_server", "url", _yaml_str(a.server_url.rstrip("/")))
    if a.server_ca:
        ca = Path(a.server_ca)
        if not ca.is_file():
            die(f"--server-ca {ca}: file not found")
        dest = CONFIG / "server-ca.crt"
        shutil.copyfile(ca, dest)
        _set_yaml_key(boot, "central_server", "ssl_verify", _yaml_str(str(dest)))
        say(f"The agent will verify the server certificate against {dest}")
    elif re.search(r"(?m)^\s+ssl_verify:\s*false", boot.read_text(encoding="utf-8")):
        say("WARNING: ssl_verify is false - the agent will NOT authenticate the server. "
            "Re-run with --server-ca <server.crt or CA bundle>.")
    if a.app_name:
        _set_yaml_key(boot, "application", "name", _yaml_str(a.app_name))
    if a.environment:
        _set_yaml_key(boot, "application", "environment", _yaml_str(a.environment))
    if a.with_browser is not None:
        _install_browser(a.with_browser or None)
    if not IS_WINDOWS:
        for d in (CONFIG, BASE / "logs", BASE / "queue"):
            d.mkdir(parents=True, exist_ok=True)
            d.chmod(0o700)                               # API key and monitor credentials live here
        for p in [EXE] if EXE else []:
            p.chmod(0o755)
    say(f"Configured {boot}")
    _ensure_registered(a)
    if a.no_service:
        say(f"Start the agent with: {EXE or 'python axara_agent.py'} run")
        return 0
    _install_service()
    return 0


def _read_yaml(path: Path) -> dict:
    """Load a YAML file.

    Args:
        path: File to read.

    Returns:
        Its contents, or an empty dict when the file does not exist.
    """
    import yaml
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}


def _registration() -> dict:
    """server_id / api_key from agent_config.yaml ({} when not registered)."""
    agent = (_read_yaml(CONFIG / "agent_config.yaml").get("agent") or {})
    return agent if agent.get("server_id") and agent.get("api_key") else {}


def _enrolment_token(a) -> str:
    """The enrolment token for the first registration.

    Read from --enrol-token-file, --enrol-token or AXARA_ENROL_TOKEN, else asked for when
    running interactively.

    Args:
        a: Parsed install arguments.

    Returns:
        The token.
    """
    if a.enrol_token_file:
        p = Path(a.enrol_token_file)
        if not p.is_file():
            die(f"--enrol-token-file {p}: file not found")
        tok = p.read_text(encoding="utf-8").strip()
    else:
        tok = (a.enrol_token or os.environ.get("AXARA_ENROL_TOKEN") or "").strip()
    if not tok and sys.stdin is not None and sys.stdin.isatty():
        say("This agent must be registered with the server. An administrator creates an enrolment "
            "token in the dashboard (Agent enrolment).")
        tok = getpass.getpass("Enrolment token: ").strip()
    if not tok:
        die("An enrolment token is required for the first install: --enrol-token-file <file> "
            "(or the AXARA_ENROL_TOKEN environment variable).")
    return tok


def _ensure_registered(a):
    """Register now (first install), so a bad token or an unreachable server is reported here
    rather than later in the service log. The token is used once and never written to disk."""
    reg = _registration()
    if reg:
        say(f"Already registered with the server (server id {reg['server_id']}) - keeping that registration")
        return
    tok = _enrolment_token(a)
    from axara.agent.core.agent import MonitoringAgent
    agent = MonitoringAgent(config_dir=str(CONFIG))
    agent._load_or_create_config()
    if not agent._register_and_configure(enrolment_token=tok):
        die(f"Registration failed - {agent.registration_error}")
    say(f"Registered with the server (server id {agent.server_id})")


# ------------------------------------------------------------------ service

def _run(cmd, check=True):
    """Run a command and capture its output.

    Args:
        cmd: Command and arguments.
        check: Raise when the command fails.

    Returns:
        The completed process.
    """
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _exe_or_die() -> Path:
    """The compiled agent executable; exits when running from source (services need the executable)."""
    if not EXE:
        die("Services are registered only by the compiled agent (axara-agent / axara-agent.exe).")
    return EXE


def _install_service():
    """Register and start the agent service (Windows service or systemd unit)."""
    exe = _exe_or_die()
    if IS_WINDOWS:
        bin_path = f'"{exe}" service'
        exists = _run(["sc.exe", "query", SERVICE_NAME], check=False).returncode == 0
        if exists:
            _run(["sc.exe", "stop", SERVICE_NAME], check=False)
            _run(["sc.exe", "config", SERVICE_NAME, "binPath=", bin_path, "start=", "auto"])
        else:
            _run(["sc.exe", "create", SERVICE_NAME, "binPath=", bin_path, "start=", "auto",
                  "DisplayName=", SERVICE_DISPLAY])
        _run(["sc.exe", "description", SERVICE_NAME, "Axara AIOps monitoring agent"], check=False)
        _run(["sc.exe", "failure", SERVICE_NAME, "reset=", "86400",
              "actions=", "restart/60000/restart/60000/restart/60000"], check=False)
        time.sleep(2)
        r = _run(["sc.exe", "start", SERVICE_NAME], check=False)
        if r.returncode not in (0, 1056):                # 1056 = already running
            die(f"Service registered but did not start: {r.stdout.strip() or r.stderr.strip()}")
        say(f"Windows service '{SERVICE_NAME}' registered (automatic start) and started")
        return
    if not shutil.which("systemctl"):
        die("systemd was not found. Re-run with --no-service and start the agent with your own tooling.")
    unit_dir = Path(os.environ.get("AXARA_SYSTEMD_DIR", "/etc/systemd/system"))
    q = lambda p: f'"{p}"' if " " in str(p) else str(p)
    unit = (
        "[Unit]\nDescription=Axara AIOps monitoring agent\n"
        "After=network-online.target\nWants=network-online.target\n\n"
        "[Service]\nType=simple\n"
        f"WorkingDirectory={q(BASE)}\nExecStart={q(exe)} run\n"
        "Restart=always\nRestartSec=30\nUser=root\n\n"
        "[Install]\nWantedBy=multi-user.target\n")
    (unit_dir / SYSTEMD_UNIT).write_text(unit, encoding="utf-8")
    _run(["systemctl", "daemon-reload"])
    _run(["systemctl", "enable", "--now", SYSTEMD_UNIT])
    say(f"systemd service '{SYSTEMD_UNIT}' enabled and started")


def _is_installed_agent_folder() -> bool:
    """--purge deletes BASE: only ever an unpacked agent package, never a source checkout."""
    licence = (BASE / "EULA.md").is_file() or (BASE / "LICENSE").is_file()
    return (BASE / "VERSION").is_file() and licence and CONFIG.is_dir() \
        and BASE.parent != BASE and len(BASE.parts) > 2


def _deregister(force: bool):
    """Tell the server to remove this agent (archive its monitors, delete its configuration,
    free its licence slot, revoke its key). Uses the agent's own API key."""
    reg = _registration()
    if not reg:
        say("This agent is not registered with a server - nothing to remove there")
        return
    server = _read_yaml(CONFIG / "bootstrap.yaml").get("central_server") or {}
    url = (server.get("url") or reg.get("central_server_url") or "").rstrip("/")
    verify = server.get("ssl_verify", reg.get("ssl_verify", True))
    import requests
    try:
        r = requests.post(f"{url}/api/v1/agents/deregister", timeout=30, verify=verify,
                          headers={"Authorization": f"Bearer {reg['api_key']}"})
    except Exception as e:                               # unreachable, TLS failure, ...
        r, err = None, str(e)
    if r is not None and r.status_code == 200:
        d = r.json()
        say(f"Removed from the server {url}: {d.get('monitors_archived', 0)} monitor(s) archived, "
            f"configuration deleted, licence slot freed (server id {reg['server_id']})")
        return
    if r is not None and r.status_code == 401:
        say("The server no longer recognises this agent (already removed in the dashboard) - continuing")
        return
    reason = err if r is None else f"HTTP {r.status_code}: {r.text[:200]}"
    if not force:
        die(f"Could not remove this agent from the server {url} ({reason}). Nothing was changed. "
            "Re-run when the server is reachable, or add --force to remove it from this host only "
            "and then remove it in the dashboard (Servers > host > Remove agent).")
    say(f"WARNING: could not remove this agent from the server ({reason}). Remove it in the dashboard: "
        f"Servers > {socket.gethostname()} > Remove agent.")


def _wait_windows_service_gone(timeout=30):
    """Wait until the Windows service is stopped or gone.

    Args:
        timeout: Seconds to wait at most.
    """
    end = time.time() + timeout
    while time.time() < end:
        out = _run(["sc.exe", "query", SERVICE_NAME], check=False).stdout or ""
        if "STOPPED" in out or "1060" in out or not out.strip():
            return
        time.sleep(1)


def _purge_files():
    """Delete the agent folder (and the installer's parent folder when nothing else is in it).

    On Windows the running executable cannot delete itself, so the remainder is removed by a
    short-lived cleanup process after exit.
    """
    parent = BASE.parent
    sibling_files = {p.name for p in parent.iterdir()} - {BASE.name}
    remove_parent = sibling_files <= {"EULA-ACCEPTED.json"}       # only what the installer put there
    if not IS_WINDOWS:
        shutil.rmtree(BASE)
        if remove_parent:
            (parent / "EULA-ACCEPTED.json").unlink(missing_ok=True)
            try:
                parent.rmdir()
            except OSError:
                pass
        say(f"Deleted {BASE}" + (f" and {parent}" if remove_parent and not parent.exists() else ""))
        return
    # Windows: a running program cannot delete itself. Delete everything else now, the rest
    # (this executable and the folder) a few seconds after we exit.
    os.chdir(parent)
    for child in BASE.iterdir():
        if EXE and child.resolve() == EXE:
            continue
        try:
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        except OSError as e:
            say(f"WARNING: could not delete {child} now ({e}); it is retried after exit")
    script = f'ping -n 4 127.0.0.1 >nul & rmdir /s /q "{BASE}"'
    if remove_parent:
        (parent / "EULA-ACCEPTED.json").unlink(missing_ok=True)
        script += f' & rmdir "{parent}"'                 # without /s: only if it is empty
    flags = 0x00000008 | 0x00000200 | 0x08000000         # DETACHED | NEW_PROCESS_GROUP | NO_WINDOW
    subprocess.Popen(["cmd.exe", "/c", script], creationflags=flags, close_fds=True,
                     cwd=os.environ.get("SystemRoot", "C:\\Windows"))
    say(f"{BASE} is deleted a few seconds after this command ends. If your command prompt is "
        "inside that folder, change to another folder (cd \\) so it can be removed.")


def cmd_uninstall(a=None):
    """Remove the service; with --purge also remove the agent from the server and delete its files.

    Args:
        a: Parsed uninstall arguments (purge, yes, force).

    Returns:
        Exit status.
    """
    if not _is_admin():
        die("Run as " + ("Administrator" if IS_WINDOWS else "root (sudo)") + ".")
    purge = bool(getattr(a, "purge", False))
    if purge:
        if not _is_installed_agent_folder():
            die(f"{BASE} does not look like an installed agent package - --purge refused.")
        if not getattr(a, "yes", False):
            if sys.stdin is None or not sys.stdin.isatty():
                die("--purge permanently deletes the agent; add --yes to confirm non-interactively.")
            say(f"This removes the agent from the server (its monitors are archived, its configuration "
                f"deleted, its licence slot freed) and deletes {BASE}.")
            if input("Type REMOVE to continue: ").strip() != "REMOVE":
                die("Not confirmed - nothing was changed.")
        _deregister(force=bool(getattr(a, "force", False)))
    if IS_WINDOWS:
        _run(["sc.exe", "stop", SERVICE_NAME], check=False)
        r = _run(["sc.exe", "delete", SERVICE_NAME], check=False)
        say("Windows service removed" if r.returncode == 0 else "No Windows service was registered")
        if purge:
            _wait_windows_service_gone()
    else:
        unit = Path(os.environ.get("AXARA_SYSTEMD_DIR", "/etc/systemd/system")) / SYSTEMD_UNIT
        if shutil.which("systemctl"):
            _run(["systemctl", "disable", "--now", SYSTEMD_UNIT], check=False)
        if unit.exists():
            unit.unlink()
            if shutil.which("systemctl"):
                _run(["systemctl", "daemon-reload"], check=False)
            say("systemd service removed")
        else:
            say("No systemd service was registered")
    if purge:
        _purge_files()
        return 0
    say(f"Agent files are kept in {BASE}. To remove the agent completely (server side too), "
        "run: axara-agent uninstall --purge")
    return 0


def cmd_status(_a=None):
    """Print the version, service state, registration and server URL (the 'status' command).

    Args:
        _a: Parsed arguments (unused).

    Returns:
        Exit status.
    """
    if IS_WINDOWS:
        r = _run(["sc.exe", "query", SERVICE_NAME], check=False)
        m = re.search(r"STATE\s*:\s*\d+\s+(\w+)", r.stdout or "")
        state = m.group(1) if m else "not installed"
    elif shutil.which("systemctl"):
        state = _run(["systemctl", "is-active", SYSTEMD_UNIT], check=False).stdout.strip() or "unknown"
    else:
        state = "no service manager"
    cfg = CONFIG / "agent_config.yaml"
    registered = bool(cfg.exists() and re.search(r"(?m)^\s*server_id:\s*[0-9a-fA-F-]{8,}", cfg.read_text(encoding="utf-8")))
    boot = (CONFIG / "bootstrap.yaml")
    url = re.search(r'(?m)^\s+url:\s*"?([^"\n#]+)', boot.read_text(encoding="utf-8")) if boot.exists() else None
    say(f"version {version()} | service: {state} | registered: {'yes' if registered else 'no'} | "
        f"server: {url.group(1).strip() if url else '-'}")
    return 0


def _windows_service_dispatch():
    """Entry point when the Windows service manager starts `axara-agent.exe service`."""
    import threading
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil

    class AgentService(win32serviceutil.ServiceFramework):
        """Windows service wrapper that runs the agent on a worker thread."""
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = SERVICE_DISPLAY

        def __init__(self, args):
            """Create the stop event.

            Args:
                args: Service arguments from the service manager.
            """
            super().__init__(args)
            self.stop_event = win32event.CreateEvent(None, 0, 0, None)

        def SvcStop(self):
            """Signal the service to stop."""
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self.stop_event)

        def SvcDoRun(self):
            """Run the agent until stopped; exit with an error if the agent thread ends on its own,
            so the service recovery actions restart it.
            """
            worker = threading.Thread(target=cmd_run, daemon=True, name="axara-agent")
            worker.start()
            while win32event.WaitForSingleObject(self.stop_event, 5000) == win32event.WAIT_TIMEOUT:
                if not worker.is_alive():
                    # The agent stopped on its own (e.g. server unreachable at registration). Exit
                    # without reporting "stopped" so the service recovery actions restart it.
                    servicemanager.LogErrorMsg("Axara agent stopped unexpectedly; the service will restart it")
                    os._exit(1)

    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(AgentService)
    servicemanager.StartServiceCtrlDispatcher()


# ------------------------------------------------------------------ CLI

# Packages the agent needs before it can do anything (module -> pip name). The compiled agent
# has them built in; from source they come from requirements.txt.
REQUIRED_MODULES = {"yaml": "PyYAML", "requests": "requests", "psutil": "psutil"}


def _check_packages():
    """Stop with a clear message, before anything is changed, when a required package is missing.

    Only applies when running from source; the compiled agent has every package built in.
    """
    if _compiled():
        return
    import importlib.util
    missing = [pip for mod, pip in REQUIRED_MODULES.items() if importlib.util.find_spec(mod) is None]
    if missing:
        die(f"Python packages missing: {', '.join(missing)}. Install the agent's packages first "
            f"(in the agent's virtual environment):  python -m pip install -r "
            f"{BASE / 'requirements.txt'}  -  nothing was changed.")


def main(argv=None):
    """Command-line entry point: run, install, uninstall, status, selftest or --run-script.

    Args:
        argv: Arguments after the program name (default sys.argv[1:]).

    Returns:
        Exit status.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--run-script"]:
        return cmd_run_script(argv[1:])
    if argv[:1] == ["service"] and IS_WINDOWS:
        return _windows_service_dispatch()
    ap = argparse.ArgumentParser(prog="axara-agent", usage=USAGE)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run")
    i = sub.add_parser("install")
    i.add_argument("--server-url", required=True)
    i.add_argument("--server-ca")
    i.add_argument("--with-browser", nargs="?", const="", default=None, metavar="DIR")
    i.add_argument("--app-name")
    i.add_argument("--environment")
    i.add_argument("--no-service", action="store_true")
    i.add_argument("--accept-eula", action="store_true")
    i.add_argument("--enrol-token-file", metavar="FILE")
    i.add_argument("--enrol-token", metavar="TOKEN")
    u = sub.add_parser("uninstall")
    u.add_argument("--purge", action="store_true",
                   help="also remove the agent from the server and delete this folder")
    u.add_argument("--force", action="store_true",
                   help="with --purge: continue when the server cannot be reached")
    u.add_argument("--yes", action="store_true", help="with --purge: do not ask for confirmation")
    for name in ("status", "selftest", "version"):
        sub.add_parser(name)
    a = ap.parse_args(argv)
    if a.cmd != "version":
        _check_packages()
    if a.cmd in (None, "run"):
        return cmd_run(a)
    if a.cmd == "version":
        print(version())
        return 0
    return {"install": cmd_install, "uninstall": cmd_uninstall, "status": cmd_status,
            "selftest": cmd_selftest}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main() or 0)
