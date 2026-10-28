"""Comment stripping must never corrupt code.

The critical failure mode: a regex-based stripper deletes ``//`` inside a
string literal or ``#`` inside a CSS colour, silently changing the code that
is submitted as identifying material.
"""

from __future__ import annotations

import ast

import pytest

from hag.comments import StripMethod, strip_comments


class TestStringSafety:
    def test_url_in_string_survives(self) -> None:
        src = 'const url = "https://example.com/path";\n'
        result = strip_comments(src, ".ts")
        assert "https://example.com/path" in result.text

    def test_line_comment_after_string_removed(self) -> None:
        src = 'const a = "x"; // explained\n'
        result = strip_comments(src, ".ts")
        assert "explained" not in result.text
        assert '"x"' in result.text

    def test_css_hex_colour_survives(self) -> None:
        src = "body { color: #ff0000; }\n"
        result = strip_comments(src, ".css")
        assert "#ff0000" in result.text

    def test_regex_literal_not_treated_as_comment(self) -> None:
        src = "const re = /a\\/b/; // trailing\n"
        result = strip_comments(src, ".js")
        assert "trailing" not in result.text
        assert "/a\\/b/" in result.text

    def test_escaped_quote_inside_string(self) -> None:
        src = 'const s = "he said \\"hi\\" // not a comment";\n'
        result = strip_comments(src, ".ts")
        assert "// not a comment" in result.text

    def test_block_comment_removed(self) -> None:
        src = "/* header */\nint x = 1;\n"
        result = strip_comments(src, ".c")
        assert "header" not in result.text
        assert "int x = 1;" in result.text

    def test_nested_block_markers_in_string(self) -> None:
        src = 'const s = "/* not a comment */";\n'
        result = strip_comments(src, ".ts")
        assert "/* not a comment */" in result.text

    def test_html_comment_removed(self) -> None:
        src = "<div><!-- hidden --><span>x</span></div>\n"
        result = strip_comments(src, ".html")
        assert "hidden" not in result.text
        assert "<span>x</span>" in result.text


class TestLineNumbersPreserved:
    def test_newlines_kept(self) -> None:
        src = "a();\n// comment\nb();\n"
        result = strip_comments(src, ".js")
        assert result.text.count("\n") == src.count("\n")

    def test_block_comment_newlines_kept(self) -> None:
        src = "/* one\ntwo\nthree */\nx();\n"
        result = strip_comments(src, ".c")
        assert result.text.count("\n") == src.count("\n")


class TestPython:
    def test_line_comment_removed(self) -> None:
        result = strip_comments("x = 1  # note\n", ".py")
        assert "note" not in result.text
        assert "x = 1" in result.text

    def test_docstring_removed(self) -> None:
        src = 'def f():\n    """Doc."""\n    return 1\n'
        result = strip_comments(src, ".py")
        assert "Doc." not in result.text
        assert "return 1" in result.text
        assert result.method is StripMethod.FULL

    def test_module_docstring_removed(self) -> None:
        src = '"""Module doc."""\nimport os\n'
        result = strip_comments(src, ".py")
        assert "Module doc." not in result.text

    def test_body_left_empty_gets_pass(self) -> None:
        src = 'def f():\n    """Only a docstring."""\n'
        result = strip_comments(src, ".py")
        assert "pass" in result.text
        ast.parse(result.text)

    def test_result_still_parses(self) -> None:
        src = (
            '"""Module."""\n'
            "import os\n\n"
            "def f(a):\n"
            '    """Doc."""\n'
            "    return a  # trailing\n\n"
            "class C:\n"
            '    """Class doc."""\n'
            "    def m(self):\n"
            "        return 1\n"
        )
        result = strip_comments(src, ".py")
        ast.parse(result.text)

    def test_shebang_preserved(self) -> None:
        src = "#!/usr/bin/env python3\nx = 1\n"
        result = strip_comments(src, ".py")
        assert "#!/usr/bin/env python3" in result.text

    def test_encoding_declaration_preserved(self) -> None:
        src = "# -*- coding: utf-8 -*-\nx = 1\n"
        result = strip_comments(src, ".py")
        assert "coding: utf-8" in result.text

    def test_hash_inside_string_survives(self) -> None:
        src = 's = "# not a comment"\n'
        result = strip_comments(src, ".py")
        assert "# not a comment" in result.text

    def test_triple_quote_inside_string(self) -> None:
        src = 's = """keep"""\n'
        result = strip_comments(src, ".py")
        assert "keep" in result.text


class TestPartialCoverage:
    def test_shell_is_line_only(self) -> None:
        result = strip_comments("# comment\necho hi # trailing\n", ".sh")
        assert result.method is StripMethod.LINE_ONLY
        assert "comment" not in result.text
        assert result.warnings
        assert not result.is_complete

    def test_sql_is_line_only(self) -> None:
        result = strip_comments("-- note\nSELECT 1;\n", ".sql")
        assert result.method is StripMethod.LINE_ONLY
        assert "note" not in result.text

    def test_json_has_no_comments(self) -> None:
        result = strip_comments('{"a": 1}\n', ".json")
        assert result.method is StripMethod.NONE
        assert result.text == '{"a": 1}\n'

    def test_unknown_suffix_is_none(self) -> None:
        result = strip_comments("whatever\n", ".xyz")
        assert result.method is StripMethod.NONE


class TestSafety:
    def test_c_like_code_still_compiles_conceptually(self) -> None:
        src = (
            "/* header */\n"
            "function greet(name) {\n"
            '  const msg = "hello // world"; // greet\n'
            "  return msg;\n"
            "}\n"
        )
        result = strip_comments(src, ".js")
        assert "function greet(name)" in result.text
        assert "hello // world" in result.text
        assert "greet\n" not in result.text.replace("greet(", "")

    def test_empty_input(self) -> None:
        result = strip_comments("", ".py")
        assert result.text == ""

    def test_only_comment_file(self) -> None:
        result = strip_comments("# just a comment\n", ".py")
        assert result.text.strip() == ""

    @pytest.mark.parametrize("suffix", [".ts", ".go", ".rs", ".java", ".kt", ".swift"])
    def test_no_exception_across_languages(self, suffix: str) -> None:
        src = "// c\nlet x = 1;\n"
        result = strip_comments(src, suffix)
        assert "let x = 1;" in result.text
