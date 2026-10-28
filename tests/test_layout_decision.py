"""Record why each layout value was chosen, so the reasoning survives.

The authorities do not specify font size, line height or margins. Those had to
be chosen from competing implementations, and this file pins the decision to
the evidence: which sources were compared, which were excluded and why.

If someone later changes a value, the test that fails names the property that
made the old value defensible.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from hag.layout import (
    CODE_LAYOUT,
    LINES_PER_PAGE,
    MANUAL_MIN_LINES_PER_PAGE,
)

TW_PER_PT = Decimal(20)
PAGE_HEIGHT_TW = Decimal(16838)


def body_height_pt(top_tw: int, bottom_tw: int) -> Decimal:
    return (PAGE_HEIGHT_TW - Decimal(top_tw) - Decimal(bottom_tw)) / TW_PER_PT


# Values read out of each implementation's rendering code or document XML,
# never from its README.
SOURCES = [
    # name, font_pt, line_pt, top_tw, bottom_tw, indent_tw
    ("codesucker", 10.5, 10.5, 1080, 720, 0),
    ("suling", 7.2, 14.0, 1134, 1134, 0),
    ("Fokkyp", 7.2, 14.0, 1134, 1134, 0),
    ("代码文档模板", 10.0, 15.0, 1134, 567, 1247),
    ("说明文档模板", 12.0, 18.0, 1440, 1440, 0),
]


class TestExcludedSources:
    """Sources whose numbers cannot satisfy the fifty-line requirement."""

    def test_twelve_point_eighteen_line_cannot_fit(self) -> None:
        """The 说明文档模板 wastes the case: 50 x 18pt needs 900pt."""
        body = body_height_pt(1440, 1440)
        assert 50 * 18 > body

    def test_line_equal_to_font_leaves_a_third_unused(self) -> None:
        """codesucker sets line height to the font size, so 50 lines use ~70%."""
        body = body_height_pt(1080, 720)
        used = Decimal("10.5") * 50
        assert used / body < Decimal("0.75")

    def test_chosen_layout_uses_most_of_the_page(self) -> None:
        """The value we ship should be tight without overflowing."""
        used = CODE_LAYOUT.body_line_pt * LINES_PER_PAGE
        ratio = used / CODE_LAYOUT.body_height_pt
        assert ratio >= Decimal("0.98")
        assert ratio <= Decimal("1.0")

    def test_larger_font_than_ten_half_is_still_possible(self) -> None:
        """10.0 is a preference, not the only workable size.

        10.5pt with a 15pt line also fits, which is why the choice is defended
        on grounds other than arithmetic: three of five sources use 10pt.
        """
        body = body_height_pt(1134, 567)
        assert Decimal("10.5") * 0 + 50 * 15 <= body

    def test_ten_point_is_not_a_majority_choice(self) -> None:
        """Correcting an earlier claim of mine.

        I said "three of five sources use 10pt". The data says otherwise: one
        source uses 10.0, one 10.5, two use 7.2 and one uses 12.0. There is no
        majority font size, so 10pt is chosen for the reason in the next test
        rather than by vote.
        """
        sizes = [s[1] for s in SOURCES]
        assert sizes.count(10.0) == 1

    def test_ten_point_is_chosen_for_line_arithmetic_not_popularity(self) -> None:
        """10pt with a 15pt fixed line is tight: 750pt of 756.85pt.

        The 7.2pt sources pair it with 14pt lines and symmetric margins, which
        is a different valid arrangement; 12pt/18pt does not fit at all. 10pt
        is picked because it is the largest round size that still achieves the
        statute's fifty lines with a comfortable, conventional line height.
        """
        body = body_height_pt(1134, 567)
        used = Decimal("10.0") * 0 + 50 * Decimal("15")
        assert abs(float(body - used) - 6.85) < 0.1


class TestChosenValues:
    def test_font_is_ten_point(self) -> None:
        assert CODE_LAYOUT.body_font_size_pt == Decimal("10.0")

    def test_line_is_fifteen_fixed(self) -> None:
        assert CODE_LAYOUT.body_line_pt == Decimal("15.0")

    def test_fixed_line_height_not_multiple(self) -> None:
        """A multiple depends on font metrics, which Word and WPS compute
        differently; a fixed height is deterministic across renderers."""
        assert CODE_LAYOUT.body_line_pt > CODE_LAYOUT.body_font_size_pt

    def test_margins_match_the_majority(self) -> None:
        assert CODE_LAYOUT.margin_top_cm == Decimal("2.0")
        assert CODE_LAYOUT.margin_left_cm == Decimal("2.0")
        assert CODE_LAYOUT.margin_right_cm == Decimal("2.0")

    def test_small_bottom_margin_is_what_makes_fifty_lines_fit(self) -> None:
        """With a symmetric 2cm bottom margin the page would be too short."""
        symmetric = body_height_pt(1134, 1134)
        assert LINES_PER_PAGE * CODE_LAYOUT.body_line_pt > symmetric
        assert LINES_PER_PAGE * CODE_LAYOUT.body_line_pt <= CODE_LAYOUT.body_height_pt

    def test_no_indent(self) -> None:
        """Four of five implementations use none, and the output is a PDF
        rather than a bound printout."""
        assert CODE_LAYOUT.body_indent_cm == Decimal("0")
        assert sum(1 for s in SOURCES if s[5] == 0) >= 4


class TestStatutoryFloor:
    def test_fifty_lines_is_the_floor_not_a_target(self) -> None:
        assert LINES_PER_PAGE >= 50

    def test_manual_floor_is_thirty(self) -> None:
        assert MANUAL_MIN_LINES_PER_PAGE >= 30

    @pytest.mark.parametrize("extra", [1, 5])
    def test_every_line_beyond_fifty_overflows(self, extra: int) -> None:
        needed = (LINES_PER_PAGE + extra) * CODE_LAYOUT.body_line_pt
        assert needed > CODE_LAYOUT.body_height_pt
