"""Environment checks and single-file-component comment handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from hag.comments import strip_comments
from hag.environment import (
    Status,
    check_fonts,
    check_output_writable,
    check_pdf_library,
    check_python,
    check_pdf_reader,
    run_all,
)


class TestEnvironment:
    def test_python_version_reported(self) -> None:
        item = check_python()
        assert item.status is Status.OK
        assert "." in item.detail

    def test_pdf_library_present(self) -> None:
        pytest.importorskip("fpdf")
        assert check_pdf_library().status is Status.OK

    def test_pdf_reader_present(self) -> None:
        pytest.importorskip("pypdf")
        assert check_pdf_reader().status is Status.OK

    def test_fonts_resolved(self) -> None:
        item = check_fonts()
        assert item.status in (Status.OK, Status.WARN)

    def test_writable_directory(self, tmp_path: Path) -> None:
        item = check_output_writable(tmp_path / "out")
        assert item.status is Status.OK

    def test_unwritable_directory(self, tmp_path: Path) -> None:
        blocker = tmp_path / "file"
        blocker.write_text("x", encoding="utf-8")
        item = check_output_writable(blocker)
        assert item.status is Status.FAIL

    def test_run_all_lists_every_check(self) -> None:
        report = run_all()
        names = [i.name for i in report.items]
        assert "字体" in names
        assert "PDF 生成库" in names
        assert len(names) == 5

    def test_can_run_reflects_failures(self) -> None:
        report = run_all()
        assert report.can_run == (not report.failures)

    def test_text_is_readable(self) -> None:
        text = run_all().to_text()
        assert "字体" in text
        assert "处理：" in text or all(i.status is Status.OK for i in run_all().items)


class TestSingleFileComponents:
    """Vue and Svelte files mix HTML, JS and CSS comment styles."""

    def test_html_comment_removed_in_vue(self) -> None:
        src = "<div><!-- hidden --><span>x</span></div>"
        result = strip_comments(src, ".vue")
        assert "hidden" not in result.text
        assert "<span>x</span>" in result.text

    def test_html_comment_removed_in_svelte(self) -> None:
        src = "<div><!-- hidden --><span>x</span></div>"
        result = strip_comments(src, ".svelte")
        assert "hidden" not in result.text

    def test_script_comments_also_removed(self) -> None:
        src = "<script>\n// js\nconst a = 1; /* block */\n</script>"
        result = strip_comments(src, ".vue")
        assert "// js" not in result.text
        assert "block" not in result.text
        assert "const a = 1;" in result.text

    def test_style_comment_removed(self) -> None:
        src = "<style>\n.a { /* hidden */ color: red; }\n</style>"
        result = strip_comments(src, ".vue")
        assert "hidden" not in result.text
        assert "color: red" in result.text

    def test_code_between_comments_survives(self) -> None:
        src = "<template>\n  <div><!-- c --></div>\n</template>"
        result = strip_comments(src, ".vue")
        assert "<template>" in result.text
        assert "<div>" in result.text

    def test_plain_html_still_works(self) -> None:
        src = "<div><!-- c --><span>x</span></div>"
        result = strip_comments(src, ".html")
        assert "c -->" not in result.text


class TestNoFalseDeletion:
    """Deliberately tricky strings: the code must survive intact."""

    @pytest.mark.parametrize(
        ("src", "ext", "must_contain"),
        [
            ('const u = "http://x.com/a"; // c', ".js", "http://x.com/a"),
            ('s = "#fff"  # c', ".py", "#fff"),
            ('s = "/* x */"; // c', ".c", "/* x */"),
            ('s = "<!-- x -->"', ".html", "<!-- x -->"),
        ],
    )
    def test_string_contents_preserved(self, src: str, ext: str, must_contain: str) -> None:
        result = strip_comments(src, ext)
        assert must_contain in result.text

    def test_cpp_raw_string_preserved(self) -> None:
        src = 'auto s = R"(a // b)"; // note'
        result = strip_comments(src, ".cpp")
        assert "a // b" in result.text
        assert "note" not in result.text

    def test_kotlin_triple_quote_preserved(self) -> None:
        src = 'val s = """a // b""" // note'
        result = strip_comments(src, ".kt")
        assert "a // b" in result.text
        assert "note" not in result.text
