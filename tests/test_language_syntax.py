"""Language-specific string forms that would otherwise be mistaken for comments.

Each case here was previously handled either by not stripping at all (Groovy)
or by stripping only whole lines (PowerShell, R). Both were safe but incomplete:
comments survived into the submitted material.
"""

from __future__ import annotations

import pytest

from hag.comments import StripMethod, strip_comments


class TestGroovy:
    def test_line_comment_removed(self) -> None:
        result = strip_comments("def x = 1  // note", ".groovy")
        assert "note" not in result.text
        assert result.method is StripMethod.FULL

    def test_block_comment_removed(self) -> None:
        result = strip_comments("/* note */ def x = 1", ".groovy")
        assert "note" not in result.text

    def test_slashy_string_preserved(self) -> None:
        """`/a\\/b/` is a regex, not division followed by a comment."""
        result = strip_comments("def p = /a\\/b/  // note", ".groovy")
        assert "/a\\/b/" in result.text
        assert "note" not in result.text

    def test_division_not_treated_as_string(self) -> None:
        """`total / count` must stay as it is."""
        result = strip_comments("def r = total / count  // note", ".groovy")
        assert "total / count" in result.text
        assert "note" not in result.text

    def test_slashy_after_assignment(self) -> None:
        result = strip_comments("x = /pattern/", ".groovy")
        assert "/pattern/" in result.text

    def test_triple_quote_preserved(self) -> None:
        src = 'def s = """a // b""" // note'
        result = strip_comments(src, ".groovy")
        assert "a // b" in result.text
        assert "note" not in result.text


class TestPowerShell:
    def test_line_comment_removed(self) -> None:
        result = strip_comments("$x = 1  # note", ".ps1")
        assert "note" not in result.text
        assert result.method is StripMethod.FULL

    def test_block_comment_removed(self) -> None:
        result = strip_comments("<# note #> $x = 1", ".ps1")
        assert "note" not in result.text

    def test_here_string_hash_preserved(self) -> None:
        """`#` inside a here-string is text, not a comment."""
        src = '@"\nline with # hash\n"@\n# real comment'
        result = strip_comments(src, ".ps1")
        assert "# hash" in result.text
        assert "real comment" not in result.text

    def test_single_quoted_here_string(self) -> None:
        src = "@'\nno $expansion # here\n'@\n# c"
        result = strip_comments(src, ".ps1")
        assert "# here" in result.text
        assert "# c" not in result.text

    def test_here_string_needs_line_position(self) -> None:
        """`@"` mid-line is not a here-string opener."""
        result = strip_comments('$s = @" not a here string', ".ps1")
        assert "$s" in result.text


class TestHCL:
    def test_hash_comment_removed(self) -> None:
        result = strip_comments("x = 1  # note", ".tf")
        assert "note" not in result.text
        assert result.method is StripMethod.FULL

    def test_slash_comment_removed(self) -> None:
        result = strip_comments("x = 1  // note", ".tf")
        assert "note" not in result.text

    def test_block_comment_removed(self) -> None:
        result = strip_comments("/* note */ x = 1", ".tf")
        assert "note" not in result.text

    def test_string_hash_preserved(self) -> None:
        result = strip_comments('name = "a # b"  # note', ".tf")
        assert "a # b" in result.text
        assert "note" not in result.text


class TestRLang:
    def test_comment_removed(self) -> None:
        result = strip_comments("x <- 1  # note", ".r")
        assert "note" not in result.text
        assert result.method is StripMethod.FULL

    def test_raw_string_hash_preserved(self) -> None:
        src = 'x <- r"(a # b)"  # note'
        result = strip_comments(src, ".r")
        assert "a # b" in result.text
        assert "note" not in result.text

    def test_hash_in_normal_string_preserved(self) -> None:
        result = strip_comments('s <- "#fff"  # note', ".r")
        assert "#fff" in result.text
        assert "note" not in result.text


class TestNoRegressionOnOtherLanguages:
    @pytest.mark.parametrize(
        ("src", "ext", "kept"),
        [
            ('const u = "http://x.com/a"; // c', ".js", "http://x.com/a"),
            ('s = "#fff"  # c', ".py", "#fff"),
            ('auto s = R"(a // b)"; // c', ".cpp", "a // b"),
            ('val s = """a // b""" // c', ".kt", "a // b"),
            ("<div><!-- c --><span>x</span></div>", ".vue", "<span>x</span>"),
        ],
    )
    def test_code_survives(self, src: str, ext: str, kept: str) -> None:
        assert kept in strip_comments(src, ext).text
