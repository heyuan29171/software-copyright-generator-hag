"""Selection produces two thirty-page excerpts, each drawn independently.

The old implementation took the last thirty pages of the file list. Once files
were ordered by quality that meant the back segment came from the worst files
in the project, and the second half of the document averaged eighteen Chinese
lines a page while the first half was clean. These tests pin the corrected
behaviour: both segments select on their own merits and never overlap.
"""

from __future__ import annotations

import pytest

from hag.layout import LINES_PER_PAGE, SEGMENT_PAGES
from hag.pagination import paginate
from hag.selection import (
    FileSpan,
    SelectionError,
    SelectionMode,
    material_slices,
    select_material,
)


def spans(*counts: int, cuts: bool = True, cjk: float = 0.0) -> list[FileSpan]:
    out = []
    for i, c in enumerate(counts):
        safe = tuple(range(1, c)) if cuts else ()
        out.append(FileSpan(f"f{i}.py", c, safe_cuts=safe, cjk_ratio=cjk))
    return out


class TestAllMode:
    def test_short_material_is_submitted_whole(self) -> None:
        selection = select_material(spans(100, 100))
        assert selection.mode is SelectionMode.ALL
        assert selection.rendered_pages == 4

    def test_title_states_true_page_count(self) -> None:
        assert select_material(spans(100)).title == "代码(全部2页)"

    def test_under_threshold_with_unselected_files_raises(self) -> None:
        with pytest.raises(SelectionError, match="not selected"):
            select_material(spans(100), unselected_candidate_count=3)

    def test_exactly_59_pages_allowed_when_all_selected(self) -> None:
        selection = select_material(spans(59 * LINES_PER_PAGE))
        assert selection.rendered_pages == 59


class TestTwoExcerpts:
    def test_produces_sixty_pages(self) -> None:
        selection = select_material(spans(*([50] * 100)))
        assert selection.rendered_pages == 60

    def test_each_segment_is_thirty_pages(self) -> None:
        selection = select_material(spans(*([50] * 100)))
        assert selection.front_pages == 30
        assert selection.back_pages == 30

    def test_segments_do_not_overlap(self) -> None:
        """No line may appear in both excerpts."""
        selection = select_material(spans(*([50] * 100)))
        ranges = sorted(material_slices(selection))
        for (a_start, a_end), (b_start, b_end) in zip(ranges, ranges[1:]):
            assert a_end <= b_start, f"{a_start}-{a_end} overlaps {b_start}-{b_end}"

    def test_segments_cover_exactly_sixty_pages_of_lines(self) -> None:
        selection = select_material(spans(*([50] * 100)))
        covered = sum(end - start for start, end in material_slices(selection))
        assert covered == 60 * LINES_PER_PAGE

    def test_no_file_contributes_to_both_segments(self) -> None:
        selection = select_material(spans(*([50] * 100)))
        front_files = {p[0] for p in selection.front.pieces}
        back_files = {p[0] for p in selection.back.pieces}
        assert not (front_files & back_files)

    def test_title_is_exact(self) -> None:
        selection = select_material(spans(*([50] * 100)))
        assert selection.title == "代码(前30页+后30页)"


class TestBothSegmentsPreferCleanFiles:
    """The bug this replaces: only the front segment avoided Chinese."""

    def test_back_segment_does_not_come_from_dirty_files(self) -> None:
        clean = [(300, 0.0)] * 10
        dirty = [(300, 0.9)] * 10
        files = [FileSpan(f"c{i}.py", n, tuple(range(1, n)), r) for i, (n, r) in enumerate(clean)]
        files += [FileSpan(f"d{i}.py", n, tuple(range(1, n)), r) for i, (n, r) in enumerate(dirty)]
        files.sort(key=lambda f: f.cjk_ratio)

        selection = select_material(files)
        front_files = {p[0] for p in selection.front.pieces}
        back_files = {p[0] for p in selection.back.pieces}
        assert max(front_files) <= 9, "front segment reached into dirty files"
        assert max(back_files) <= 9, "back segment reached into dirty files"

    def test_ordered_files_keep_quality_order(self) -> None:
        files = spans(300, 300, 300, 300, 300)
        selection = select_material(files)
        assert [f.rel_path for f in selection.ordered_files] == [
            f"f{i}.py" for i in range(5)
        ]


class TestGuards:
    def test_empty_selection_raises(self) -> None:
        with pytest.raises(SelectionError, match="no source lines"):
            select_material([])

    def test_all_zero_line_files_raise(self) -> None:
        with pytest.raises(SelectionError):
            select_material(spans(0, 0))

    def test_too_little_source_raises(self) -> None:
        """Two files of 2000 lines are 80 pages: enough for sixty, but a
        segment cannot end on 1500 lines without a cut inside a file."""
        with pytest.raises(SelectionError):
            select_material(spans(2000, 2000, cuts=False))

    def test_zero_line_files_ignored(self) -> None:
        selection = select_material(spans(100, 0, 100))
        assert all(f.line_count > 0 for f in selection.ordered_files)


class TestSlices:
    def test_matches_rendered_pages(self) -> None:
        selection = select_material(spans(*([50] * 100)))
        covered = sum(end - start for start, end in material_slices(selection))
        assert len(paginate(["x"] * covered, selection.lines_per_page)) == (
            selection.rendered_pages
        )

    def test_discontinuous_is_allowed(self) -> None:
        """The statute permits a gap between the front and back excerpts, and
        the back segment skips the files the front already used."""
        selection = select_material(spans(*([50] * 100)))
        front_files = {p[0] for p in selection.front.pieces}
        back_files = {p[0] for p in selection.back.pieces}
        assert min(back_files) > min(front_files)
        assert not (front_files & back_files)

    def test_offsets_are_within_the_stream(self) -> None:
        selection = select_material(spans(*([50] * 100)))
        total = sum(f.line_count for f in selection.ordered_files)
        for start, end in material_slices(selection):
            assert 0 <= start <= end <= total


class TestPartialFileTrimming:
    def test_large_single_file_is_trimmed_at_a_cut(self) -> None:
        """A 100-page file cannot be used whole; it must be sliced."""
        selection = select_material(spans(5000, 5000))
        assert selection.rendered_pages == 60

    def test_trimmed_segment_starts_at_a_safe_point(self) -> None:
        files = [
            FileSpan("a.py", 2000, safe_cuts=tuple(range(1, 2000))),
            FileSpan("b.py", 2000, safe_cuts=tuple(range(1, 2000))),
        ]
        selection = select_material(files)
        assert selection.front is not None
        assert selection.back is not None
        assert selection.front.line_count == 1500
        assert selection.back.line_count == 1500

    def test_without_cuts_whole_files_only(self) -> None:
        """With no safe cut inside a file, a segment cannot be exactly thirty
        pages unless a file boundary happens to fall there."""
        with pytest.raises(SelectionError):
            select_material(spans(2000, 2000, cuts=False))
