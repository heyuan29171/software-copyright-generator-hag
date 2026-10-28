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
SELF_TEST_FLAG = "--self-test"


def _wants_mcp(argv: list[str]) -> bool:
    return MCP_FLAG in argv


def _wants_self_test(argv: list[str]) -> bool:
    """Internal verification mode, used after packaging.

    It exists so the shipped entry point can be exercised end to end without a
    second build target: a window that opens is not the same as a program that
    generates material.
    """
    return SELF_TEST_FLAG in argv


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


def _run_self_test() -> int:
    """Generate one material end to end and report whether it worked.

    Runs inside the packaged program, so it proves the bundle contains the PDF
    renderer, the font tables and the parsers — things a "window opened" check
    would miss.
    """
    import tempfile
    from pathlib import Path

    from hag.fonts import resolve_font_pair
    from hag.server import generate_materials, inspect_project

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project = root / "sample"
        project.mkdir()
        for index in range(10):
            body = "\n".join(f"const value{index}_{n} = {n};" for n in range(300))
            (project / f"module{index}.ts").write_text(body + "\n", encoding="utf-8")

        scan = inspect_project(str(project))
        if not scan.get("ok"):
            print("FAIL: scan:", scan.get("error"))
            return 1
        print(f"scan ok: {scan['source_files']} files")

        result = generate_materials(
            str(project),
            "自检系统",
            "V1.0",
            output_dir=str(root / "out"),
            fields={
                "owner": "自检公司",
                "owner_category": "法人",
                "development_completed": "2025-03-01",
                "dev_os": "Windows 11",
                "dev_ide": "Visual Studio Code",
                "purpose": "自检",
                "domain": "测试",
                "main_functions": "自检用功能描述，用于验证打包后的程序能够完整生成材料。" * 30,
                "source_line_count": "3000",
            },
        )
        if not result.get("ok"):
            print("FAIL: blocking items:")
            for item in result.get("blocking", []):
                print("  -", item.get("name"), item.get("detail"))
            return 1

        pdfs = list((root / "out" / "正式资料").glob("*.pdf"))
        if not pdfs or pdfs[0].stat().st_size == 0:
            print("FAIL: no PDF")
            return 1

        pair = resolve_font_pair()
        missing = pair.missing_characters(["自检系统 V1.0"])
        if missing:
            print("FAIL: fonts missing glyphs:", missing)
            return 1

        print(f"generation ok: {result.get('rendered_pages')} pages")
        print(f"fonts ok: {pair.mono.path.name} + {pair.cjk.path.name}")

    print("SELF-TEST PASSED")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if _wants_self_test(argv):
        return _run_self_test()
    if _wants_mcp(argv):
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
