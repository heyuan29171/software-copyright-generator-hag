"""Layout constants must satisfy the statute and the published guidance.

These tests encode the authority trail documented in docs/STANDARDS.md.
If someone changes a constant in a way that breaks a hard requirement, the
failure message should say which rule was violated.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from hag.layout import (
    CODE_LAYOUT,
    FIELD_MAX,
    FIELD_RANGE,
    LINES_PER_PAGE,
    MANUAL_MIN_LINES_PER_PAGE,
    MANUAL_REQUIRED_CHAPTERS,
    SEGMENT_PAGES,
    SPLIT_THRESHOLD_PAGES,
    LayoutViolation,
    assert_layout_invariants,
    max_lines_per_page,
)


class TestPageGeometry:
    def test_page_is_a4(self) -> None:
        """[法] 第十七条: A4 297mm x 210mm."""
        assert CODE_LAYOUT.page_width_cm == Decimal("21.0")
        assert CODE_LAYOUT.page_height_cm == Decimal("29.7")

    def test_portrait_orientation(self) -> None:
        """[官] 申请须知: 申请文件应纵向排版."""
        assert CODE_LAYOUT.orientation == "portrait"

    def test_page_height_in_points(self) -> None:
        # 29.7cm / 2.54 * 72
        assert float(CODE_LAYOUT.page_height_pt) == pytest.approx(841.9, abs=0.1)

    def test_body_area(self) -> None:
        # Width: 21 - 2.0 - 2.0 = 17cm. Height: 29.7 - 2.0 - 1.0 = 26.7cm.
        # The bottom margin is smaller because the page number lives in the
        # header, which is what makes 50 lines at 15pt fit.
        assert float(CODE_LAYOUT.body_width_pt) == pytest.approx(481.9, abs=0.2)
        assert float(CODE_LAYOUT.body_height_pt) == pytest.approx(756.9, abs=0.2)


class TestLineCount:
    def test_statutory_floor(self) -> None:
        """[法] 第十条: 程序每页不少于50行."""
        assert LINES_PER_PAGE >= 50

    def test_fifty_lines_fit(self) -> None:
        """The published template combination must fit exactly 50 lines."""
        assert 50 * CODE_LAYOUT.body_line_pt <= CODE_LAYOUT.body_height_pt

    def test_max_lines_is_exactly_fifty(self) -> None:
        """50 is not a comfortable fit; it is what the geometry allows.

        756.9pt of body area divided by a 15pt line leaves room for 50 lines
        with 6.9pt to spare. The old 7.2pt/14pt combination allowed 52, which
        is why the template's own numbers matter: they leave no slack for a
        renderer to drift.
        """
        assert max_lines_per_page() == 50

    def test_fifty_five_lines_do_not_fit(self) -> None:
        """Guards against the upstream 55-lines-per-page mistake."""
        assert 55 * CODE_LAYOUT.body_line_pt > CODE_LAYOUT.body_height_pt

    def test_body_area_exists_because_bottom_margin_is_smaller(self) -> None:
        """With symmetric 2cm margins, 50 lines at 15pt would not fit."""
        from decimal import Decimal

        symmetric_height = (
            CODE_LAYOUT.page_height_cm - Decimal("2.0") - Decimal("2.0")
        ) / Decimal("2.54") * Decimal(72)
        assert 50 * CODE_LAYOUT.body_line_pt > symmetric_height


class TestSplitRules:
    def test_threshold_and_segment(self) -> None:
        """[法] 第十条: front 30 + back 30, whole document under 60."""
        assert SPLIT_THRESHOLD_PAGES == 60
        assert SEGMENT_PAGES == 30


class TestInvariants:
    def test_default_layout_is_valid(self) -> None:
        assert_layout_invariants()

    def test_rejects_fewer_than_fifty_lines(self) -> None:
        with pytest.raises(LayoutViolation, match="at least 50 lines"):
            assert_layout_invariants(lines_per_page=49)

    def test_rejects_fifty_five_lines(self) -> None:
        with pytest.raises(LayoutViolation, match="exceeds the body area"):
            assert_layout_invariants(lines_per_page=55)

    def test_rejects_wrong_paper_size(self) -> None:
        bad = type(CODE_LAYOUT)(page_width_cm=20.0)
        with pytest.raises(LayoutViolation, match="A4"):
            assert_layout_invariants(layout=bad)

    def test_rejects_landscape(self) -> None:
        bad = type(CODE_LAYOUT)(orientation="landscape")
        with pytest.raises(LayoutViolation, match="portrait"):
            assert_layout_invariants(layout=bad)

    def test_margins_leave_usable_area(self) -> None:
        assert CODE_LAYOUT.body_width_pt > 0
        assert CODE_LAYOUT.body_height_pt > 0


class TestManualRules:
    def test_manual_min_lines(self) -> None:
        """[法] 第十条: 文档每页不少于30行."""
        assert MANUAL_MIN_LINES_PER_PAGE >= 30

    def test_required_chapters_order(self) -> None:
        """[官/审] FAQ and glossary are always the final two chapters."""
        assert MANUAL_REQUIRED_CHAPTERS[-1] == "术语表"
        assert MANUAL_REQUIRED_CHAPTERS[-2] == "常见问题解答"
        assert MANUAL_REQUIRED_CHAPTERS[0] == "说明"

    def test_manual_has_six_chapters(self) -> None:
        assert len(MANUAL_REQUIRED_CHAPTERS) == 6


class TestApplicationFieldLimits:
    @pytest.mark.parametrize(
        ("field", "limit"),
        [
            ("开发的硬件环境", 50),
            ("运行平台/操作系统", 50),
            ("开发目的", 50),
            ("面向领域/行业", 50),
            ("编程语言", 120),
            ("软件的技术特点", 100),
        ],
    )
    def test_single_bounds(self, field: str, limit: int) -> None:
        assert FIELD_MAX[field] == limit

    def test_main_functions_range(self) -> None:
        low, high = FIELD_RANGE["软件的主要功能"]
        assert low == 500
        assert high == 1300

    def test_no_overlapping_field_definitions(self) -> None:
        assert not set(FIELD_MAX) & set(FIELD_RANGE)
