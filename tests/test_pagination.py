"""Wrapping and pagination must be lossless, deterministic and exact.

The failure these tests are built to catch: a line that wraps *during*
rendering rather than *before* pagination, which silently adds a page and
makes the application form's page count wrong.
"""

from __future__ import annotations

import pytest

from hag.layout import LINES_PER_PAGE, LayoutViolation
from hag.pagination import (
    display_width,
    hard_cap_line,
    locate_spans,
    material_lines,
    paginate,
    sanitize_line,
    wrap_display_line,
)


class TestDisplayWidth:
    def test_ascii(self) -> None:
        assert display_width("abc") == 3

    def test_cjk_counts_two(self) -> None:
        assert display_width("中文") == 4

    def test_mixed(self) -> None:
        assert display_width("abc中文") == 7

    def test_empty(self) -> None:
        assert display_width("") == 0


class TestWrapping:
    def test_short_line_untouched(self) -> None:
        assert wrap_display_line("short", 100) == ["short"]

    def test_exact_fit_untouched(self) -> None:
        assert wrap_display_line("x" * 100, 100) == ["x" * 100]

    def test_wraps_at_limit(self) -> None:
        assert wrap_display_line("x" * 101, 100) == ["x" * 100, "x"]

    def test_lossless_for_mixed_content(self) -> None:
        source = "const value = '" + ("中" * 70) + ("x" * 80) + "';"
        pieces = wrap_display_line(source, 100)
        assert len(pieces) > 1
        assert "".join(pieces) == source

    def test_every_piece_respects_limit(self) -> None:
        source = "中文" * 200
        pieces = wrap_display_line(source, 80)
        assert all(display_width(p) <= 80 for p in pieces)

    def test_cjk_never_split_mid_column(self) -> None:
        # An odd limit means a double-width char cannot fit at the last column.
        pieces = wrap_display_line("中" * 10, 5)
        assert "".join(pieces) == "中" * 10
        assert all(display_width(p) <= 5 for p in pieces)

    def test_zero_disables_wrapping(self) -> None:
        assert wrap_display_line("x" * 500, 0) == ["x" * 500]


class TestSanitize:
    def test_expands_tabs(self) -> None:
        assert sanitize_line("a\tb", tab_width=4) == "a   b"

    def test_control_chars_become_spaces(self) -> None:
        assert sanitize_line("a\x00b") == "a b"

    def test_newline_is_not_expected_here(self) -> None:
        # Lines are split before sanitising; a stray \n must not survive as-is.
        assert "\n" not in sanitize_line("a\nb")


class TestHardCap:
    def test_short_line_untouched(self) -> None:
        assert hard_cap_line("abc", 10) == ["abc"]

    def test_long_line_chopped(self) -> None:
        assert hard_cap_line("x" * 25, 10) == ["x" * 10, "x" * 10, "x" * 5]

    def test_lossless(self) -> None:
        text = "y" * 1000
        assert "".join(hard_cap_line(text, 300)) == text


class TestMaterialLines:
    def test_drops_blank_lines(self) -> None:
        assert material_lines("\na\n\n\nb\n", 100) == ["a", "b"]

    def test_drops_whitespace_only_lines(self) -> None:
        assert material_lines("a\n   \n\t\nb", 100) == ["a", "b"]

    def test_handles_crlf(self) -> None:
        assert material_lines("a\r\nb\r\n", 100) == ["a", "b"]

    def test_handles_lone_cr(self) -> None:
        assert material_lines("a\rb", 100) == ["a", "b"]

    def test_wraps_and_counts_physical_lines(self) -> None:
        lines = material_lines("\nshort\n" + ("x" * 101) + "\n", 100)
        assert lines == ["short", "x" * 100, "x"]

    def test_tab_expanded_before_wrapping(self) -> None:
        lines = material_lines("a\tb", 100)
        assert lines == ["a   b"]


class TestPagination:
    def test_exact_multiple(self) -> None:
        pages = paginate([str(i) for i in range(100)], 50)
        assert [len(p) for p in pages] == [50, 50]

    def test_with_remainder(self) -> None:
        pages = paginate([str(i) for i in range(101)], 50)
        assert [len(p) for p in pages] == [50, 50, 1]

    def test_single_page(self) -> None:
        assert [len(p) for p in paginate(list("abc"), 50)] == [3]

    def test_empty(self) -> None:
        assert paginate([], 50) == []

    def test_only_last_page_may_be_short(self) -> None:
        pages = paginate([str(i) for i in range(1234)], LINES_PER_PAGE)
        assert all(len(p) == LINES_PER_PAGE for p in pages[:-1])
        assert len(pages[-1]) <= LINES_PER_PAGE

    def test_no_lines_lost(self) -> None:
        lines = [f"line-{i}" for i in range(777)]
        pages = paginate(lines, LINES_PER_PAGE)
        assert [line for page in pages for line in page] == lines

    def test_rejects_zero(self) -> None:
        with pytest.raises(LayoutViolation):
            paginate(["a"], 0)


class TestLocateSpans:
    def test_single_file_within_one_page(self) -> None:
        spans = locate_spans([("a.py", 10)], 50)
        assert spans[0].page_start == 1
        assert spans[0].page_end == 1
        assert spans[0].first_line == 1
        assert spans[0].last_line == 10

    def test_second_file_starts_where_first_ended(self) -> None:
        spans = locate_spans([("a.py", 10), ("b.py", 5)], 50)
        assert spans[1].first_line == 11
        assert spans[1].last_line == 15

    def test_span_across_page_boundary(self) -> None:
        spans = locate_spans([("a.py", 60)], 50)
        assert spans[0].page_start == 1
        assert spans[0].page_end == 2

    def test_zero_line_file_skipped(self) -> None:
        spans = locate_spans([("empty.py", 0), ("a.py", 3)], 50)
        assert len(spans) == 1
        assert spans[0].path == "a.py"
        assert spans[0].first_line == 1

    def test_line_count_property(self) -> None:
        spans = locate_spans([("a.py", 7)], 50)
        assert spans[0].line_count == 7
