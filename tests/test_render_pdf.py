"""Rendering must produce the page count it claims.

The decisive assertion throughout: ``rendered_pages`` comes from re-opening
the finished PDF, so it is what a reviewer would actually count, not what the
line arithmetic predicted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hag.layout import LINES_PER_PAGE, LayoutViolation
from hag.pagination import material_lines, paginate
from hag.render_pdf import (
    HeaderSpec,
    RenderError,
    count_pdf_pages,
    prepare_page_lines,
    render_code_pdf,
)

fpdf = pytest.importorskip("fpdf", reason="fpdf2 optional dependency not installed")
pypdf = pytest.importorskip("pypdf", reason="pypdf optional dependency not installed")


def make_pages(n_lines: int, per_page: int = LINES_PER_PAGE) -> list[list[str]]:
    lines = [f"const value{i} = {i};" for i in range(n_lines)]
    return paginate(lines, per_page)


def make_header() -> HeaderSpec:
    return HeaderSpec(software_name="TestSystem", version="V1.0")


class TestHeaderSpec:
    def test_requires_name(self) -> None:
        with pytest.raises(RenderError):
            HeaderSpec(software_name="  ", version="V1.0")

    def test_requires_version(self) -> None:
        with pytest.raises(RenderError):
            HeaderSpec(software_name="Sys", version="")

    def test_left_contains_name_and_version(self) -> None:
        header = make_header()
        assert "TestSystem" in header.left
        assert "V1.0" in header.left

    def test_version_not_normalised(self) -> None:
        """V must be preserved exactly: the form decides whether to include it."""
        assert HeaderSpec("Sys", "V1.0").left.startswith("Sys V1.0")
        assert HeaderSpec("Sys", "1.0").left.startswith("Sys 1.0")


class TestRendering:
    def test_single_page(self, tmp_path: Path) -> None:
        result = render_code_pdf(make_pages(10), tmp_path / "a.pdf", make_header())
        assert result.rendered_pages == 1

    def test_exact_multiple_of_page_capacity(self, tmp_path: Path) -> None:
        result = render_code_pdf(make_pages(100), tmp_path / "b.pdf", make_header())
        assert result.rendered_pages == 2
        assert result.is_uniform

    def test_partial_last_page(self, tmp_path: Path) -> None:
        result = render_code_pdf(make_pages(120), tmp_path / "c.pdf", make_header())
        assert result.rendered_pages == 3
        assert result.page_line_counts == [50, 50, 20]
        assert result.is_uniform

    def test_rendered_pages_matches_file(self, tmp_path: Path) -> None:
        out = tmp_path / "d.pdf"
        result = render_code_pdf(make_pages(250), out, make_header())
        assert result.rendered_pages == count_pdf_pages(out) == 5

    def test_sixty_page_material(self, tmp_path: Path) -> None:
        out = tmp_path / "e.pdf"
        result = render_code_pdf(make_pages(60 * LINES_PER_PAGE), out, make_header())
        assert result.rendered_pages == 60
        assert count_pdf_pages(out) == 60

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        out = tmp_path / "nested" / "deep" / "f.pdf"
        render_code_pdf(make_pages(10), out, make_header())
        assert out.is_file()


class TestHeaderInOutput:
    def test_header_text_extractable(self, tmp_path: Path) -> None:
        out = tmp_path / "h.pdf"
        header = HeaderSpec(software_name="HelloSystem", version="V2.0")
        render_code_pdf(make_pages(10), out, header)
        text = pypdf.PdfReader(str(out)).pages[0].extract_text()
        assert "HelloSystem" in text
        assert "V2.0" in text

    def test_page_number_present(self, tmp_path: Path) -> None:
        out = tmp_path / "p.pdf"
        render_code_pdf(make_pages(120), out, make_header())
        reader = pypdf.PdfReader(str(out))
        for index, page in enumerate(reader.pages, start=1):
            text = page.extract_text()
            assert str(index) in text

    def test_body_content_present(self, tmp_path: Path) -> None:
        out = tmp_path / "body.pdf"
        render_code_pdf(make_pages(10), out, make_header())
        text = pypdf.PdfReader(str(out)).pages[0].extract_text()
        assert "const value0 = 0;" in text

    def test_cjk_header_renders_without_tofu(self, tmp_path: Path) -> None:
        out = tmp_path / "cjk.pdf"
        header = HeaderSpec(software_name="学生管理系统", version="V1.0")
        render_code_pdf(make_pages(5), out, header)
        text = pypdf.PdfReader(str(out)).pages[0].extract_text()
        assert "学生管理系统" in text


class TestGuards:
    def test_no_pages_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RenderError, match="nothing to render"):
            render_code_pdf([], tmp_path / "x.pdf", make_header())

    def test_overfull_page_raises(self, tmp_path: Path) -> None:
        """A page with more lines than the layout holds must never be drawn."""
        pages = [["line"] * (LINES_PER_PAGE + 1)]
        with pytest.raises(RenderError, match="only fits"):
            render_code_pdf(pages, tmp_path / "x.pdf", make_header())

    def test_impossible_geometry_raises(self, tmp_path: Path) -> None:
        """55 lines on a page that holds 52 must be refused, not truncated."""
        with pytest.raises(LayoutViolation):
            render_code_pdf(
                make_pages(55, per_page=55), tmp_path / "x.pdf", make_header(), lines_per_page=55
            )


class TestPageLineCounting:
    def test_prepare_expands_tabs(self) -> None:
        assert prepare_page_lines(["a\tb"]) == ["a   b"]

    def test_prepare_strips_control_characters(self) -> None:
        assert prepare_page_lines(["a\x00b"]) == ["a b"]


class TestWrappingIntegration:
    def test_long_line_wraps_before_pagination(self, tmp_path: Path) -> None:
        """Wrapping upstream keeps the page count exact."""
        long_source = "\n".join("x" * 200 for _ in range(10))
        lines = material_lines(long_source, 60)
        assert all(len(line) <= 60 for line in lines)
        pages = paginate(lines, LINES_PER_PAGE)
        out = tmp_path / "wrap.pdf"
        result = render_code_pdf(pages, out, make_header())
        assert result.rendered_pages == len(pages)
        assert count_pdf_pages(out) == len(pages)
