"""Entry point for the packaged desktop application.

The same executable serves two audiences, and which one it serves is decided
by how it is started:

* double-clicked, or started with no arguments — the window opens;
* started with ``--mcp``, which is what an MCP client does — it speaks the
  protocol over stdio instead.

Keeping one file for both is deliberate. The alternative is two downloads that
can drift apart, and a user who cannot tell which one they are supposed to
run.

When something fails before the window opens — a missing font, a dependency
PyInstaller did not pick up — the user sees a message box rather than a window
that flashes and disappears. In MCP mode a message box would be wrong: there
is nobody to click it, so the error goes to stderr and to the exit code.
"""

from __future__ import annotations

import sys
import traceback

MCP_FLAG = "--mcp"


def _wants_mcp(argv: list[str]) -> bool:
    return MCP_FLAG in argv


def _show_fatal(message: str) -> None:
    """Report a startup failure in a way a double-click user can see."""
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("软著申请材料生成器", message)
        root.destroy()
    except Exception:
        # Last resort: a frozen windowed build has no console, so write a file
        # next to the executable rather than failing silently.
        _write_crash_log(message)


def _write_crash_log(message: str) -> None:
    from pathlib import Path

    targets = [Path.cwd()]
    try:
        targets.insert(0, Path(sys.executable).parent)
    except Exception:
        pass
    for directory in targets:
        try:
            (directory / "启动失败.txt").write_text(message, encoding="utf-8")
            return
        except Exception:
            continue


def _run_mcp() -> int:
    """Serve the protocol over stdio.

    Nothing may be written to stdout here: stdout carries the protocol, and a
    stray print corrupts the stream and disconnects the client. Diagnostics go
    to stderr.
    """
    try:
        from hag.server import main as mcp_main

        mcp_main()
        return 0
    except Exception:
        print("MCP 启动失败：", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1


def main() -> int:
    if _wants_mcp(sys.argv[1:]):
        return _run_mcp()

    try:
        from hag.gui import main as gui_main

        return gui_main()
    except Exception:
        _show_fatal(
            "程序启动失败：\n\n"
            + traceback.format_exc(limit=4)
            + "\n请将上述信息反馈给开发者。"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
