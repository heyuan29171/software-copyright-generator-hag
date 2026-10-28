"""Verify the packaged application actually generates material.

A window that opens is not the same as a program that works: bundling can drop
a lazily-imported parser or a font table reader, and the failure only appears
when a real project is processed. This script drives the bundled code the same
way the window does.

Run it against the frozen build::

    # 1. build a console variant so output is visible
    python scripts/build_exe.py --console
    # 2. run the verification build
    dist/软著申请材料生成器-verify.exe --self-test
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path


def _make_project(root: Path) -> Path:
    project = root / "sample"
    project.mkdir(parents=True, exist_ok=True)
    for index in range(10):
        body = "\n".join(f"const value{index}_{n} = {n};" for n in range(300))
        (project / f"module{index}.ts").write_text(body + "\n", encoding="utf-8")
    return project


def run_self_test() -> int:
    """Exercise the same path the GUI uses, end to end."""
    from hag.server import generate_materials, inspect_project

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project = _make_project(root)

        scan = inspect_project(str(project))
        if not scan.get("ok"):
            print("FAIL: project scan failed:", scan.get("error"))
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
            print("FAIL: generation reported blocking items:")
            for item in result.get("blocking", []):
                print("  -", item.get("name"), item.get("detail"))
            return 1

        pdfs = list((root / "out" / "正式资料").glob("*.pdf"))
        if not pdfs:
            print("FAIL: no PDF produced")
            return 1
        if pdfs[0].stat().st_size == 0:
            print("FAIL: PDF is empty")
            return 1

        pages = result.get("rendered_pages", 0)
        print(f"generation ok: {pages} pages, PDF {pdfs[0].stat().st_size} bytes")

        # Fonts must resolve inside the bundle, not only on the build machine.
        from hag.fonts import resolve_font_pair

        pair = resolve_font_pair()
        print(f"fonts ok: {pair.mono.path.name} + {pair.cjk.path.name}")
        missing = pair.missing_characters(["自检系统 V1.0"])
        if missing:
            print("FAIL: bundled fonts missing glyphs:", missing)
            return 1

    print("SELF-TEST PASSED")
    return 0


def main() -> int:
    if "--self-test" not in sys.argv:
        print("usage: --self-test")
        return 2
    try:
        return run_self_test()
    except Exception:
        print("FAIL: unhandled error")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
