"""Single source of truth for every numeric layout value and hard rule.

Nothing else in the codebase may hard-code a page size, margin, font size,
line count or split threshold. Every value here is annotated with the
authority it comes from:

    [法]  《计算机软件著作权登记办法》 statute text
    [官]  China Copyright Protection Center published guidance
    [审]  industry practice, not published by an authority
    [推]  engineering inference

See docs/SPEC.md (appendix A/B) and docs/STANDARDS.md for the full evidence
trail. Changing any value here changes rendered pagination and therefore the
"页数" field on the application form, which is a hard consistency check.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

CM_PER_INCH = Decimal("2.54")
POINTS_PER_INCH = Decimal(72)


class LayoutViolation(Exception):
    """Raised when the configured layout cannot satisfy a hard requirement.

    This is deliberately an exception rather than a warning: a layout that
    cannot legally hold the required number of lines must never silently
    produce a document.
    """


@dataclass(frozen=True, slots=True)
class CodeLayout:
    """Layout for the source-code identifying material (程序鉴别材料).

    Authority notes:
      * page size and portrait orientation: [法] 第十七条 / [官] 申请须知
      * margins, font size, line height, header metrics: [推] — the statute
        says nothing; these reproduce the only combination that has been
        verified to fit 50 lines on A4 with a fixed 14pt line height.
      * header placement and page-number placement: [官] 问题问答
    """

    page_width_cm: Decimal = Decimal("21.0")
    page_height_cm: Decimal = Decimal("29.7")
    # The published template uses 2cm top and 1cm bottom. The bottom margin is
    # smaller than the top because the page number sits in the header, not the
    # footer, which leaves more room for the fifty lines.
    margin_top_cm: Decimal = Decimal("2.0")
    margin_bottom_cm: Decimal = Decimal("1.0")
    margin_left_cm: Decimal = Decimal("2.0")
    margin_right_cm: Decimal = Decimal("2.0")
    header_distance_cm: Decimal = Decimal("1.27")
    # SimSun at 10.5pt (小四/五号 in the template) with a fixed 15pt line.
    body_font_size_pt: Decimal = Decimal("10.5")
    body_line_pt: Decimal = Decimal("15.0")
    header_font_size_pt: Decimal = Decimal("9.0")
    header_line_pt: Decimal = Decimal("12.0")
    tab_width: int = 4
    orientation: str = "portrait"

    @property
    def page_width_pt(self) -> Decimal:
        return self.page_width_cm / CM_PER_INCH * POINTS_PER_INCH

    @property
    def page_height_pt(self) -> Decimal:
        return self.page_height_cm / CM_PER_INCH * POINTS_PER_INCH

    @property
    def body_width_pt(self) -> Decimal:
        return self.page_width_pt - self._cm_to_pt(self.margin_left_cm) - self._cm_to_pt(
            self.margin_right_cm
        )

    @property
    def body_height_pt(self) -> Decimal:
        return self.page_height_pt - self._cm_to_pt(self.margin_top_cm) - self._cm_to_pt(
            self.margin_bottom_cm
        )

    @staticmethod
    def _cm_to_pt(value: Decimal) -> Decimal:
        return value / CM_PER_INCH * POINTS_PER_INCH


# --- Code material hard rules -------------------------------------------------

CODE_LAYOUT = CodeLayout()

# [法] 第十条: "程序每页不少于50行". This is a floor, never a target.
# Raising it is a statute violation; lowering it is a statute violation too.
LINES_PER_PAGE = 50

# [法] 第十条: front 30 + back 30, or the whole thing when under 60 pages.
SPLIT_THRESHOLD_PAGES = 60
SEGMENT_PAGES = SPLIT_THRESHOLD_PAGES // 2

# [推] When exact 30+30 alignment is impossible without splitting a file, the
# result may fall short of thirty pages. Falling slightly over is worse — a
# reviewer counting pages sees a segment longer than the statute asks for — so
# the preferred band is 28..30 and the tolerated maximum is 31. Anything beyond
# that is refused rather than submitted with a caveat.
#
# The asymmetry matters in practice: projects with uniform file sizes only
# step in file-sized increments, and 31 is sometimes the closest reachable
# value. Reporting it honestly is better than refusing to produce material at
# all, but it must be visible.
FALLBACK_MIN_SEGMENT_PAGES = 28
FALLBACK_MAX_SEGMENT_PAGES = 30
FALLBACK_HARD_MAX_SEGMENT_PAGES = 31

# [审] Not statutory. A very short last page looks like padding.
LAST_PAGE_MIN_RATIO = Decimal("2") / Decimal("3")

# [推] Pathological lines are hard-wrapped before measuring so a single
# minified line cannot blow up the wrapping pass.
MAX_LINE_CHARS_HARD_CAP = 3000

# [推] Upper bound on how many lines of a page may contain CJK characters.
# Material that is mostly Chinese prose reads as a document rather than as a
# program, and reviewers expect the identifying material to look like source
# code. Zero disables the limit.
MAX_CJK_LINES_PER_PAGE = 10

# [官] 问题问答: page number sits at the top-right, plain digits.
PAGE_NUMBER_POSITION = "top-right"
PAGE_NUMBER_STYLE = "plain"

# --- Manual (document identifying material) -----------------------------------

MANUAL_LAYOUT = CodeLayout(
    margin_top_cm=Decimal("2.54"),
    margin_bottom_cm=Decimal("2.54"),
    margin_left_cm=Decimal("3.17"),
    margin_right_cm=Decimal("2.54"),
    body_font_size_pt=Decimal("10.5"),
    body_line_pt=Decimal("10.5"),
)

# [法] 第十条: "文档每页不少于30行".
MANUAL_MIN_LINES_PER_PAGE = 30

# [官] Required chapters, fixed order. FAQ and glossary are always last.
MANUAL_REQUIRED_CHAPTERS: tuple[str, ...] = (
    "说明",
    "功能特点",
    "系统要求",
    "各功能页面/模块",
    "常见问题解答",
    "术语表",
)

# [审] Body length guidance for module chapters.
MANUAL_MODULE_CHARS = (120, 220)
MANUAL_MODULE_MAX_CHARS = 400

# [推] Images must never exceed one page.
IMAGE_MAX_WIDTH_IN = Decimal("5.8")
IMAGE_MAX_HEIGHT_IN = Decimal("8.8")

# --- Application form field constraints ---------------------------------------

# [审] These limits come from a single third-party source; the live form is
# behind real-name authentication and could not be verified directly.
FIELD_MAX: dict[str, int] = {
    "开发的硬件环境": 50,
    "运行的硬件环境": 50,
    "开发该软件的操作系统": 50,
    "软件开发环境/开发工具": 50,
    "运行平台/操作系统": 50,
    "运行支撑环境/支持软件": 50,
    "开发目的": 50,
    "面向领域/行业": 50,
    "编程语言": 120,
    "软件的技术特点": 100,
}

FIELD_RANGE: dict[str, tuple[int, int]] = {
    "软件的主要功能": (500, 1300),
}

# [官] 填表说明: 自然人、法人、非法人组织或其他
OWNER_CATEGORIES: tuple[str, ...] = ("自然人", "法人", "非法人组织", "其他")


def _to_decimal(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def assert_layout_invariants(
    layout: CodeLayout = CODE_LAYOUT,
    lines_per_page: int = LINES_PER_PAGE,
) -> None:
    """Verify every geometric invariant that must hold before rendering.

    Raises LayoutViolation instead of degrading. Callers render nothing on
    failure: a document whose pagination is not provably correct cannot be
    submitted, and producing one anyway is worse than producing none.
    """
    if lines_per_page < 50:
        raise LayoutViolation(
            f"[法] 第十条 requires at least 50 lines per page, got {lines_per_page}"
        )

    line_pt = _to_decimal(layout.body_line_pt)
    needed = line_pt * lines_per_page
    available = layout.body_height_pt
    if needed > available:
        raise LayoutViolation(
            f"{lines_per_page} lines x {line_pt}pt = {needed}pt exceeds the "
            f"body area of {available:.1f}pt on {layout.page_height_cm}cm paper "
            f"with {layout.margin_top_cm}+{layout.margin_bottom_cm}cm margins. "
            "Reduce lines_per_page or the line height; do not shrink margins."
        )

    if layout.page_width_cm != Decimal("21.0") or layout.page_height_cm != Decimal("29.7"):
        raise LayoutViolation(
            f"[法] 第十七条 requires A4 297mm x 210mm, got "
            f"{layout.page_width_cm}cm x {layout.page_height_cm}cm"
        )

    if layout.orientation != "portrait":
        raise LayoutViolation("[官] 申请须知 requires portrait layout")

    if layout.body_width_pt <= 0 or layout.body_height_pt <= 0:
        raise LayoutViolation("margins leave no usable body area")


def max_lines_per_page(layout: CodeLayout = CODE_LAYOUT) -> int:
    """How many lines physically fit, given the fixed line height."""
    return int(layout.body_height_pt // _to_decimal(layout.body_line_pt))
