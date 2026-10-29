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


def _find_upx() -> Path | None:
    """Directory holding upx.exe, or None when it is not installed."""
    found = shutil.which("upx")
    if found:
        return Path(found).parent
    for base in (
        Path.home() / "AppData/Local/Microsoft/WinGet/Packages",
        Path("C:/Program Files"),
        Path("C:/upx"),
    ):
        if base.is_dir():
            for candidate in base.rglob("upx.exe"):
                return candidate.parent
    return None


def build(*, console: bool = False, lite: bool = False) -> Path:
    """Build the executable.

    `console=True` produces a variant that keeps its console, used only by the
    self-test: a windowed build has no stdout, so verification output would be
    invisible.

    `lite=True` omits the MCP server and its dependencies. The MCP SDK brings in
    cryptography, Pillow, lxml and pydantic, which is most of the file size; a
    buyer using the window needs none of it. The lite build keeps the whole GUI
    and generation pipeline, and `--mcp` reports that the server is unavailable
    rather than failing obscurely.
    """
    for directory in (DIST, BUILD):
        if directory.exists():
            shutil.rmtree(directory)

    if console:
        name = f"{APP_NAME}-verify"
    elif lite:
        name = f"{APP_NAME}-lite"
    else:
        name = APP_NAME
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
        # Windows file properties: author, copyright and version, shown on the
        # Details tab of the executable.
        *(
            ["--version-file", str(ROOT / "scripts" / "version_info.txt")]
            if (ROOT / "scripts" / "version_info.txt").is_file()
            else []
        ),
    ]

    hidden = list(HIDDEN_IMPORTS)
    collect = list(COLLECT_SUBMODULES)
    excludes = list(EXCLUDES)

    if lite:
        hidden = [m for m in hidden if not m.startswith("mcp")]
        collect = [p for p in collect if p != "mcp"]
        # The MCP SDK's own dependencies are the bulk of the size, and nothing
        # else in the application imports them.
        excludes += [
            "mcp",
            "cryptography",
            "pydantic",
            "pydantic_core",
            "PIL",
            "lxml",
            "httpx",
            "httpcore",
            "h11",
            "certifi",
            "jsonschema",
            "referencing",
            "rpds",
        ]

    for module in hidden:
        args.extend(["--hidden-import", module])

    for package in collect:
        args.extend(["--collect-submodules", package])

    for module in excludes:
        args.extend(["--exclude-module", module])

    # UPX compresses the bundled Python runtime. Without it the lite build sits
    # just above the 30 MB single-file limit of the free upload space.
    upx_dir = _find_upx()
    if upx_dir is not None:
        args.extend(["--upx-dir", str(upx_dir)])
        args.extend(["--upx-exclude", "vcruntime140.dll"])

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
    verify(
        build(
            console="--console" in sys.argv,
            lite="--lite" in sys.argv,
        )
    )
