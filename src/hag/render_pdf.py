"""Rendering the source-code identifying material to PDF.

Two rules dominate this module.

**Paginate before drawing.** The lines handed to the renderer are already
paginated, and each page is drawn at y-offsets computed by arithmetic. The
renderer never lets the library decide where a page ends, because an
automatic page break would silently change the page count.

**Read the page count back from the artifact.** The application form asks for
the number of pages, and that number must match what a reviewer counts in the
PDF. Computing it from the line count is a guess; ``rendered_pages`` opens the
finished file and counts.

If the configured geometry cannot hold the lines, rendering refuses rather
than producing a document that is one page longer than its own filename claims.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .fonts import (
    FontError,
    FontPair,
    assert_coverage,
    resolve_font_pair,
    sanitize_unrenderable,
)
from .layout import CODE_LAYOUT, LINES_PER_PAGE, CodeLayout, assert_layout_invariants
from .pagination import sanitize_line


class RenderError(Exception):
    """Raised when a correct document cannot be produced."""


@dataclass(frozen=True, slots=True)
class HeaderSpec:
    """Header text for the material.

    Per the Centre's published guidance the header carries the software name
    and version, and the page number sits at the top right.
    """

    software_name: str
    version: str
    company: str | None = None

    def __post_init__(self) -> None:
        if not self.software_name.strip():
            raise RenderError("header requires a software name")
        if not self.version.strip():
            raise RenderError("header requires a version string")

    @property
    def left(self) -> str:
        base = f"{self.software_name} {self.version}"
        if self.company:
            return f"{base} {self.company}"
        return base


@dataclass(slots=True)
class RenderResult:
    pdf_path: Path
    rendered_pages: int
    lines_per_page: int
    header_left: str
    page_line_counts: list[int]
    replaced_characters: list[str] = field(default_factory=list)

    @property
    def is_uniform(self) -> bool:
        """True when every page but the last holds exactly `lines_per_page`."""
        if len(self.page_line_counts) <= 1:
            return True
        return all(n == self.lines_per_page for n in self.page_line_counts[:-1])


def count_pdf_pages(pdf_path: Path) -> int:
    """Number of page objects in a finished PDF.

    This is the authoritative value for the form's page field.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise RenderError("pypdf is required to read back the page count") from exc

    try:
        reader = PdfReader(str(pdf_path))
        return len(reader.pages)
    except Exception as exc:
        raise RenderError(f"cannot read {pdf_path}: {exc}") from exc


def _require_fpdf():  # noqa: ANN202 - returns the FPDF class
    try:
        from fpdf import FPDF
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RenderError(
            "fpdf2 is not installed; install it with: pip install 'hag[pdf]'"
        ) from exc
    return FPDF


def _pt(value: Decimal) -> float:
    return float(value)


def cm_to_pt(value: Decimal) -> Decimal:
    """Convert centimetres to PostScript points."""
    return value / Decimal("2.54") * Decimal(72)


def _font_runs(
    text: str, fonts: FontPair
) -> list[tuple[str, str]]:
    """Split text into runs of (font_key, chunk) for mixed-script lines."""
    runs: list[tuple[str, str]] = []
    current_key = ""
    current: list[str] = []

    for char in text:
        key = "mono" if fonts.mono.has(char) else "cjk"
        if key != current_key and current:
            runs.append((current_key, "".join(current)))
            current = []
        current_key = key
        current.append(char)

    if current:
        runs.append((current_key, "".join(current)))
    return runs


def render_code_pdf(
    pages: Sequence[Sequence[str]],
    out_path: Path,
    header: HeaderSpec,
    *,
    layout: CodeLayout = CODE_LAYOUT,
    lines_per_page: int | None = None,
    fonts: FontPair | None = None,
    draft: bool = False,
) -> RenderResult:
    """Render pre-paginated lines to a PDF and return the real page count."""
    if not pages:
        raise RenderError("nothing to render: no pages")

    # Never infer the capacity from the first page's length: a short document
    # would then look like it "uses" a smaller page and slip past the statute's
    # 50-line floor. The capacity is a property of the page, not of the input.
    expected_lines = lines_per_page or LINES_PER_PAGE
    assert_layout_invariants(layout, expected_lines)

    for index, page in enumerate(pages):
        if len(page) > expected_lines:
            raise RenderError(
                f"page {index + 1} holds {len(page)} lines but the layout only fits "
                f"{expected_lines}; paginate with lines_per_page={expected_lines}"
            )

    fonts = fonts or resolve_font_pair()
    all_lines = [line for page in pages for line in page]

    # The header appears on every page, so a missing glyph there would be
    # wrong everywhere; that still aborts. Body text is different: an emoji in
    # a UI string is ordinary, and refusing to produce material over it would
    # leave the user with nothing. Such characters become "?" and are reported.
    assert_coverage(fonts, [header.left])
    sanitized_pages, replaced = _sanitize_pages(pages, fonts)
    pages = sanitized_pages

    FPDF = _require_fpdf()

    out_path.parent.mkdir(parents=True, exist_ok=True)

    pdf = FPDF(
        orientation="P",
        unit="pt",
        format=(_pt(layout.page_width_pt), _pt(layout.page_height_pt)),
    )
    pdf.set_auto_page_break(False)
    pdf.set_margins(
        _pt(cm_to_pt(layout.margin_left_cm)),
        _pt(cm_to_pt(layout.margin_top_cm)),
        _pt(cm_to_pt(layout.margin_right_cm)),
    )
    pdf.set_creator("hag")
    pdf.set_title(f"{header.software_name} {header.version}")

    try:
        pdf.add_font("mono", "", str(fonts.mono.path))
        pdf.add_font("cjk", "", str(fonts.cjk.path))
    except Exception as exc:
        raise RenderError(f"cannot register fonts: {exc}") from exc

    margin_left = cm_to_pt(layout.margin_left_cm)
    margin_top = cm_to_pt(layout.margin_top_cm)
    body_width = layout.body_width_pt
    header_distance = cm_to_pt(layout.header_distance_cm)

    line_pt = layout.body_line_pt

    for page_index, page_lines in enumerate(pages, start=1):
        pdf.add_page()

        # Header: software name + version on the left, page number at the top right.
        pdf.set_font("cjk", "", _pt(layout.header_font_size_pt))
        header_y = header_distance
        for key, chunk in _font_runs(header.left, fonts):
            pdf.set_font("mono" if key == "mono" else "cjk", "", _pt(layout.header_font_size_pt))
            pdf.set_xy(_pt(margin_left) if not pdf.get_x() else pdf.get_x(), _pt(header_y))
            pdf.cell(text=chunk)
            if key != "mono":
                continue
        pdf.set_font("mono", "", _pt(layout.header_font_size_pt))
        page_label = str(page_index)
        label_width = pdf.get_string_width(page_label)
        pdf.set_xy(_pt(margin_left + body_width - Decimal(str(label_width))), _pt(header_y))
        pdf.cell(text=page_label)

        # Body: draw each line at an exact baseline so nothing can reflow.
        body_top = margin_top
        for row, line in enumerate(page_lines):
            baseline = body_top + line_pt * (row + 1) - layout.body_font_size_pt * Decimal(
                "0.25"
            )
            x = margin_left
            for key, chunk in _font_runs(line, fonts):
                size = _pt(layout.body_font_size_pt)
                pdf.set_font("mono" if key == "mono" else "cjk", "", size)
                pdf.set_xy(_pt(x), _pt(baseline))
                pdf.cell(text=chunk)
                x += Decimal(str(pdf.get_string_width(chunk)))

    try:
        pdf.output(str(out_path))
    except Exception as exc:
        raise RenderError(f"cannot write {out_path}: {exc}") from exc

    rendered = count_pdf_pages(out_path)
    if rendered != len(pages):
        raise RenderError(
            f"rendered {rendered} pages but {len(pages)} were paginated. "
            "The document does not match its own line budget; refusing to report "
            "a page count that a reviewer would contradict."
        )

    return RenderResult(
        pdf_path=out_path,
        rendered_pages=rendered,
        lines_per_page=expected_lines,
        header_left=header.left,
        page_line_counts=[len(p) for p in pages],
        replaced_characters=replaced,
    )


def _sanitize_pages(
    pages: Sequence[Sequence[str]], fonts: FontPair
) -> tuple[list[list[str]], list[str]]:
    """Replace undrawable body characters, collecting what was replaced.

    Line breaks are preserved, so the page count is unaffected by substitution.
    """
    sanitized: list[list[str]] = []
    replaced: list[str] = []
    for page in pages:
        new_page: list[str] = []
        for line in page:
            cleaned, lost = sanitize_unrenderable(line, fonts)
            for char in lost:
                if char not in replaced:
                    replaced.append(char)
            new_page.append(cleaned)
        sanitized.append(new_page)
    return sanitized, replaced


def prepare_page_lines(lines: Sequence[str], tab_width: int = 4) -> list[str]:
    """Sanitise already-wrapped lines for drawing.

    Wrapping happened upstream; this only expands tabs and removes control
    characters that no font can draw.
    """
    return [sanitize_line(line, tab_width=tab_width) for line in lines]


__all__ = [
    "FontError",
    "HeaderSpec",
    "RenderError",
    "RenderResult",
    "count_pdf_pages",
    "prepare_page_lines",
    "render_code_pdf",
]
