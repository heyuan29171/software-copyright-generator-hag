"""Check that the machine can actually produce material before starting.

A user double-clicks the executable and expects to know within seconds whether
it will work. Discovering a missing font after a two-minute scan, or a missing
PDF library after the whole pipeline has run, wastes their time and looks like
a crash.

Every check here answers one question: will the next run succeed?
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Status(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class Item:
    name: str
    status: Status
    detail: str
    fix: str = ""


@dataclass(slots=True)
class EnvironmentReport:
    items: list[Item] = field(default_factory=list)

    def add(self, item: Item) -> None:
        self.items.append(item)

    @property
    def can_run(self) -> bool:
        """True when nothing essential is missing."""
        return not any(i.status is Status.FAIL for i in self.items)

    @property
    def failures(self) -> list[Item]:
        return [i for i in self.items if i.status is Status.FAIL]

    def to_text(self) -> str:
        lines = []
        for item in self.items:
            mark = {"ok": "[通过]", "warn": "[注意]", "fail": "[失败]"}[item.status.value]
            lines.append(f"{mark} {item.name}：{item.detail}")
            if item.fix and item.status is not Status.OK:
                lines.append(f"        处理：{item.fix}")
        return "\n".join(lines)


def check_python() -> Item:
    major, minor = sys.version_info[:2]
    version = f"{major}.{minor}.{sys.version_info[2]}"
    if (major, minor) < (3, 11):
        return Item(
            "Python 版本",
            Status.FAIL,
            f"当前 {version}，需要 3.11 或更高",
            "安装 Python 3.11+，或使用打包好的 exe",
        )
    return Item("Python 版本", Status.OK, version)


def check_pdf_library() -> Item:
    try:
        import fpdf  # noqa: F401

        return Item("PDF 生成库", Status.OK, f"fpdf2 {getattr(fpdf, '__version__', '已安装')}")
    except ImportError:
        return Item(
            "PDF 生成库",
            Status.FAIL,
            "未安装 fpdf2，无法生成 PDF",
            "pip install 'hag[pdf]'，或使用打包好的 exe",
        )


def check_pdf_reader() -> Item:
    try:
        import pypdf  # noqa: F401

        return Item("PDF 读取库", Status.OK, f"pypdf {getattr(pypdf, '__version__', '已安装')}")
    except ImportError:
        return Item(
            "PDF 读取库",
            Status.FAIL,
            "未安装 pypdf，无法回读页数（申请表页数字段依赖它）",
            "pip install pypdf",
        )


def check_fonts() -> Item:
    from .fonts import resolve_font_pair

    try:
        pair = resolve_font_pair()
    except Exception as exc:
        return Item(
            "字体",
            Status.FAIL,
            f"找不到可用字体：{exc}",
            "安装宋体（SimSun）或 Noto Sans CJK",
        )
    missing = pair.missing_characters(["测试中文 V1.0"])
    if missing:
        return Item(
            "字体",
            Status.WARN,
            f"{pair.cjk.label} 缺少部分字形：{''.join(missing)}",
            "安装完整的中文字体",
        )
    return Item("字体", Status.OK, f"{pair.mono.path.name} + {pair.cjk.path.name}")


def check_output_writable(directory: Path | None = None) -> Item:
    target = directory or Path.cwd()
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".hag-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return Item("输出目录可写", Status.OK, str(target))
    except Exception as exc:
        return Item(
            "输出目录可写",
            Status.FAIL,
            f"无法写入 {target}：{exc}",
            "选择有写权限的输出目录",
        )


def run_all(output_dir: Path | None = None) -> EnvironmentReport:
    """Every check, in the order a failure would bite."""
    report = EnvironmentReport()
    for item in (
        check_python(),
        check_pdf_library(),
        check_pdf_reader(),
        check_fonts(),
        check_output_writable(output_dir),
    ):
        report.add(item)
    return report
