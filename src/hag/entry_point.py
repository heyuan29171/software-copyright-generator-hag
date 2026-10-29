"""Picking the file that should open the material.

A reviewer's first impression comes from page one. The strongest opening is an
entry point: the program's main module, a router table, or an application
bootstrap. It reads as "this is where the software starts", which is exactly
what the first page should say.

This is a heuristic, and deliberately a cautious one. It only promotes a file
when the evidence is unambiguous; otherwise the existing quality ordering
stands.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import PurePosixPath

# Filenames that conventionally hold an entry point.
_ENTRY_NAMES = {
    "main.py", "__main__.py", "app.py", "manage.py", "wsgi.py", "asgi.py",
    "main.go", "main.rs", "main.ts", "main.js", "index.js", "index.ts",
    "main.c", "main.cpp", "program.cs", "application.java",
    "app.vue", "index.vue", "home.vue",
}

# Markers inside a file that indicate the program starts here.
_ENTRY_MARKERS = (
    re.compile(r"if\s+__name__\s*==\s*[\"']__main__[\"']"),
    re.compile(r"^\s*func\s+main\s*\(\s*\)", re.MULTILINE),
    re.compile(r"^\s*fn\s+main\s*\(\s*\)", re.MULTILINE),
    re.compile(r"^\s*static\s+void\s+Main\s*\(", re.MULTILINE),
    re.compile(r"^\s*public\s+static\s+void\s+main\s*\(", re.MULTILINE),
    re.compile(r"createApp\s*\("),
    re.compile(r"new\s+Vue\s*\("),
    re.compile(r"ReactDOM\.render|createRoot\s*\("),
    re.compile(r"^\s*def\s+main\s*\(", re.MULTILINE),
)

# Directories whose files are unlikely to be the program's entry.
_LOW_PRIORITY_DIRS = {
    "test", "tests", "__tests__", "spec", "specs", "migrations",
    "fixtures", "examples", "docs", "scripts", "tools", "benchmarks",
}


def is_entry_candidate(rel_path: str) -> bool:
    """True when the path alone suggests an entry point."""
    path = PurePosixPath(rel_path)
    if {part.lower() for part in path.parts[:-1]} & _LOW_PRIORITY_DIRS:
        return False
    return path.name.lower() in _ENTRY_NAMES


def entry_score(rel_path: str, first_lines: str) -> int:
    """Higher means a better opening. Zero means no evidence at all."""
    if {part.lower() for part in PurePosixPath(rel_path).parts[:-1]} & _LOW_PRIORITY_DIRS:
        return 0

    score = 0
    if is_entry_candidate(rel_path):
        score += 3

    for marker in _ENTRY_MARKERS:
        if marker.search(first_lines):
            score += 5
            break

    # Shallow files are more likely to be top-level wiring than deep helpers.
    depth = len(PurePosixPath(rel_path).parts) - 1
    if depth == 0:
        score += 1

    return score


def choose_opening(files: Sequence[tuple[str, str]]) -> int | None:
    """Index of the file that should open the material, or None.

    `files` is (path, opening text). Returns None when nothing scores, so the
    caller keeps its existing order rather than promoting an arbitrary file.
    """
    best_index: int | None = None
    best_score = 0
    for index, (path, head) in enumerate(files):
        score = entry_score(path, head)
        if score > best_score:
            best_score = score
            best_index = index
    return best_index
