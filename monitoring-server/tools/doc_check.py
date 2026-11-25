#!/usr/bin/env python3
# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check the code documentation standard on source files.

Every file needs a notice header with an `SPDX-License-Identifier: AGPL-3.0-or-later` line in its
first lines
and a description of what it does. Every function and class needs a Google-style docstring
(Python) or a comment block directly above it (JavaScript, shell, batch).

Usage (repo root):
    python monitoring-server/tools/doc_check.py FILE...          check these files
    python monitoring-server/tools/doc_check.py --since REF      check files changed since a git ref
Exit status 1 when a file fails, listing each problem.
"""
import ast
import re
import subprocess
import sys
from pathlib import Path
from typing import List

SPDX = re.compile(r"SPDX-License-Identifier:\s*AGPL-3\.0-or-later")
CHECKED = (".py", ".js", ".jsx", ".sh", ".bat", ".cmd")
SKIP_PARTS = {"node_modules", "dist", ".venv", "__pycache__", "migrations"}


def header_ok(text: str) -> bool:
    """Whether the file has an SPDX licence line in its first 6 lines.

    Args:
        text: File contents.

    Returns:
        True when the header is present.
    """
    return bool(SPDX.search("\n".join(text.splitlines()[:6])))


def python_problems(text: str) -> List[str]:
    """Missing module, class and function docstrings in a Python file.

    Args:
        text: Python source.

    Returns:
        One message per problem (empty when the file passes).

    Raises:
        SyntaxError: when the source does not parse.
    """
    tree = ast.parse(text)
    out = [] if ast.get_docstring(tree) else ["module docstring missing"]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not ast.get_docstring(node):
                out.append(f"line {node.lineno}: {node.name} has no docstring")
    return out


def commented_blocks_problems(text: str, pattern: str) -> List[str]:
    """Functions in JS/shell/batch files without a comment block directly above them.

    Args:
        text: File contents.
        pattern: Regex matching a function definition line (group 1 = its name).

    Returns:
        One message per undocumented function.
    """
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        m = re.match(pattern, line)
        if not m:
            continue
        prev = next((lines[j].strip() for j in range(i - 1, -1, -1) if lines[j].strip()), "")
        if not (prev.startswith(("*/", "//", "#", "rem ", "REM ", "::")) or prev.endswith("*/")):
            name = next((g for g in m.groups() if g), "function")
            out.append(f"line {i + 1}: {name} has no comment block")
    return out


JS_FN = r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+(\w+)|^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\("
SH_FN = r"^(\w+)\s*\(\)\s*\{"
BAT_FN = r"^:(\w+)\s*$"


def problems(path: Path) -> List[str]:
    """All documentation problems of one file.

    Args:
        path: File to check.

    Returns:
        Problems found (empty when it passes or is not a checked file type).
    """
    if path.suffix not in CHECKED or SKIP_PARTS & set(path.parts) or not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace").lstrip("﻿")
    out = [] if header_ok(text) else ["SPDX licence header missing"]
    if path.suffix == ".py":
        try:
            out += python_problems(text)
        except SyntaxError as e:
            out.append(f"does not parse: {e}")
    elif path.suffix in (".js", ".jsx"):
        out += commented_blocks_problems(text, JS_FN)
    elif path.suffix == ".sh":
        out += commented_blocks_problems(text, SH_FN)
    else:
        out += [p for p in commented_blocks_problems(text, BAT_FN) if not p.split(": ")[1].upper().startswith("EOF")]
    return out


def changed_since(ref: str) -> List[Path]:
    """Files added or modified since a git ref (deleted files excluded).

    Args:
        ref: Git ref, e.g. a restore tag.

    Returns:
        Paths relative to the current directory.

    Raises:
        subprocess.CalledProcessError: when git fails.
    """
    out = subprocess.run(["git", "diff", "--name-only", "--diff-filter=AMR", ref], check=True,
                         capture_output=True, text=True).stdout.split()
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], check=True,
                               capture_output=True, text=True).stdout.split()
    return [Path(p) for p in out + untracked]


def main(argv: List[str]) -> int:
    """Command-line entry point.

    Args:
        argv: Arguments after the program name.

    Returns:
        0 when every file passes, 1 otherwise.
    """
    files = changed_since(argv[1]) if argv[:1] == ["--since"] else [Path(a) for a in argv]
    failed = 0
    for f in files:
        for p in problems(f):
            print(f"{f}: {p}")
            failed = 1
    return failed


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
