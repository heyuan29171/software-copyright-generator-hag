"""Entry point for the packaged desktop application.

Kept separate from ``hag.gui`` so the frozen build has a single, obvious
target. When something fails before the window opens — a missing font, a
dependency that PyInstaller did not pick up — the user sees a message box
rather than a window that flashes and disappears with no explanation.
"""

from __future__ import annotations

import sys
import traceback


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
        try:
            from pathlib import Path

            log = Path(sys.executable).parent / "启动失败.txt"
            log.write_text(message, encoding="utf-8")
        except Exception:
            pass


def main() -> int:
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
