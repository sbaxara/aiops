#!/usr/bin/env python3
# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Add the copyright and SPDX licence header to source files that lack one (idempotent).

Keeps shebang/encoding lines first and preserves each file's line endings.

Usage (repository root):
  python monitoring-server/tools/add_copyright.py [--check]                 the Community sources (AGPL)
  python monitoring-server/tools/add_copyright.py [--check] --proprietary PATH...
                                                                            other code (all rights reserved)
PATH may be a folder or a file; without PATH the Community source folders are used.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGPL = ("Copyright (c) 2026 Axara Pty Ltd.", "SPDX-License-Identifier: AGPL-3.0-or-later")
PROPRIETARY = ("Copyright (c) 2026 Axara Pty Ltd. All rights reserved.",
               "SPDX-License-Identifier: LicenseRef-Axara-Proprietary")
COMMUNITY_PATHS = [
    "monitoring-server/axara/community", "monitoring-server/config/monitor_templates",
    "monitoring-server/tools", "monitoring-server/tests", "monitoring-server/manage.py",
    "monitoring-server/ensure_env.py", "monitoring-server/dashboard/src",
    "monitoring-agent/axara/agent", "monitoring-agent/axara_agent.py", "monitoring-agent/tests",
    "release/agent_build",
]
EXTS = {".py": "#", ".js": "//", ".jsx": "//"}
SKIP_PARTS = {"__pycache__", ".venv", "node_modules", "dist"}


def targets(paths):
    """Source files under the given paths.

    Args:
        paths: Folders or files, relative to the repository root.

    Yields:
        (path, comment mark).
    """
    for rel in paths:
        p = ROOT / rel
        files = [p] if p.is_file() else sorted(p.rglob("*")) if p.is_dir() else []
        for f in files:
            if f.suffix in EXTS and f.is_file() and not SKIP_PARTS & set(f.parts):
                yield f, EXTS[f.suffix]


def add(path: Path, mark: str, header, check: bool) -> bool:
    """Add the header to a file that has no SPDX licence line.

    Args:
        path: File.
        mark: Comment mark ('#' or '//').
        header: The two header lines.
        check: Only report, do not write.

    Returns:
        True when the file lacks (or was given) the header.
    """
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw[3:].decode("utf-8") if bom else raw.decode("utf-8")
    if "SPDX-License-Identifier" in "\n".join(text.splitlines()[:6]):
        return False
    if check:
        return True
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and i < 2 and (lines[i].startswith("#!") or re.match(r"#.*coding[:=]", lines[i])):
        i += 1
    lines[i:i] = [f"{mark} {h}{eol}" for h in header]
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + "".join(lines).encode("utf-8"))
    return True


def main(argv) -> int:
    """Command-line entry point.

    Args:
        argv: Arguments without the program name.

    Returns:
        Exit code: 1 when --check finds files without a header.
    """
    check = "--check" in argv
    proprietary = "--proprietary" in argv
    paths = [a for a in argv if not a.startswith("--")] or ([] if proprietary else COMMUNITY_PATHS)
    if proprietary and not paths:
        print("--proprietary needs at least one PATH")
        return 2
    header = PROPRIETARY if proprietary else AGPL
    changed = [p for p, m in targets(paths) if add(p, m, header, check)]
    for p in changed:
        print(("missing: " if check else "added: ") + str(p.relative_to(ROOT)))
    print(f"{len(changed)} file(s) {'missing a header' if check else 'updated'}")
    return 1 if check and changed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
