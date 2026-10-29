"""Regressions for the tolerance and overshoot rules.

Two failures shaped these:

  * demanding a segment of exactly 1500 lines made a real project fail on
    5,611 usable lines, because its files offered cuts every three lines and
    1500 is not a multiple of three;
  * loosening that freely produced a 64-page document, breaching the
    thirty-pages-per-segment rule in the other direction.
"""

from __future__ import annotations

import pytest

from hag.layout import LINES_PER_PAGE
from hag.selection import FileSpan, SelectionError, select_material


def spans(*counts: int, cuts: bool = True) -> list[FileSpan]:
    out = []
    for i, c in enumerate(counts):
        safe = tuple(range(1, c)) if cuts else ()
        out.append(FileSpan(f"f{i}.py", c, safe_cuts=safe))
    return out


class TestSlightShortfallIsAccepted:
    def test_segment_just_under_a_full_page_is_usable(self) -> None:
        """One line short of a full page is not a reason to produce nothing.

        Cuts land on multiples of seven, so no segment can hit exactly 1500;
        the closest reachable total must still be accepted.
        """
        files = [
            FileSpan(f"f{i}.py", 2000, safe_cuts=tuple(range(7, 2000, 7)))
            for i in range(5)
        ]
        selection = select_material(files)
        assert selection.front_pages >= 28
        assert selection.back_pages >= 28

    def test_twenty_eight_pages_is_the_floor(self) -> None:
        """When even 28 pages per segment cannot be reached, refuse.

        3,000 lines total is sixty pages, but a single 3,000-line file offers
        only its own end as a cut point, so no segment can be built.
        """
        files = [FileSpan("huge.py", 3000)]
        with pytest.raises(SelectionError):
            select_material(files)


class TestOvershootIsBounded:
    def test_overshoot_never_breaks_thirty_pages(self) -> None:
        """A segment may reach 30 pages, never 31 or more."""
        files = [FileSpan(f"f{i}.py", 800) for i in range(5)]
        try:
            selection = select_material(files)
        except SelectionError:
            return
        assert selection.front.line_count <= 30 * LINES_PER_PAGE
        assert selection.back.line_count <= 30 * LINES_PER_PAGE

    def test_whole_files_without_cuts_do_not_produce_sixty_four_pages(self) -> None:
        """5 x 800 = 80 pages: taking two whole files per segment gives 64,
        which breaches the ceiling. The run must refuse instead."""
        files = [FileSpan(f"f{i}.py", 800) for i in range(5)]
        with pytest.raises(SelectionError):
            select_material(files)

    def test_every_seventh_line_cut_still_produces_material(self) -> None:
        """Cuts every three lines cannot hit 1500 exactly; the result must
        still be produced rather than refused."""
        files = [
            FileSpan(f"f{i}.py", 181, safe_cuts=tuple(range(1, 181, 3)))
            for i in range(60)
        ]
        selection = select_material(files)
        assert selection.rendered_pages >= 56
        assert selection.front_pages >= 28


class TestReportedCountsAreTrue:
    def test_front_and_back_reported_accurately(self) -> None:
        files = [FileSpan(f"f{i}.py", 181, safe_cuts=tuple(range(1, 181, 3))) for i in range(60)]
        selection = select_material(files)
        front_total = sum(end - start for _, start, end in selection.front.pieces)
        back_total = sum(end - start for _, start, end in selection.back.pieces)
        assert selection.front.line_count == front_total
        assert selection.back.line_count == back_total

    def test_segments_stay_distinct(self) -> None:
        files = [FileSpan(f"f{i}.py", 181, safe_cuts=tuple(range(1, 181, 3))) for i in range(60)]
        selection = select_material(files)
        front = {p[0] for p in selection.front.pieces}
        back = {p[0] for p in selection.back.pieces}
        assert not (front & back)
