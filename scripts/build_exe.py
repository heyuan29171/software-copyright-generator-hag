"""Build a single-file Windows executable.

Checked in so the build is reproducible and reviewable rather than a command
someone pasted once. Run it with::

    python scripts/build_exe.py

The result is ``dist/软著申请材料生成器.exe``: one file, no console window,
no Python installation required on the target machine.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "软著申请材料生成器"
ENTRY = ROOT / "src" / "hag" / "app.py"
DIST = ROOT / "dist"
BUILD = ROOT / "build"

# Modules imported lazily or through the mcp SDK that PyInstaller cannot see
# by scanning alone. Missing any of these shows up as a crash at runtime.
HIDDEN_IMPORTS = [
    "fpdf",
    "fontTools",
    "fontTools.ttLib",
    "fontTools.pens",
    "fontTools.pens.recordingPen",
    "pypdf",
    "pypdf._reader",
    # The mcp server is reachable only through ``hag.app --mcp``. PyInstaller
    # follows imports statically, so the whole package is named explicitly.
    "mcp",
    "mcp.server",
    "mcp.server.mcpserver",
    "mcp.server.stdio",
    "mcp.types",
]

# Packages whose submodules are reached dynamically.
COLLECT_SUBMODULES = ["fpdf", "fontTools", "pypdf", "mcp"]

EXCLUDES = [
    # Not used by either entry point, and large.
    "pytest",
    "numpy",
    "pandas",
    "matplotlib",
    "PySide6",
    "PyQt5",
    "PyQt6",
]


def build(*, console: bool = False) -> Path:
    """Build the executable.

    `console=True` produces a variant that keeps its console, used only by the
    self-test: a windowed build has no stdout, so verification output would be
    invisible. The shipped artefact is always windowed.
    """
    for directory in (DIST, BUILD):
        if directory.exists():
            shutil.rmtree(directory)

    name = APP_NAME if not console else f"{APP_NAME}-verify"
    # Both variants use the real entry point, so the verify build exercises the
    # same code paths as the shipped one. Earlier the verify build pointed at
    # the self-test script instead, which meant it could not be used to check
    # that ``--mcp`` works — the flag was swallowed by an unrelated argparse.
    entry = ENTRY

    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        # No console: this is the flag that stops a black window appearing.
        *([] if console else ["--windowed"]),
        "--onefile",
        "--name",
        name,
        "--distpath",
        str(DIST),
        "--workpath",
        str(BUILD),
        "--specpath",
        str(BUILD),
        "--paths",
        str(ROOT / "src"),
        # Pull in the icon if one exists; skip silently otherwise.
        *(["--icon", str(ROOT / "assets" / "app.ico")] if (ROOT / "assets" / "app.ico").is_file() else []),
    ]

    for module in HIDDEN_IMPORTS:
        args.extend(["--hidden-import", module])

    for package in COLLECT_SUBMODULES:
        args.extend(["--collect-submodules", package])

    for module in EXCLUDES:
        args.extend(["--exclude-module", module])

    args.append(str(entry))

    print("building:", " ".join(args[:6]), "...")
    subprocess.run(args, check=True, cwd=ROOT)

    produced = DIST / f"{name}.exe"
    if not produced.is_file():
        raise SystemExit(f"build did not produce {produced}")
    return produced


def verify(exe: Path) -> None:
    """Sanity-check the artefact rather than trusting the build's own report."""
    size_mb = exe.stat().st_size / (1024 * 1024)
    print(f"built {exe.name} ({size_mb:.1f} MB)")
    if size_mb < 5:
        raise SystemExit(
            "the executable is implausibly small; the application was probably "
            "not bundled. Check that the entry point imports the GUI."
        )


if __name__ == "__main__":
    verify(build(console="--console" in sys.argv))
