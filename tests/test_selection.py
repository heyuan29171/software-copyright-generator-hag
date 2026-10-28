"""Splitting must respect file boundaries and never overstate the page counts.

The failure these tests exist to prevent is a document named
"front 30 + back 30" that actually contains 59 pages, which draws a
correction notice.
"""

from __future__ import annotations

import pytest

from hag.layout import (
    FALLBACK_MAX_SEGMENT_PAGES,
    FALLBACK_MIN_SEGMENT_PAGES,
    LINES_PER_PAGE,
    LayoutViolation,
)
from hag.selection import (
    FileSpan,
    SelectionError,
    SelectionMode,
    material_slices,
    select_material,
)


def spans(*counts: int) -> list[FileSpan]:
    return [FileSpan(rel_path=f"f{i}.py", line_count=c) for i, c in enumerate(counts)]


class TestAllMode:
    def test_short_material_is_submitted_whole(self) -> None:
        selection = select_material(spans(100, 100))
        assert selection.mode is SelectionMode.ALL
        assert selection.rendered_pages == 4
        assert selection.front_pages == 4
        assert selection.back_pages == 0

    def test_title_states_true_page_count(self) -> None:
        selection = select_material(spans(100))
        assert selection.title == "代码(全部2页)"

    def test_under_threshold_with_unselected_files_raises(self) -> None:
        with pytest.raises(SelectionError, match="not selected"):
            select_material(spans(100), unselected_candidate_count=3)

    def test_exactly_59_pages_allowed_when_all_selected(self) -> None:
        selection = select_material(spans(59 * LINES_PER_PAGE))
        assert selection.mode is SelectionMode.ALL
        assert selection.rendered_pages == 59


class TestExactFrontBack:
    def test_two_files_of_thirty_pages(self) -> None:
        # 30 pages + 30 pages, plus a middle file so it is a real split.
        selection = select_material(
            spans(30 * LINES_PER_PAGE, 5, 30 * LINES_PER_PAGE)
        )
        assert selection.mode is SelectionMode.FRONT_BACK
        assert selection.front_pages == 30
        assert selection.back_pages == 30
        assert selection.exact is True
        assert selection.title == "代码(前30页+后30页)"

    def test_many_equal_files(self) -> None:
        # 120 files x 25 lines = 3000 lines = 60 pages exactly.
        selection = select_material(spans(*([25] * 120)))
        assert selection.mode is SelectionMode.FRONT_BACK
        assert selection.rendered_pages == 60

    def test_cut_lands_on_file_boundary(self) -> None:
        files = spans(*([50] * 100))
        selection = select_material(files)
        assert selection.front_end_line % LINES_PER_PAGE == 0

    def test_back_start_after_front_end(self) -> None:
        selection = select_material(spans(*([50] * 100)))
        assert selection.back_start_line > selection.front_end_line


class TestFallback:
    def test_tolerated_deviation_reports_truth(self) -> None:
        # 29 pages then a one-file bridge then 30 pages: an exact 30/30 cut is
        # impossible because the bridge cannot be split, but 29+31 is inside
        # the tolerated band.
        selection = select_material(spans(29 * LINES_PER_PAGE, 10, 31 * LINES_PER_PAGE))
        assert selection.mode is SelectionMode.FRONT_BACK
        if selection.exact:
            pytest.skip("arrangement happened to be exact")
        assert FALLBACK_MIN_SEGMENT_PAGES <= selection.front_pages <= FALLBACK_MAX_SEGMENT_PAGES
        assert FALLBACK_MIN_SEGMENT_PAGES <= selection.back_pages <= FALLBACK_MAX_SEGMENT_PAGES
        assert selection.warnings

    def test_title_states_real_total_when_inexact(self) -> None:
        selection = select_material(spans(29 * LINES_PER_PAGE, 10, 31 * LINES_PER_PAGE))
        if selection.exact:
            pytest.skip("arrangement happened to be exact")
        # Only an exact 30+30 may claim 30+30; anything else states the total,
        # because the two segment counts do not add up to the real page count.
        assert selection.title == f"代码(共{selection.rendered_pages}页)"
        assert "前30页+后30页" not in selection.title

    def test_out_of_band_raises(self) -> None:
        # One enormous file makes a boundary-aligned 30/30 split impossible.
        with pytest.raises(SelectionError, match="tolerance"):
            select_material(spans(40 * LINES_PER_PAGE, 1000 * LINES_PER_PAGE))


class TestGuards:
    def test_empty_selection_raises(self) -> None:
        with pytest.raises(SelectionError, match="no source lines"):
            select_material([])

    def test_all_zero_line_files_raise(self) -> None:
        with pytest.raises(SelectionError):
            select_material(spans(0, 0))

    def test_rejects_invalid_lines_per_page(self) -> None:
        with pytest.raises(LayoutViolation):
            select_material(spans(100), lines_per_page=0)

    def test_zero_line_files_are_ignored(self) -> None:
        selection = select_material(spans(100, 0, 100))
        assert all(f.line_count > 0 for f in selection.ordered_files)


class TestSlices:
    def test_all_mode_single_slice(self) -> None:
        selection = select_material(spans(100))
        assert material_slices(selection) == ((0, selection.total_lines),)

    def test_front_back_two_discontinuous_slices(self) -> None:
        selection = select_material(spans(*([50] * 100)))
        slices = material_slices(selection)
        assert len(slices) == 2
        assert slices[0][0] == 0
        assert slices[1][1] == selection.total_lines
        # Discontinuity between the segments is permitted by the statute.
        assert slices[1][0] > slices[0][1]

    def test_slices_cover_requested_pages(self) -> None:
        selection = select_material(spans(*([50] * 100)))
        covered = sum(end - start for start, end in material_slices(selection))
        assert covered == selection.rendered_lines

    def test_rendered_pages_is_not_the_sum_of_segments(self) -> None:
        """Concatenating and re-paginating can merge a partial page away.

        A front segment ending 14 lines into its last page leaves room for the
        back segment's first lines. Summing the segment page counts would
        report one page too many, and that number goes on the application form.
        """
        selection = select_material(spans(1464, 1000, 1354))
        slices = material_slices(selection)
        covered = sum(end - start for start, end in slices)
        expected = (covered + selection.lines_per_page - 1) // selection.lines_per_page
        assert selection.rendered_pages == expected

    def test_rendered_pages_matches_actual_pagination(self) -> None:
        from hag.pagination import paginate

        selection = select_material(spans(1464, 1000, 1354))
        total = sum(end - start for start, end in material_slices(selection))
        pages = paginate(["x"] * total, selection.lines_per_page)
        assert len(pages) == selection.rendered_pages


class TestNoFileIsSplit:
    def test_ordered_files_match_input_set(self) -> None:
        files = spans(*([50] * 100))
        selection = select_material(files)
        assert {f.rel_path for f in selection.ordered_files} == {f.rel_path for f in files}

    def test_reordering_does_not_change_total_lines(self) -> None:
        # Mixed sizes that still admit a boundary-aligned split.
        files = spans(700, 900, 1200, 300, 450, 350, 400)
        selection = select_material(files)
        assert selection.total_lines == sum(f.line_count for f in files)

    def test_split_point_falls_between_files(self) -> None:
        files = spans(*([50] * 100))
        selection = select_material(files)
        prefix = 0
        boundaries = set()
        for f in selection.ordered_files:
            prefix += f.line_count
            boundaries.add(prefix)
        assert selection.front_end_line in boundaries
        assert selection.back_start_line - 1 in boundaries
