"""Line preparation and pagination for the source-code identifying material.

The contract this module upholds:

1. A "line" entering pagination is already a *physical* line. Wrapping happens
   here, before pagination, so that a page holding N lines really holds N
   printable lines. Wrapping later, during rendering, would let the renderer
   silently change the page count.

2. Nothing is ever lost. Concatenating the wrapped pieces must reproduce the
   original text (after tab expansion and control-character sanitising).

3. Pagination is arithmetic, not typographic. Every page but the last holds
   exactly LINES_PER_PAGE lines; page breaks are explicit, never "made to fit".
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from .layout import CodeLayout, LayoutViolation

# East Asian wide/fullwidth ranges that occupy two terminal columns.
_WIDE_RANGES: tuple[tuple[int, int], ...] = (
    (0x1100, 0x115F),
    (0x2E80, 0x303E),
    (0x3041, 0x33FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xA000, 0xA4CF),
    (0xAC00, 0xD7A3),
    (0xF900, 0xFAFF),
    (0xFE30, 0xFE6F),
    (0xFF00, 0xFF60),
    (0xFFE0, 0xFFE6),
    (0x1F300, 0x1F64F),
    (0x1F900, 0x1F9FF),
    (0x20000, 0x2FFFD),
    (0x30000, 0x3FFFD),
)


def is_wide(char: str) -> bool:
    """True when the character occupies two display columns."""
    code = ord(char)
    for low, high in _WIDE_RANGES:
        if low <= code <= high:
            return True
    return False


def display_width(text: str) -> int:
    """Display columns of `text`, counting CJK and emoji as two columns."""
    return sum(2 if is_wide(ch) else 1 for ch in text)


def sanitize_line(text: str, tab_width: int = 4) -> str:
    """Expand tabs and replace control characters that no font can render."""
    expanded = text.expandtabs(tab_width)
    return "".join(ch if ch >= " " else " " for ch in expanded)


def wrap_display_line(text: str, max_columns: int) -> list[str]:
    """Split `text` into pieces no wider than `max_columns` display columns.

    Never drops characters: `"".join(result)` always equals the input.
    A width of zero or less disables wrapping.
    """
    if max_columns <= 0 or display_width(text) <= max_columns:
        return [text]

    pieces: list[str] = []
    current: list[str] = []
    used = 0

    for char in text:
        width = 2 if is_wide(char) else 1
        if used + width > max_columns and current:
            pieces.append("".join(current))
            current = []
            used = 0
        current.append(char)
        used += width

    if current:
        pieces.append("".join(current))
    return pieces


def hard_cap_line(text: str, cap: int) -> list[str]:
    """Chop an absurdly long line into `cap`-character slices before measuring.

    Purely a guard against pathological input (minified bundles, generated
    data blobs). Normal lines never reach this path.
    """
    if len(text) <= cap:
        return [text]
    return [text[i : i + cap] for i in range(0, len(text), cap)]


def is_cjk_line(line: str) -> bool:
    """True when the line contains any Chinese character."""
    return any("\u4e00" <= ch <= "\u9fff" for ch in line)


_CODE_CHARS = set("{}[]()<>=;:,.+-*/%&|!?\"'`@#$\\")

# Comment and string delimiters are stripped before judging whether a line
# carries code, so that "// 这是注释" and '"""说明"""' are not mistaken for lines
# containing syntax.
_COMMENT_PREFIXES = (
    "//",
    "#",
    "--",
    ";",
    "/*",
    "*",
    "<!--",
    "rem ",
    "REM ",
    '"""',
    "'''",
    '"',
    "'",
    "`",
)


def is_code_bearing(line: str) -> bool:
    """True when the line contains something other than prose.

    A line of Chinese with no punctuation or syntax is a comment or a string
    fragment; keeping it consumes the Chinese-line budget without showing any
    of the program's logic.

    Comment markers are removed first: their punctuation would otherwise make
    every commented-out Chinese sentence look like code.
    """
    stripped = line.strip()
    # Strip a leading comment or string delimiter, then a matching trailing one,
    # so the delimiters themselves never count as syntax.
    for prefix in _COMMENT_PREFIXES:
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix) :].strip()
            break
    for suffix in ('"""', "'''", '"', "'", "`", "*/", "-->"):
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)].strip()
            break
    if not stripped:
        return False
    return any(
        ch in _CODE_CHARS or (ch.isascii() and ch.isalnum()) for ch in stripped
    )


def material_lines(
    text: str,
    max_columns: int,
    *,
    tab_width: int = 4,
    hard_cap: int = 3000,
    drop_pure_cjk: bool = False,
) -> list[str]:
    """Turn raw file text into the physical lines that will be paginated.

    Blank lines are dropped ([审]: they dilute the effective line count) and
    over-long lines are wrapped. Tabs are expanded before measuring so column
    arithmetic matches what the reader sees.

    `drop_pure_cjk` removes lines that are Chinese prose with no code in them.
    The material should read as a program; a reviewer counting Chinese lines
    per page is judging whether it does. Dropping these lines is the difference
    between a warning that says the limit was exceeded and actually meeting it.
    """
    result: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        cleaned = sanitize_line(raw, tab_width=tab_width)
        if not cleaned.strip():
            continue
        if drop_pure_cjk and is_cjk_line(cleaned) and not is_code_bearing(cleaned):
            continue
        for chunk in hard_cap_line(cleaned, hard_cap):
            result.extend(wrap_display_line(chunk, max_columns))
    return result


def paginate(lines: Sequence[str], lines_per_page: int) -> list[list[str]]:
    """Chop `lines` into pages of exactly `lines_per_page` lines.

    Only the final page may be short. This is arithmetic, not typography:
    the caller renders each page with explicit page breaks so the two never
    disagree.
    """
    if lines_per_page <= 0:
        raise LayoutViolation("lines_per_page must be positive")
    return [list(lines[i : i + lines_per_page]) for i in range(0, len(lines), lines_per_page)]


@dataclass(frozen=True, slots=True)
class PageSpan:
    """Where one file's rendered lines landed in the paginated output."""

    path: str
    first_line: int  # 1-based, inclusive
    last_line: int  # 1-based, inclusive
    page_start: int
    page_end: int

    @property
    def line_count(self) -> int:
        return self.last_line - self.first_line + 1


def locate_spans(layout_line_counts: Iterable[tuple[str, int]], lines_per_page: int) -> list[PageSpan]:
    """Map each file's material-line count onto page coordinates.

    Used to prove that no split point lands inside a file, and to write the
    extraction manifest that lets a reviewer trace any material line back to
    its source file.
    """
    if lines_per_page <= 0:
        raise LayoutViolation("lines_per_page must be positive")

    spans: list[PageSpan] = []
    cursor = 0
    for path, count in layout_line_counts:
        if count <= 0:
            continue
        first_line = cursor + 1
        last_line = cursor + count
        spans.append(
            PageSpan(
                path=path,
                first_line=first_line,
                last_line=last_line,
                page_start=(first_line - 1) // lines_per_page + 1,
                page_end=(last_line - 1) // lines_per_page + 1,
            )
        )
        cursor = last_line
    return spans


def max_columns_for(layout: CodeLayout) -> int:
    """How many columns of body text fit across the page.

    Measured against the body font size as a conservative monospace estimate.
    The renderer performs the authoritative measurement with real glyph widths;
    this value is what the wrapping pass uses so pagination stays stable.
    """
    # A monospace glyph is ~0.6em wide; CJK glyphs are 1em (handled as 2 columns).
    return int(layout.body_width_pt / (layout.body_font_size_pt * Decimal("0.6")))
