"""The document identifying material (操作手册).

The Centre's guidance asks for the same shape as the code material: the first
thirty pages and the last thirty, with page numbering starting at the table of
contents. That is a materially different requirement from what the
third-party tools assume, several of which submit the whole manual.

This module owns the structure and the trimming. It does not write prose: the
body comes from whoever is producing the document, and the checks here only
verify that the required chapters exist and that the content fits the page
budget. A generator that invents sentences produces template text, which is
exactly what the manual is supposed to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .layout import (
    MANUAL_MIN_LINES_PER_PAGE,
    MANUAL_REQUIRED_CHAPTERS,
    SEGMENT_PAGES,
    SPLIT_THRESHOLD_PAGES,
)


class ManualError(Exception):
    """Raised when the manual cannot satisfy a structural requirement."""


class ChapterKind(str, Enum):
    REQUIRED = "required"
    PROJECT_SPECIFIC = "project_specific"


@dataclass(slots=True)
class Chapter:
    """One chapter of the manual.

    `reason` and `evidence` only apply to project-specific chapters: a chapter
    that exists to fill out the section count, rather than because the project
    needs it, is padding and should not be created.
    """

    title: str
    body: str
    kind: ChapterKind = ChapterKind.REQUIRED
    figures: list[Figure] = field(default_factory=list)
    reason: str = ""
    evidence: str = ""


@dataclass(frozen=True, slots=True)
class Figure:
    """A screenshot and its caption.

    The caption describes the state the reader should look for, not a
    restatement of the surrounding prose.

    `chapter_number` is the Arabic position of the chapter (1, 2, 3…), because
    figure labels read 图 3-1 rather than 图 三-1. Chapter headings themselves
    still use Chinese numerals.
    """

    chapter_number: str
    index: int
    caption: str
    image_path: str | None = None

    @property
    def number(self) -> str:
        return f"图 {self.chapter_number}-{self.index}"


@dataclass(slots=True)
class Manual:
    software_name: str
    version: str
    chapters: list[Chapter] = field(default_factory=list)

    def required_titles(self) -> list[str]:
        return [c.title for c in self.chapters if c.kind is ChapterKind.REQUIRED]

    def project_specific(self) -> list[Chapter]:
        return [c for c in self.chapters if c.kind is ChapterKind.PROJECT_SPECIFIC]


_CN_NUMERALS = (
    "一",
    "二",
    "三",
    "四",
    "五",
    "六",
    "七",
    "八",
    "九",
    "十",
    "十一",
    "十二",
    "十三",
    "十四",
    "十五",
    "十六",
    "十七",
    "十八",
    "十九",
    "二十",
)


def chapter_number(index: int) -> str:
    """Chinese uppercase numeral for chapter headings.

    The guidance requires 一、说明 rather than (1)、说明.
    """
    if 1 <= index <= len(_CN_NUMERALS):
        return _CN_NUMERALS[index - 1]
    return str(index)


def validate_structure(manual: Manual) -> list[str]:
    """Structural problems with the manual, in the order they occur."""
    problems: list[str] = []

    if not manual.software_name.strip():
        problems.append("手册缺少软件名称，页眉将无法与申请表保持一致")
    if not manual.version.strip():
        problems.append("手册缺少版本号，页眉将无法与申请表保持一致")

    titles = [c.title for c in manual.chapters]
    required = list(MANUAL_REQUIRED_CHAPTERS)

    missing = [name for name in required if name not in titles]
    if missing:
        problems.append("缺少必备章节：" + "、".join(missing))

    present_required = [name for name in required if name in titles]
    if present_required:
        actual_order = [t for t in titles if t in present_required]
        if actual_order != present_required:
            problems.append(
                "必备章节顺序不正确，应为：" + " → ".join(required)
            )

    # FAQ and glossary are always last two; nothing may follow the glossary.
    if titles and titles[-1] != "术语表" and "术语表" in titles:
        problems.append("术语表必须是最后一章")
    if len(titles) >= 2 and titles[-2] != "常见问题解答" and "常见问题解答" in titles:
        problems.append("常见问题解答必须是倒数第二章")

    for chapter in manual.project_specific():
        if not chapter.reason.strip():
            problems.append(f"自定章节「{chapter.title}」缺少设立理由")
        if not chapter.evidence.strip():
            problems.append(f"自定章节「{chapter.title}」缺少证据来源")

    # Figure numbering must be continuous within each chapter.
    for position, chapter in enumerate(manual.chapters, start=1):
        # Figure labels use Arabic numerals; chapter headings use Chinese ones.
        arabic = str(position)
        chinese = chapter_number(position)
        expected = 1
        for figure in chapter.figures:
            if figure.chapter_number not in (arabic, chinese):
                problems.append(
                    f"图注编号错误：{figure.number} 位于第 {arabic} 章"
                )
                break
            if figure.index != expected:
                problems.append(
                    f"图注编号不连续：第 {arabic} 章缺少图 {arabic}-{expected}"
                )
                break
            if not figure.caption.strip():
                problems.append(f"{figure.number} 缺少图注")
                break
            expected += 1

    return problems


def assert_structure(manual: Manual) -> None:
    problems = validate_structure(manual)
    if problems:
        raise ManualError("；".join(problems))


@dataclass(slots=True)
class PageBudget:
    """How the manual's pages map onto what gets submitted."""

    total_pages: int
    submitted_pages: int
    front_pages: int
    back_pages: int
    submits_everything: bool
    note: str = ""


def plan_pages(total_pages: int) -> PageBudget:
    """Decide whether to submit the whole manual or a front/back slice.

    Under sixty pages the whole thing goes in, which is what the statute says.
    At or above sixty the first and last thirty pages are submitted, and the
    page numbers on those pages are the ones already printed on them: numbering
    starts at the table of contents and runs straight through.
    """
    if total_pages <= 0:
        raise ManualError("manual has no pages")

    if total_pages < SPLIT_THRESHOLD_PAGES:
        return PageBudget(
            total_pages=total_pages,
            submitted_pages=total_pages,
            front_pages=total_pages,
            back_pages=0,
            submits_everything=True,
            note="手册不足 60 页，提交全部内容",
        )

    return PageBudget(
        total_pages=total_pages,
        submitted_pages=SEGMENT_PAGES * 2,
        front_pages=SEGMENT_PAGES,
        back_pages=SEGMENT_PAGES,
        submits_everything=False,
        note="提交前 30 页与后 30 页，页码沿用原文档的 1–N 编号",
    )


def figure_placeholder(number: str, description: str) -> str:
    """Visible placeholder for a missing screenshot.

    An HTML comment would be invisible in the finished document, so a reviewer
    would see a gap with no explanation. This string stays visible.
    """
    return f"【{number} 截图预留：请在此处插入「{description}」的截图。】"


def check_minimum_lines(lines_per_page: int) -> None:
    """Guard the statute's floor for documents, which is lower than for code."""
    if lines_per_page < MANUAL_MIN_LINES_PER_PAGE:
        raise ManualError(
            f"文档每页不少于 {MANUAL_MIN_LINES_PER_PAGE} 行，当前设置为 {lines_per_page}"
        )


@dataclass(slots=True)
class ManualResult:
    manual: Manual
    budget: PageBudget
    rendered_pages: int
    pdf_path: Path | None = None
    warnings: list[str] = field(default_factory=list)


def describe(manual: Manual) -> str:
    """Outlined table of contents, used for the draft and for review."""
    lines = [f"{manual.software_name} {manual.version}", "", "目录", ""]
    for index, chapter in enumerate(manual.chapters, start=1):
        marker = "（自定）" if chapter.kind is ChapterKind.PROJECT_SPECIFIC else ""
        lines.append(f"{chapter_number(index)}、{chapter.title}{marker}")
        for figure in chapter.figures:
            lines.append(f"    {figure.number} {figure.caption}")
    return "\n".join(lines)


__all__ = [
    "Chapter",
    "ChapterKind",
    "Figure",
    "Manual",
    "ManualError",
    "ManualResult",
    "PageBudget",
    "assert_structure",
    "chapter_number",
    "check_minimum_lines",
    "describe",
    "figure_placeholder",
    "plan_pages",
    "validate_structure",
]
