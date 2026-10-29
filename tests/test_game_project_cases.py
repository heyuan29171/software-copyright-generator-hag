"""The three problems a real game project exposed.

  * icon lookup rows became thousands of `<U+1F56F>` placeholders;
  * Chinese-heavy pages exceeded a ten-line limit that a Chinese-language game
    cannot meet;
  * the material ended on `N);`, the middle of a call.
"""

from __future__ import annotations

from hag.audit import Level, check_cjk_line_density
from hag.fonts import resolve_font_pair
from hag.layout import MAX_CJK_LINES_PER_PAGE
from hag.pipeline import _ends_cleanly, _mostly_unrenderable
from hag.selection import FileSpan, select_material

E = "\U0001F600"
VS = "\uFE0F"


class TestIconTableLinesAreSkipped:
    def test_icon_row_detected(self) -> None:
        pair = resolve_font_pair()
        row = f"'{E}{VS}': 'candle', '{E}': 'moon', '{E}': 'star',"
        assert _mostly_unrenderable(row, pair)

    def test_single_emoji_in_sentence_kept(self) -> None:
        pair = resolve_font_pair()
        line = f"  toast('ok {E}');"
        assert not _mostly_unrenderable(line, pair)

    def test_two_emoji_kept(self) -> None:
        """Three is the threshold; a message with two emoji is ordinary."""
        pair = resolve_font_pair()
        line = f"  msg = 'done {E}{E}';"
        assert not _mostly_unrenderable(line, pair)

    def test_plain_code_untouched(self) -> None:
        pair = resolve_font_pair()
        assert not _mostly_unrenderable("const total = items.length + 1;", pair)

    def test_chinese_text_untouched(self) -> None:
        pair = resolve_font_pair()
        assert not _mostly_unrenderable("const title = '\u639b\u673a\u96c6\u5361';", pair)


class TestChineseLimitIsReachable:
    def test_limit_is_twenty(self) -> None:
        """Ten was too strict: a Chinese game averages two or three but spikes
        to the mid-twenties on interface-heavy pages."""
        assert MAX_CJK_LINES_PER_PAGE >= 20

    def test_twenty_lines_pass(self) -> None:
        page = ["\u4e2d\u6587"] * 20 + ["code"] * 30
        checks = check_cjk_line_density([page], MAX_CJK_LINES_PER_PAGE)
        assert checks[0].level is Level.PASS

    def test_twenty_one_fails(self) -> None:
        page = ["\u4e2d\u6587"] * 21 + ["code"] * 29
        checks = check_cjk_line_density([page], MAX_CJK_LINES_PER_PAGE)
        assert checks[0].level is Level.WARN

    def test_reported_as_warning_not_failure(self) -> None:
        page = ["\u4e2d\u6587"] * 40 + ["code"] * 10
        checks = check_cjk_line_density([page], MAX_CJK_LINES_PER_PAGE)
        assert all(c.level is not Level.FAIL for c in checks)


class TestEndsCleanly:
    def test_closing_brace_is_clean(self) -> None:
        assert _ends_cleanly(["x()", "}"])

    def test_balanced_call_is_clean(self) -> None:
        """`N);` with its opener present is an ordinary statement ending."""
        assert _ends_cleanly(["f(a,", "  N);"])

    def test_unbalanced_call_is_not_clean(self) -> None:
        """Only a missing opener makes the excerpt look truncated."""
        assert not _ends_cleanly(["f(a,", "  N"])

    def test_open_block_is_not_clean(self) -> None:
        assert not _ends_cleanly(["function x() {"])

    def test_trailing_comma_is_not_clean(self) -> None:
        assert not _ends_cleanly(["a", "  b,"])

    def test_open_paren_is_not_clean(self) -> None:
        assert not _ends_cleanly(["call("])

    def test_operator_ending_is_not_clean(self) -> None:
        assert not _ends_cleanly(["total = a +"])


class TestBackSegmentPrefersCleanEnding:
    def test_back_segment_chooses_a_clean_file_last(self) -> None:
        """A file ending mid-call may sit inside a segment but not at its end."""
        files = [
            FileSpan(f"f{i}.py", 1500, tuple(range(1, 1500)), ends_cleanly=(i % 2 == 0))
            for i in range(6)
        ]
        selection = select_material(files)
        last_piece = selection.back.pieces[-1]
        last_file = selection.ordered_files[last_piece[0]]
        assert last_file.ends_cleanly or len(
            [f for f in files if f.ends_cleanly]
        ) < len(selection.back.pieces)

    def test_span_defaults_to_clean(self) -> None:
        assert FileSpan("a.py", 10).ends_cleanly
