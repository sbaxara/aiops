# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Scripts API Routes
Serves monitor script metadata and content for agent auto-update.
"""
from fastapi import APIRouter, HTTPException
import os
from pathlib import Path
import hashlib
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/scripts", tags=["scripts"])

# scripts.py lives at monitoring-server/axara/community/api/routes/scripts.py, so parents[4] is
# monitoring-server/; the monitor templates are in monitoring-server/config/monitor_templates/
# (the image sets MONITOR_TEMPLATES_DIR=/app/config/monitor_templates).
_SCRIPTS_DIR = Path(os.getenv("MONITOR_TEMPLATES_DIR",
                             Path(__file__).resolve().parents[4] / "config" / "monitor_templates"))


def _sha256(path: Path) -> str:
    """
    Calculate SHA-256 checksum in binary mode.
    Binary mode ensures identical checksums on Windows and Linux
    regardless of line-ending differences.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _get_version(path: Path) -> str:
    """Extract __version__ = '...' from a script. Returns 'unknown' if absent."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("__version__"):
                    return line.split("=")[1].strip().strip("\"'")
    except Exception:
        pass
    return "unknown"


def _script_dirs() -> list:
    """Folders monitor scripts are served from: the core templates, then plugin folders
    active now.

    Returns:
        Paths, core first (a plugin script never replaces a core script of the same name).
    """
    dirs = [_SCRIPTS_DIR]
    try:
        from axara.community.plugins.loader import get_registry
        dirs += get_registry().monitor_template_dirs()
    except Exception:
        logger.warning("plugin monitor folders unavailable", exc_info=True)
    return dirs


def _build_registry() -> dict:
    """
    Dynamically scan the script folders for *_monitor.py files.
    Called per-request so no server restart is needed after script updates.

    Returns:
        {script name: {"version", "checksum", "path"}}.
    """
    registry = {}
    if not _SCRIPTS_DIR.exists():
        logger.warning(f"monitor_templates directory not found: {_SCRIPTS_DIR}")
    for folder in _script_dirs():
        if not Path(folder).is_dir():
            continue
        for path in sorted(Path(folder).glob("*_monitor.py")):
            if path.name in registry:
                continue
            registry[path.name] = {
                "version":  _get_version(path),
                "checksum": _sha256(path),
                "path":     path,
            }
    return registry


@router.get("/")
def list_scripts():
    """List all available monitor scripts with version and checksum."""
    registry = _build_registry()
    return [
        {"script_name": name, "version": info["version"], "checksum": info["checksum"]}
        for name, info in registry.items()
    ]


@router.get("/{script_name}/{version}")
def get_script(script_name: str, version: str):
    """
    Return the content of a monitor script.
    Called by the agent auto-updater when a newer version is available.
    """
    # Path traversal guard
    if "/" in script_name or "\\" in script_name or ".." in script_name:
        raise HTTPException(status_code=400, detail="Invalid script name")

    registry = _build_registry()

    if script_name not in registry:
        raise HTTPException(status_code=404, detail=f"Script '{script_name}' not found")

    registered = registry[script_name]
    if registered["version"] != version:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Version {version} not found for {script_name}. "
                f"Latest is {registered['version']}"
            ),
        )

    script_path = registered["path"]
    if not script_path.exists():
        logger.warning(f"Script file not found on disk: {script_path}")
        raise HTTPException(
            status_code=503,
            detail=f"Script file not available on server: {script_name}",
        )

    return {
        "script_name": script_name,
        "version":     version,
        "checksum":    registered["checksum"],
        "content":     script_path.read_text(encoding="utf-8"),
    }
