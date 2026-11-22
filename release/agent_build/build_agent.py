#!/usr/bin/env python3
# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the compiled Axara agent (vendor side). Customers need no Python, Docker or other software.

  python release/agent_build/build_agent.py --os windows --version 1.0.0 --out DIR   (run on Windows)
  python release/agent_build/build_agent.py --os linux   --version 1.0.0 --out DIR   (needs Docker)

Windows is compiled natively (Nuitka downloads its own C compiler on first use). Linux is compiled
inside a manylinux2014 container (glibc 2.17), so the agent runs on RHEL/CentOS 7+, Ubuntu 18.04+
and Debian 9+. Browser monitors on Linux need glibc 2.28+ (RHEL 8+, Ubuntu 20.04+, Debian 10+), because
Playwright's Node.js driver and Chromium require it; Linux Chromium is therefore fetched in a separate
modern container. Docker is used only here, at build time.

Outputs in DIR:
  axara-agent-<version>-<os>-x64.zip|.tar.gz    the agent folder (executable, monitors, config templates)
  chromium-<os>-x64.zip|.tar.gz                 Chromium headless shell for browser monitors (optional)

Optional additions (for builds that add extensions):
  --extension DIR        an agent extension package (folder named agent_<name>, see
                         axara/agent/core/extensions.py), compiled into the agent; its
                         requirements.txt, if any, is installed too; repeatable
  --extra-monitors DIR   more monitor scripts to ship with the agent; repeatable
  --eula FILE            a licence file to ship as EULA.md next to LICENSE

The agent core (agent loop, registration, collector, updaters, sender) and any extensions are
compiled to native code with docstrings removed. Monitor scripts stay as .py files because the server
pushes updated monitors to agents at run time; every module they import is compiled into the agent.
Standard library only, so it runs from any Python 3.9+ on the build machine.
"""
import argparse
import ast
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
AGENT = ROOT / "monitoring-agent"
MONITORS = AGENT / "axara" / "agent" / "monitors"
NUITKA = "nuitka==4.3.5"
BUILD_PYTHON = "3.12"                                   # the agent's embedded Python
MANYLINUX_IMAGE = "quay.io/pypa/manylinux2014_x86_64"
LINUX_IMAGE_TAG = "axara/agent-builder:manylinux2014-py312"
CHROMIUM_FETCH_IMAGE = "python:3.12-slim-bookworm"     # glibc 2.36: can run Playwright's Node.js driver
PLAYWRIGHT_PIN = "playwright==1.65.2"                  # must match monitoring-agent/requirements.txt

# Imported by every agent; Windows-only modules are added on Windows.
ALWAYS = ["playwright.sync_api", "playwright._impl._driver"]
WINDOWS_ONLY = ["pythoncom", "pywintypes", "win32api", "win32con", "win32event",
                "win32service", "win32serviceutil", "servicemanager"]
NOT_ON_LINUX = {"wmi", "pythoncom", "pywintypes", "win32api", "win32con", "win32com",
                "win32event", "win32service", "win32serviceutil", "servicemanager", "winreg", "msvcrt",
                "_winapi", "nt"}


def log(msg):
    """Print a build message."""
    print(f"[agent-build] {msg}", flush=True)


def run(cmd, **kw):
    """Run a command, echoing it first; raises CalledProcessError on failure."""
    log("$ " + " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def os_tag(target_os: str) -> str:
    """Package OS tag for a target OS (e.g. linux-x64)."""
    return f"{target_os}-x64"


# ------------------------------------------------------------------ dependency scan

def scan_imports(files):
    """Top-level module names imported by a set of Python files.

    Args:
        files: Python source files.

    Returns:
        Set of top-level module names.
    """
    names = set()
    for f in files:
        tree = ast.parse(Path(f).read_text(encoding="utf-8"), filename=str(f))
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names |= {a.name for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                names.add(n.module)
    return names


def monitor_files(extra_monitors=()):
    """Monitor scripts shipped with the agent: the standard ones, then any extra folders.

    Args:
        extra_monitors: Extra monitor folders.

    Returns:
        {file name: path}; a later folder replaces a file of the same name.
    """
    files = {}
    for folder in (MONITORS, *[Path(d) for d in extra_monitors]):
        for f in sorted(folder.glob("*.py")):
            files[f.name] = f
    return files


def check_extension(folder: Path) -> str:
    """Validate an extension folder and return its module name.

    Args:
        folder: Extension package folder.

    Returns:
        'axara.<folder name>'.
    """
    if not folder.name.startswith("agent_") or not (folder / "__init__.py").is_file():
        sys.exit(f"--extension {folder}: must be a package folder named agent_<name>")
    return f"axara.{folder.name}"


def deps_module(target_os: str, extra_monitors=(), extensions=()) -> str:
    """Source of _agent_deps.py: imports every module the (uncompiled) monitors and browser
    journey scripts use, so Nuitka compiles them into the agent."""
    files = list(monitor_files(extra_monitors).values()) + list((AGENT / "scripts" / "browser").glob("*.py"))
    for ext in extensions:
        files += list(Path(ext).rglob("*.py"))
    mods = {m for m in scan_imports(files) if not m.split(".")[0] in ("axara", "__future__")}
    mods |= set(ALWAYS)
    if target_os == "windows":
        mods |= set(WINDOWS_ONLY)
    else:
        mods = {m for m in mods if m.split(".")[0] not in NOT_ON_LINUX}
    lines = ["# Generated by release/agent_build/build_agent.py - modules used by monitors and scripts.",
             "# Each import is optional at run time so a missing optional driver never stops the agent."]
    for m in sorted(mods):
        lines += ["try:", f"    import {m}  # noqa: F401", "except Exception:", "    pass"]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ native build

def build_native(target_os: str, version: str, out: Path, browser: bool, extensions=(), extra_monitors=(),
                 eula=None):
    """Compile on THIS machine (Windows host, or inside the manylinux container).

    Args:
        target_os: 'windows' or 'linux'.
        version: Agent version.
        out: Output folder.
        browser: Also build the Chromium package.
        extensions: Extension package folders to compile in.
        extra_monitors: Extra monitor folders to ship.
        eula: Licence file shipped as EULA.md, or None.
    """
    ext_names = [check_extension(Path(e)) for e in extensions]
    if (target_os == "windows") != (os.name == "nt"):
        sys.exit(f"--os {target_os} must be compiled on {target_os}")
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    if py_ver != BUILD_PYTHON:
        log(f"WARNING: building with Python {py_ver}; releases use Python {BUILD_PYTHON}")
    out.mkdir(parents=True, exist_ok=True)
    tag = os_tag(target_os)
    work = Path(tempfile.mkdtemp(prefix="axara-agent-build-"))
    try:
        venv = work / "venv"
        run([sys.executable, "-m", "venv", venv])
        vpy = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run([vpy, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
        extra = ["patchelf"] if target_os == "linux" and not shutil.which("patchelf") else []
        ext_reqs = [a for e in extensions if (Path(e) / "requirements.txt").is_file()
                    for a in ("-r", Path(e) / "requirements.txt")]
        run([vpy, "-m", "pip", "install", "--quiet", NUITKA, "ordered-set", "zstandard", *extra,
             "-r", AGENT / "requirements.txt", *ext_reqs])
        env = dict(os.environ)
        env["PATH"] = str(vpy.parent) + os.pathsep + env.get("PATH", "")

        # --- stage the compiled part: launcher + axara.agent (core, utils) (+ generated deps).
        # axara is a namespace package (no __init__.py); the monitors stay source (see below).
        src = work / "src"
        (src / "axara" / "agent").mkdir(parents=True)
        shutil.copy2(AGENT / "axara_agent.py", src / "axara_agent.py")
        shutil.copy2(AGENT / "axara" / "agent" / "__init__.py", src / "axara" / "agent" / "__init__.py")
        for pkg in ("core", "utils"):
            shutil.copytree(AGENT / "axara" / "agent" / pkg, src / "axara" / "agent" / pkg,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for ext in extensions:
            shutil.copytree(ext, src / "axara" / Path(ext).name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"))
        (src / "axara" / "agent" / "_build_extensions.py").write_text(
            "# Generated by release/agent_build/build_agent.py - extensions compiled into this agent.\n"
            f"EXTENSIONS = {tuple(ext_names)!r}\n", encoding="utf-8")
        (src / "_agent_deps.py").write_text(deps_module(target_os, extra_monitors, extensions), encoding="utf-8")
        launcher = (src / "axara_agent.py").read_text(encoding="utf-8")
        (src / "axara_agent.py").write_text(launcher.replace(
            "import argparse\n", "import argparse\nimport _agent_deps  # noqa: F401  (build-generated)\n", 1),
            encoding="utf-8")

        nums = [int(x) for x in "".join(c if c.isdigit() or c == "." else "" for c in version).split(".") if x][:4]
        file_version = ".".join(str(n) for n in (nums + [0, 0, 0, 0])[:4])
        cmd = [vpy, "-m", "nuitka", "--standalone", "--assume-yes-for-downloads",
               "--python-flag=no_docstrings", "--remove-output",
               "--include-module=axara.agent", "--include-package=axara.agent.core",
               "--include-package=axara.agent.utils", "--include-module=_agent_deps",
               "--include-module=axara.agent._build_extensions",
               *[f"--include-package={n}" for n in ext_names],
               "--nofollow-import-to=axara.agent.monitors", "--playwright-include-browser=none",
               f"--output-dir={work / 'build'}", "--output-filename=axara-agent",
               "--company-name=Axara Pty Ltd", "--product-name=Axara AIOps Agent",
               f"--file-version={file_version}", f"--product-version={file_version}",
               "--copyright=Copyright (c) 2026 Axara Pty Ltd. All rights reserved.",
               src / "axara_agent.py"]
        run(cmd, cwd=src, env=env)
        dist = work / "build" / "axara_agent.dist"
        if not dist.is_dir():
            sys.exit("Nuitka did not produce axara_agent.dist")
        leaked = [p for p in dist.rglob("*.py") if "playwright" not in p.parts]
        if leaked:
            sys.exit(f"Refusing to package: agent source in the build output: {leaked[:5]}")

        # --- assemble the agent folder
        pkg = work / "package" / "axara-agent"
        shutil.copytree(dist, pkg)
        # Monitor scripts ship as source: the server updates them, core.monitor_loader loads them.
        (pkg / "axara" / "agent" / "monitors").mkdir(parents=True)
        for name, f in monitor_files(extra_monitors).items():
            shutil.copy2(f, pkg / "axara" / "agent" / "monitors" / name)
        (pkg / "config").mkdir()
        shutil.copy2(AGENT / "config" / "bootstrap.yaml", pkg / "config" / "bootstrap.example.yaml")
        shutil.copy2(AGENT / "config" / "agent_config_example.yaml", pkg / "config" / "agent_config_example.yaml")
        (pkg / "scripts" / "browser").mkdir(parents=True)
        shutil.copy2(HERE / "example_journey.py", pkg / "scripts" / "browser" / "example_journey.py")
        for d in ("logs", "queue"):
            (pkg / d).mkdir()
        shutil.copy2(AGENT / "LICENSE", pkg / "LICENSE")          # AGPL-3.0: the agent is open source
        if eula:
            shutil.copy2(eula, pkg / "EULA.md")
        shutil.copy2(HERE / "AGENT-README.txt", pkg / "README.txt")
        (pkg / "VERSION").write_text(version + "\n", encoding="utf-8")

        name = f"axara-agent-{version}-{tag}"
        archive = _archive(pkg, out / name, target_os, executables={"axara-agent"})
        log(f"agent package: {archive}")

        if browser:
            fetch_chromium(target_os, out, vpy, env)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def fetch_chromium(target_os: str, out: Path, vpy=None, env=None):
    """Download Playwright's Chromium headless shell and archive it as chromium-<os>-x64."""
    tag = os_tag(target_os)
    work = Path(tempfile.mkdtemp(prefix="axara-chromium-"))
    try:
        browsers = work / f"chromium-{tag}"
        benv = dict(env or os.environ, PLAYWRIGHT_BROWSERS_PATH=str(browsers))
        run([vpy or sys.executable, "-m", "playwright", "install", "--only-shell", "chromium"], env=benv)
        arc = _archive(browsers, out / f"chromium-{tag}", target_os,
                       executables={"headless_shell", "chrome_crashpad_handler"})
        log(f"browser package: {arc}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _archive(folder: Path, base: Path, target_os: str, executables=()):
    """zip for Windows, tar.gz with Unix modes for Linux (top-level folder = folder.name)."""
    if target_os == "windows":
        path = Path(str(base) + ".zip")         # not with_suffix: "1.0-windows-x64" is not a suffix
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for p in sorted(folder.rglob("*")):
                z.write(p, Path(folder.name) / p.relative_to(folder))
        return path
    path = Path(str(base) + ".tar.gz")

    def modes(ti: tarfile.TarInfo):
        """Normalise a tar member: executables, libraries and folders 0755, other files 0644, owner root."""
        leaf = ti.name.rsplit("/", 1)[-1]
        if ti.isdir() or leaf in executables or leaf.endswith((".so", ".sh")) or ".so." in leaf \
                or (ti.isfile() and ti.mode & 0o111):
            ti.mode = 0o755
        else:
            ti.mode = 0o644
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = ""
        return ti

    with tarfile.open(path, "w:gz", compresslevel=6) as t:
        t.add(folder, arcname=folder.name, filter=modes)
    return path


# ------------------------------------------------------------------ linux via docker

def build_linux_in_docker(version: str, out: Path, browser: bool, extensions=(), extra_monitors=(), eula=None):
    """Build the Linux agent inside the pinned build container (glibc baseline), writing to `out`.

    Args:
        version: Agent version.
        out: Output folder.
        browser: Also build the Chromium package.
        extensions: Extension package folders (mounted read-only into the container).
        extra_monitors: Extra monitor folders (mounted read-only).
        eula: Licence file (mounted read-only), or None.
    """
    if not shutil.which("docker"):
        sys.exit("Docker is required to build the Linux agent (build time only).")
    out.mkdir(parents=True, exist_ok=True)
    run(["docker", "build", "-t", LINUX_IMAGE_TAG, "-f", HERE / "Dockerfile.linux", HERE])
    ids = ["-e", f"HOST_UID={os.getuid()}", "-e", f"HOST_GID={os.getgid()}"] if hasattr(os, "getuid") else []
    vols = ["-v", f"{ROOT}:/src:ro", "-v", f"{out.resolve()}:/out"]
    extra = []
    for i, ext in enumerate(extensions):
        p = Path(ext).resolve()
        vols += ["-v", f"{p}:/ext/{i}/{p.name}:ro"]
        extra += ["--extension", f"/ext/{i}/{p.name}"]
    for i, mon in enumerate(extra_monitors):
        vols += ["-v", f"{Path(mon).resolve()}:/mon/{i}:ro"]
        extra += ["--extra-monitors", f"/mon/{i}"]
    if eula:
        vols += ["-v", f"{Path(eula).resolve()}:/eula/EULA.md:ro"]
        extra += ["--eula", "/eula/EULA.md"]
    run(["docker", "run", "--rm", *ids, *vols, LINUX_IMAGE_TAG,
         "python3.12", "/src/release/agent_build/build_agent.py",
         "--os", "linux", "--in-container", "--no-browser", "--version", version, "--out", "/out", *extra])
    if browser:
        run(["docker", "run", "--rm", *ids, *vols, CHROMIUM_FETCH_IMAGE, "sh", "-c",
             f"pip install --quiet --root-user-action=ignore {PLAYWRIGHT_PIN} && "
             "python /src/release/agent_build/build_agent.py --os linux --in-container "
             f"--chromium-only --version {version} --out /out"])


def build(target_os: str, version: str, out: Path, browser: bool = True, in_container: bool = False,
          chromium_only: bool = False, extensions=(), extra_monitors=(), eula=None):
    """Build the compiled agent package (and the Chromium package unless disabled).

    Args:
        target_os: 'windows' or 'linux'.
        version: Agent version.
        out: Output folder.
        browser: Also build the Chromium package.
        in_container: Running inside the Linux build container.
        chromium_only: Only fetch the Chromium package.
        extensions: Extension package folders to compile in.
        extra_monitors: Extra monitor folders to ship.
        eula: Licence file shipped as EULA.md, or None.
    """
    for ext in extensions:
        check_extension(Path(ext))
    if chromium_only:
        out.mkdir(parents=True, exist_ok=True)
        fetch_chromium(target_os, out)
    elif target_os == "linux" and not in_container and not os.environ.get("AXARA_AGENT_NATIVE_LINUX"):
        return build_linux_in_docker(version, out, browser, extensions, extra_monitors, eula)
    else:
        build_native(target_os, version, out, browser, extensions, extra_monitors, eula)
    uid, gid = os.environ.get("HOST_UID"), os.environ.get("HOST_GID")
    if in_container and uid and gid:
        for p in out.iterdir():
            os.chown(p, int(uid), int(gid))


def main(argv=None):
    """Command-line entry point.

    Args:
        argv: Arguments (default sys.argv[1:]).
    """
    ap = argparse.ArgumentParser(description="Build the compiled Axara agent")
    ap.add_argument("--os", required=True, choices=("windows", "linux"))
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-browser", action="store_true", help="skip the Chromium package")
    ap.add_argument("--extension", action="append", default=[], metavar="DIR",
                    help="agent extension package folder (agent_<name>) to compile in; repeatable")
    ap.add_argument("--extra-monitors", action="append", default=[], metavar="DIR",
                    help="folder of extra monitor scripts to ship; repeatable")
    ap.add_argument("--eula", metavar="FILE", help="licence file shipped as EULA.md")
    ap.add_argument("--in-container", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--chromium-only", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    build(a.os, a.version, Path(a.out), browser=not a.no_browser, in_container=a.in_container,
          chromium_only=a.chromium_only, extensions=a.extension, extra_monitors=a.extra_monitors,
          eula=a.eula)


if __name__ == "__main__":
    main()
