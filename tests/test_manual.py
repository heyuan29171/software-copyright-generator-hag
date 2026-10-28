"""The manual's structure rules come from published guidance, not preference.

Two of them contradict what third-party tools do: the manual must also be
trimmed to front thirty plus back thirty, and page numbering starts at the
table of contents rather than at chapter one.
"""

from __future__ import annotations

import pytest

from hag.layout import SEGMENT_PAGES, SPLIT_THRESHOLD_PAGES
from hag.manual import (
    Chapter,
    ChapterKind,
    Figure,
    Manual,
    ManualError,
    assert_structure,
    chapter_number,
    describe,
    figure_placeholder,
    plan_pages,
    validate_structure,
)


def chapter(title: str, body: str = "正文内容。") -> Chapter:
    return Chapter(title=title, body=body)


def full_manual(**overrides: object) -> Manual:
    manual = Manual(software_name="学生信息管理系统", version="V1.0")
    manual.chapters = [
        chapter("说明"),
        chapter("功能特点"),
        chapter("系统要求"),
        chapter("各功能页面/模块"),
        chapter("常见问题解答"),
        chapter("术语表"),
    ]
    for key, value in overrides.items():
        setattr(manual, key, value)
    return manual


class TestChapterNumbering:
    def test_first_is_chinese_one(self) -> None:
        assert chapter_number(1) == "一"

    def test_sequence(self) -> None:
        assert [chapter_number(i) for i in range(1, 7)] == ["一", "二", "三", "四", "五", "六"]

    def test_never_uses_parenthesised_digits(self) -> None:
        assert "(" not in chapter_number(1)


class TestStructure:
    def test_complete_manual_passes(self) -> None:
        assert validate_structure(full_manual()) == []

    def test_missing_required_chapter(self) -> None:
        manual = full_manual()
        manual.chapters = [c for c in manual.chapters if c.title != "系统要求"]
        problems = validate_structure(manual)
        assert any("系统要求" in p for p in problems)

    def test_wrong_order_reported(self) -> None:
        manual = full_manual()
        manual.chapters[0], manual.chapters[1] = manual.chapters[1], manual.chapters[0]
        problems = validate_structure(manual)
        assert any("顺序" in p for p in problems)

    def test_glossary_must_be_last(self) -> None:
        manual = full_manual()
        manual.chapters.append(
            Chapter("附加", "x", kind=ChapterKind.PROJECT_SPECIFIC, reason="r", evidence="e")
        )
        problems = validate_structure(manual)
        assert any("术语表" in p for p in problems)

    def test_faq_must_be_second_to_last(self) -> None:
        manual = full_manual()
        manual.chapters.insert(-1, chapter("插入章"))
        problems = validate_structure(manual)
        assert any("常见问题解答" in p for p in problems)

    def test_missing_name(self) -> None:
        problems = validate_structure(full_manual(software_name=""))
        assert any("软件名称" in p for p in problems)

    def test_missing_version(self) -> None:
        problems = validate_structure(full_manual(version=""))
        assert any("版本号" in p for p in problems)

    def test_assert_raises_with_reasons(self) -> None:
        manual = full_manual(software_name="")
        with pytest.raises(ManualError, match="软件名称"):
            assert_structure(manual)


class TestProjectSpecificChapters:
    def test_requires_reason(self) -> None:
        manual = full_manual()
        manual.chapters.insert(
            4,
            Chapter("指标与口径", "x", kind=ChapterKind.PROJECT_SPECIFIC, evidence="源码"),
        )
        problems = validate_structure(manual)
        assert any("理由" in p for p in problems)

    def test_requires_evidence(self) -> None:
        manual = full_manual()
        manual.chapters.insert(
            4,
            Chapter("指标与口径", "x", kind=ChapterKind.PROJECT_SPECIFIC, reason="口径易冲突"),
        )
        problems = validate_structure(manual)
        assert any("证据" in p for p in problems)

    def test_complete_custom_chapter_passes(self) -> None:
        manual = full_manual()
        manual.chapters.insert(
            4,
            Chapter(
                "指标与口径",
                "x",
                kind=ChapterKind.PROJECT_SPECIFIC,
                reason="各模块分别解释统计口径会互相矛盾",
                evidence="src/stats.py",
            ),
        )
        assert validate_structure(manual) == []


class TestFigures:
    def test_correct_numbering_passes(self) -> None:
        manual = full_manual()
        manual.chapters[3].figures = [
            Figure(chapter_number="4", index=1, caption="列表状态"),
            Figure(chapter_number="4", index=2, caption="详情状态"),
        ]
        assert validate_structure(manual) == []

    def test_chinese_numeral_also_accepted(self) -> None:
        """Chapter headings use Chinese numerals, so that form is tolerated."""
        manual = full_manual()
        manual.chapters[3].figures = [Figure(chapter_number="四", index=1, caption="x")]
        assert validate_structure(manual) == []

    def test_wrong_chapter_number(self) -> None:
        manual = full_manual()
        manual.chapters[3].figures = [Figure(chapter_number="3", index=1, caption="x")]
        problems = validate_structure(manual)
        assert any("图注编号错误" in p for p in problems)

    def test_gap_in_numbering(self) -> None:
        manual = full_manual()
        manual.chapters[3].figures = [
            Figure(chapter_number="4", index=1, caption="a"),
            Figure(chapter_number="4", index=3, caption="c"),
        ]
        problems = validate_structure(manual)
        assert any("不连续" in p for p in problems)

    def test_missing_caption(self) -> None:
        manual = full_manual()
        manual.chapters[3].figures = [Figure(chapter_number="4", index=1, caption="  ")]
        problems = validate_structure(manual)
        assert any("图注" in p for p in problems)

    def test_figure_label_format(self) -> None:
        assert Figure("3", 1, "x").number == "图 3-1"
        assert Figure("12", 4, "x").number == "图 12-4"


class TestPageBudget:
    def test_short_manual_submits_everything(self) -> None:
        budget = plan_pages(40)
        assert budget.submits_everything
        assert budget.submitted_pages == 40

    def test_exactly_sixty_trims(self) -> None:
        budget = plan_pages(SPLIT_THRESHOLD_PAGES)
        assert not budget.submits_everything
        assert budget.submitted_pages == SEGMENT_PAGES * 2

    def test_long_manual_trims(self) -> None:
        budget = plan_pages(120)
        assert budget.front_pages == 30
        assert budget.back_pages == 30

    def test_zero_pages_raises(self) -> None:
        with pytest.raises(ManualError):
            plan_pages(0)

    def test_note_explains_decision(self) -> None:
        assert plan_pages(40).note
        assert plan_pages(80).note


class TestPlaceholder:
    def test_is_visible_not_a_comment(self) -> None:
        text = figure_placeholder("图 3-1", "项目管理页面列表")
        assert "<!--" not in text
        assert "截图预留" in text
        assert "图 3-1" in text


class TestDescribe:
    def test_lists_chapters(self) -> None:
        text = describe(full_manual())
        assert "一、说明" in text
        assert "六、术语表" in text

    def test_marks_custom_chapters(self) -> None:
        manual = full_manual()
        manual.chapters.insert(
            4,
            Chapter(
                "指标与口径",
                "x",
                kind=ChapterKind.PROJECT_SPECIFIC,
                reason="r",
                evidence="e",
            ),
        )
        assert "（自定）" in describe(manual)
