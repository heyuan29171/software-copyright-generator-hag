"""Vue-style files must be splittable.

A real project failed with "not enough material" despite having 15,806 usable
lines for a 1,500-line requirement. The cause was the cut-point test: it
required a line starting at column zero, and nothing inside a Vue `<template>`
or `<script>` does. Every component reported zero cut points, so a file larger
than a segment could be neither taken whole nor trimmed.
"""

from __future__ import annotations

import pytest

from hag.comments import StripMethod
from hag.pipeline import CleanedFile, prefer_low_cjk_order, safe_cut_offsets
from hag.selection import FileSpan, SelectionError, material_slices, select_material


def vue_file(name: str, lines: int) -> CleanedFile:
    body = []
    for i in range(lines):
        if i % 13 == 0:
            body.append(f"        <span>羊只状态 {i}</span>")
        elif i % 7 == 0:
            body.append("      </div>")
        else:
            body.append(f"      const v{i} = props.data[{i}];")
    return CleanedFile(name, ".vue", tuple(body), StripMethod.FULL)


class TestCutPointsInIndentedFiles:
    def test_vue_component_has_cut_points(self) -> None:
        f = vue_file("big.vue", 500)
        assert len(safe_cut_offsets(f.lines)) > 100

    def test_indented_file_is_not_all_cut_points(self) -> None:
        f = vue_file("big.vue", 500)
        cuts = safe_cut_offsets(f.lines)
        assert len(cuts) < f.line_count

    def test_closing_tag_is_not_a_cut(self) -> None:
        lines = ["      a = 1;", "      </div>", "      b = 2;"]
        assert 1 not in safe_cut_offsets(lines)

    def test_python_top_level_still_found(self) -> None:
        lines = ["def f():", "    a = 1", "class C:", "    x = 1"]
        assert 2 in safe_cut_offsets(lines)

    def test_deeper_indent_is_not_a_cut(self) -> None:
        lines = ["    a = 1;", "        nested();", "    b = 2;"]
        assert 1 not in safe_cut_offsets(lines)


class TestLargeFileCanBeSplit:
    def test_single_file_under_sixty_pages_is_submitted_whole(self) -> None:
        """57 pages is below the threshold, so the whole file goes in."""
        f = vue_file("DataBoard.vue", 2836)
        spans = [
            FileSpan(f.rel_path, f.line_count, safe_cut_offsets(f.lines), f.cjk_ratio)
        ]
        selection = select_material(spans)
        assert selection.mode.value == "all"
        assert selection.rendered_pages == 57

    def test_one_file_cannot_fill_two_segments(self) -> None:
        """A 100-page single file still cannot supply both a front and a back
        excerpt, because no line may appear twice."""
        f = vue_file("huge.vue", 5000)
        spans = [
            FileSpan(f.rel_path, f.line_count, safe_cut_offsets(f.lines), f.cjk_ratio)
        ]
        with pytest.raises(SelectionError) as exc:
            select_material(spans)
        assert "可用素材" in str(exc.value)

    def test_many_large_files_produce_sixty_pages(self) -> None:
        sizes = [2836, 1712, 1360, 1308, 1135, 1083, 1066, 897, 875, 832]
        files = [vue_file(f"f{i}.vue", n) for i, n in enumerate(sizes)]
        ordered = prefer_low_cjk_order(files)
        spans = [
            FileSpan(f.rel_path, f.line_count, safe_cut_offsets(f.lines), f.cjk_ratio)
            for f in ordered
        ]
        selection = select_material(spans)
        assert selection.rendered_pages == 60
        assert selection.front.line_count == 1500
        assert selection.back.line_count == 1500

    def test_segments_remain_distinct(self) -> None:
        sizes = [2836, 1712, 1360, 1308, 1135, 1083, 1066, 897, 875, 832]
        files = [vue_file(f"f{i}.vue", n) for i, n in enumerate(sizes)]
        spans = [
            FileSpan(f.rel_path, f.line_count, safe_cut_offsets(f.lines), f.cjk_ratio)
            for f in files
        ]
        selection = select_material(spans)
        ranges = sorted(material_slices(selection))
        for (a_start, a_end), (b_start, b_end) in zip(ranges, ranges[1:]):
            assert a_end <= b_start


class TestShortfallMessages:
    def test_not_enough_material_says_so(self) -> None:
        """65 pages in total, but the largest file is 800 lines, so the front
        can only reach 16 pages before every file is used."""
        files = [FileSpan(f"f{i}.py", 800) for i in range(5)]
        with pytest.raises(SelectionError, match="可用素材"):
            select_material(files)

    def test_enough_material_without_cuts_says_so(self) -> None:
        """"Not enough files" and "no place to cut" need different fixes."""
        with pytest.raises(SelectionError, match="可切分"):
            select_material([FileSpan("a.py", 2000), FileSpan("b.py", 2000)])

    def test_message_mentions_the_scope(self) -> None:
        with pytest.raises(SelectionError) as exc:
            select_material([FileSpan("a.py", 2000), FileSpan("b.py", 2000)])
        assert "前段" in str(exc.value) or "后段" in str(exc.value)
