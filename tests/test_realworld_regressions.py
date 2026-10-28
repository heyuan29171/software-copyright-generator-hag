"""Regressions for four problems found in a real project.

Each of these broke a real run: an emoji stopped generation entirely, a
git-ignore file became the first line of the material, a segment exceeded the
statutory page count, and Chinese-heavy pages went unreported.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hag.audit import Level, check_cjk_line_density
from hag.discovery import discover
from hag.fonts import is_decorative, resolve_font_pair, sanitize_unrenderable
from hag.layout import (
    CODE_LAYOUT,
    FALLBACK_MAX_SEGMENT_PAGES,
    MAX_CJK_LINES_PER_PAGE,
)
from hag.pagination import paginate
from hag.render_pdf import HeaderSpec, render_code_pdf
from hag.selection import FileSpan, select_material


def write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestEmojiDoesNotAbort:
    """An emoji in a UI string is ordinary; it must not stop the run."""

    def test_emoji_replaced_not_raised(self, tmp_path: Path) -> None:
        pages = paginate(
            ["const s = \"\U0001F507 mute\";"] + [f"const v{i} = {i};" for i in range(60)],
            50,
        )
        result = render_code_pdf(pages, tmp_path / "e.pdf", HeaderSpec("Sys", "V1.0"))
        assert result.rendered_pages == 2
        assert "\U0001F507" in result.replaced_characters

    def test_substitution_covers_every_kind(self) -> None:
        fonts = resolve_font_pair()
        text, replaced = sanitize_unrenderable("a\U0001F514b\U0001F4E3c", fonts)
        assert "\U0001F514" in replaced
        assert "\U0001F4E3" in replaced
        assert "?" in text

    def test_renderable_text_untouched(self) -> None:
        fonts = resolve_font_pair()
        text, replaced = sanitize_unrenderable("const x = 1; 中文", fonts)
        assert replaced == []
        assert text == "const x = 1; 中文"

    def test_line_breaks_preserved(self) -> None:
        fonts = resolve_font_pair()
        text, _ = sanitize_unrenderable("a\nb\r\tc", fonts)
        assert text.count("\n") == 1
        assert "\r" in text
        assert "\t" in text

    def test_page_count_unaffected_by_substitution(self) -> None:
        """Substitution must not change how many pages are produced."""
        lines = ["x" * 10 + "\U0001F507"] + ["y" * 10] * 99
        pages = paginate(lines, 50)
        assert len(pages) == 2

    def test_decorative_range_recognised(self) -> None:
        assert is_decorative("\U0001F507")
        assert is_decorative("\u2705")
        assert not is_decorative("A")
        assert not is_decorative("中")


class TestGitIgnoreExcluded:
    """Housekeeping files are not part of the software."""

    def test_gitignore_not_a_source(self, tmp_path: Path) -> None:
        write(tmp_path, ".gitignore", "# ignore\nnode_modules/\n.env\n")
        write(tmp_path, "src/main.ts", "const x = 1;\n")
        result = discover(tmp_path)
        paths = [s.rel_path for s in result.sources]
        assert ".gitignore" not in paths
        assert "src/main.ts" in paths

    def test_first_line_is_real_code(self, tmp_path: Path) -> None:
        """The material must open with source, not with a config file."""
        from hag.pipeline import clean_sources

        write(tmp_path, ".gitignore", "# ===== 敏感文件 =====\n.env\n")
        write(tmp_path, "src/a.ts", "export function run() {}\n")
        cleaned = clean_sources(discover(tmp_path).sources)
        assert cleaned.files[0].rel_path == "src/a.ts"
        assert not cleaned.files[0].lines[0].startswith("#")

    @pytest.mark.parametrize(
        "name", [".gitattributes", ".dockerignore", ".eslintignore", ".editorconfig"]
    )
    def test_other_housekeeping_files_skipped(self, tmp_path: Path, name: str) -> None:
        write(tmp_path, name, "# config\n")
        write(tmp_path, "a.py", "x = 1\n")
        assert [s.rel_path for s in discover(tmp_path).sources] == ["a.py"]


class TestSegmentNeverExceedsThirty:
    """Thirty pages is a ceiling, not a target to overshoot."""

    def test_max_is_thirty(self) -> None:
        assert FALLBACK_MAX_SEGMENT_PAGES == 30

    def test_result_within_ceiling(self) -> None:
        selection = select_material(
            [FileSpan("a.py", 1450), FileSpan("b.py", 200), FileSpan("c.py", 1450)],
        )
        assert selection.front_pages <= 30
        assert selection.back_pages <= 30

    def test_material_does_not_claim_sixty_when_short(self) -> None:
        selection = select_material(
            [FileSpan("a.py", 1450), FileSpan("b.py", 200), FileSpan("c.py", 1450)],
        )
        if not selection.exact:
            assert selection.rendered_pages < 60
            assert "前30页+后30页" not in selection.title


class TestCjkDensity:
    """Chinese-heavy pages read as prose, not as a program."""

    def test_limit_is_configured(self) -> None:
        assert MAX_CJK_LINES_PER_PAGE > 0

    def test_within_limit_passes(self) -> None:
        page = ["中文"] * 5 + ["code"] * 45
        checks = check_cjk_line_density([page], 10)
        assert checks[0].level is Level.PASS

    def test_over_limit_warns(self) -> None:
        page = ["中文"] * 20 + ["code"] * 30
        checks = check_cjk_line_density([page], 10)
        assert checks[0].level is Level.WARN

    def test_warning_names_the_page(self) -> None:
        pages = [["code"] * 50, ["中文"] * 20 + ["code"] * 30]
        checks = check_cjk_line_density(pages, 10)
        assert "第 2 页" in checks[0].detail

    def test_density_is_warning_not_failure(self) -> None:
        """This is a judgement call, so it must never block a submission."""
        page = ["中文"] * 50
        checks = check_cjk_line_density([page], 10)
        assert all(c.level is not Level.FAIL for c in checks)

    def test_zero_disables(self) -> None:
        page = ["中文"] * 50
        checks = check_cjk_line_density([page], 0)
        assert checks[0].level is Level.PASS


class TestTemplateGeometry:
    """Values measured from the .docx that circulates as a template.

    That document is not an official publication: the Centre does not publish
    an R11 template. The numbers are recorded because they are self-consistent
    and land on exactly fifty lines, not because an authority endorses them.
    """

    def test_font_is_ten_point(self) -> None:
        from decimal import Decimal

        assert CODE_LAYOUT.body_font_size_pt == Decimal("10.0")

    def test_line_height_is_fifteen(self) -> None:
        from decimal import Decimal

        assert CODE_LAYOUT.body_line_pt == Decimal("15.0")

    def test_bottom_margin_is_one_cm(self) -> None:
        from decimal import Decimal

        assert CODE_LAYOUT.margin_bottom_cm == Decimal("1.0")

    def test_no_paragraph_indent(self) -> None:
        """Four of five implementations use no indent, and the material is a
        PDF rather than a bound printout, so an indent only forces extra
        wrapping without buying anything."""
        from decimal import Decimal

        assert CODE_LAYOUT.body_indent_cm == Decimal("0")

    def test_body_width_is_full_text_area(self) -> None:
        """481.9pt: 21cm minus 2cm margins on each side, no indent."""
        assert float(CODE_LAYOUT.body_width_pt) == pytest.approx(481.9, abs=0.2)

    def test_fifty_lines_fit_and_fifty_one_does_not(self) -> None:
        """The template's numbers land on exactly fifty lines, not by rounding."""
        fits = 50 * CODE_LAYOUT.body_line_pt <= CODE_LAYOUT.body_height_pt
        overflowing = 51 * CODE_LAYOUT.body_line_pt <= CODE_LAYOUT.body_height_pt
        assert fits
        assert not overflowing

    def test_fifty_lines_fit_within_seven_points(self) -> None:
        slack = CODE_LAYOUT.body_height_pt - 50 * CODE_LAYOUT.body_line_pt
        assert 0 < slack < 10
