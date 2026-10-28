"""Regressions for a real run that produced 59 pages with 31 on one side.

That output was labelled "front 28 + back 31". Three separate faults combined:
the search only considered file boundaries, the exact test accepted a segment
one line into its last page, and Chinese prose was reported but not removed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hag.layout import LINES_PER_PAGE, SEGMENT_PAGES
from hag.pagination import is_code_bearing, material_lines
from hag.pipeline import safe_cut_offsets
from hag.selection import (
    FileSpan,
    SelectionMode,
    material_slices,
    select_material,
)


def spans(*counts: int, cuts: bool = False) -> list[FileSpan]:
    out = []
    for i, c in enumerate(counts):
        safe = tuple(range(1, c)) if cuts else ()
        out.append(FileSpan(rel_path=f"f{i}.py", line_count=c, safe_cuts=safe))
    return out


class TestExactSplitFillsPages:
    """A sixty-page document must be sixty pages, not fifty-nine."""

    def test_exact_result_is_sixty_pages(self) -> None:
        selection = select_material(spans(*([50] * 100)))
        assert selection.rendered_pages == 60

    def test_front_line_count_is_a_whole_number_of_pages(self) -> None:
        """1451 lines is 30 pages by ceiling but leaves page 30 nearly empty,
        and the back segment's opening lines then flow into it."""
        selection = select_material(spans(*([50] * 100)))
        assert selection.front_end_line % LINES_PER_PAGE == 0

    def test_rendered_lines_equals_sixty_pages_of_lines(self) -> None:
        selection = select_material(spans(*([50] * 100)))
        assert selection.rendered_lines == 60 * LINES_PER_PAGE

    def test_slices_cover_exactly_the_rendered_lines(self) -> None:
        selection = select_material(spans(*([50] * 100)))
        covered = sum(end - start for start, end in material_slices(selection))
        assert covered == selection.rendered_lines

    def test_partial_page_at_cut_is_not_accepted_as_exact(self) -> None:
        """A 1451-line front is 30 pages by ceiling. It must not be reported
        as an exact split, because concatenating it with the back segment
        merges two half-empty pages into one."""
        assert 1451 // LINES_PER_PAGE == 29
        assert -(-1451 // LINES_PER_PAGE) == 30

        selection = select_material(spans(1451, 5000, 1451))
        if selection.mode is SelectionMode.FRONT_BACK and selection.exact:
            assert selection.rendered_pages == 60


class TestSafeCutsEnableExactSplits:
    """Cutting inside a large file is allowed when the cut is safe."""

    def test_huge_single_component_without_cuts_is_refused(self) -> None:
        """When only file ends are cut points and one file is 60 pages, no
        combination lands near 30+30. Refusing is correct: a 60-page front
        segment would be twice the statutory length."""
        from hag.selection import SelectionError

        files = spans(2986, 1000, 500)
        with pytest.raises(SelectionError, match="tolerance"):
            select_material(files)

    def test_safe_cuts_allow_exact_split(self) -> None:
        files = spans(2986, 1000, 500, cuts=True)
        selection = select_material(files)
        assert selection.rendered_pages == 60
        assert selection.front_end_line == 1500

    def test_no_segment_exceeds_thirty(self) -> None:
        files = spans(2986, 1000, 500, cuts=True)
        selection = select_material(files)
        assert selection.front_pages <= 30
        assert selection.back_pages <= 30


class TestSafeCutDetection:
    def test_top_level_line_is_safe(self) -> None:
        lines = ["def f():", "    a = 1", "class C:", "    x = 1"]
        assert 2 in safe_cut_offsets(lines)

    def test_indented_line_is_not_safe(self) -> None:
        lines = ["def f():", "    a = 1", "    b = 2", "class C:"]
        assert 1 not in safe_cut_offsets(lines)
        assert 2 not in safe_cut_offsets(lines)

    def test_closer_is_not_safe(self) -> None:
        lines = ["if x:", "    y()", "}", "z()"]
        assert 2 not in safe_cut_offsets(lines)

    def test_continuation_is_not_safe(self) -> None:
        lines = ["a = 1", "+ 2", "b = 3"]
        assert 1 not in safe_cut_offsets(lines)

    def test_first_line_is_never_a_cut(self) -> None:
        assert 0 not in safe_cut_offsets(["x()", "y()"])


class TestChineseProseIsRemoved:
    """The limit was reported but never enforced."""

    def test_pure_chinese_comment_dropped(self) -> None:
        src = "// 这是一段纯中文注释\nconst x = 1;\n"
        lines = material_lines(src, 100, drop_pure_cjk=True)
        assert len(lines) == 1
        assert "const" in lines[0]

    def test_line_with_code_and_chinese_kept(self) -> None:
        src = "const x = 1; // 中文说明\n"
        lines = material_lines(src, 100, drop_pure_cjk=True)
        assert len(lines) == 1

    def test_python_docstring_prose_dropped(self) -> None:
        src = '"""这是一段很长的说明"""\n'
        assert material_lines(src, 100, drop_pure_cjk=True) == []

    def test_hash_comment_prose_dropped(self) -> None:
        src = "# 中文注释\nx = 1\n"
        lines = material_lines(src, 100, drop_pure_cjk=True)
        assert lines == ["x = 1"]

    def test_disabled_keeps_everything(self) -> None:
        src = "// 中文注释\nconst x = 1;\n"
        assert len(material_lines(src, 100, drop_pure_cjk=False)) == 2

    def test_code_bearing_detection(self) -> None:
        assert is_code_bearing("const x = 1;")
        assert is_code_bearing("return '中文'")
        assert not is_code_bearing("这是一段纯中文文本")
        assert not is_code_bearing("// 这是一段中文注释")
        assert not is_code_bearing("# 中文")
        assert not is_code_bearing("")
