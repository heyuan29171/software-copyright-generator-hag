"""Selecting which pages become the identifying material.

The statute asks for the first 30 and the last 30 pages. The hard part is
that a split must never fall inside a source file, so the page boundaries and
the file boundaries have to agree.

Strategy, in order:

1. If the material is under 60 pages, submit all of it. If candidate files
   remain unselected, stop: the project might reach 60 pages and silently
   submitting 41 would be wrong.
2. Otherwise, try to reorder the selected files so the front segment ends
   exactly on a file boundary at page 30 and the back segment begins exactly
   on a file boundary at page 31. Reordering changes only the order files are
   presented in, never which files are included.
3. If exact alignment is impossible, accept a result inside a small tolerance
   band but **report the true page counts**, so the filename cannot claim
   30+30 when it is really 28+31.
4. Outside the band, stop and ask for a different selection.

Reporting the truth matters more than hitting the number: a file named
"front 30 + back 30" that is really 59 pages is a correction notice waiting
to happen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .layout import (
    FALLBACK_HARD_MAX_SEGMENT_PAGES,
    FALLBACK_MAX_SEGMENT_PAGES,
    FALLBACK_MIN_SEGMENT_PAGES,
    LINES_PER_PAGE,
    SEGMENT_PAGES,
    SPLIT_THRESHOLD_PAGES,
    LayoutViolation,
)


class SelectionMode(str, Enum):
    """Which shape the material took."""

    ALL = "all"
    """Material is shorter than 60 pages; everything is submitted."""

    FRONT_BACK = "front_back"
    """Front and back segments, page 30 to 31 may be discontinuous."""


@dataclass(frozen=True, slots=True)
class FileSpan:
    """One source file's contribution to the material."""

    rel_path: str
    line_count: int
    safe_cuts: tuple[int, ...] = ()
    """Line offsets inside this file where a segment may be cut.

    The statute asks for "thirty continuous pages", not "thirty pages ending on
    a file boundary". Insisting on file boundaries is a convention copied from
    other tools, and it fails badly on real projects: one 60-page Vue component
    makes an exact split arithmetically impossible, which produced a 59-page
    document with a 31-page segment.

    So splits may land inside a file, but only at a point that does not break a
    syntactic unit. The pipeline supplies those offsets (blank lines and
    top-level boundaries); when it supplies none, only the file end is eligible.
    """

    def __post_init__(self) -> None:
        if self.line_count < 0:
            raise ValueError(f"line_count must be non-negative, got {self.line_count}")

    def cut_candidates(self) -> tuple[int, ...]:
        """Offsets at which this file may end a segment, file end included."""
        points = {c for c in self.safe_cuts if 0 < c < self.line_count}
        points.add(self.line_count)
        return tuple(sorted(points))


@dataclass(frozen=True, slots=True)
class Selection:
    """The chosen ordering and the resulting page layout."""

    mode: SelectionMode
    ordered_files: tuple[FileSpan, ...]
    lines_per_page: int
    total_lines: int
    total_pages: int

    front_pages: int = 0
    back_pages: int = 0
    front_end_line: int = 0
    """Last material line belonging to the front segment (1-based, inclusive)."""

    back_start_line: int = 0
    """First material line belonging to the back segment (1-based, inclusive)."""

    exact: bool = True
    """True when the segments came out at exactly SEGMENT_PAGES each."""

    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def rendered_pages(self) -> int:
        """Pages actually produced, which is what the form must report.

        The front and back segments are concatenated and then re-paginated, so
        the total is not the sum of the two segments' page counts. A front
        segment ending 14 lines into its last page leaves room for the first
        14 lines of the back segment on that same page. Adding the two counts
        would over-report by one page, and that number goes straight onto the
        application form.
        """
        if self.mode is SelectionMode.ALL:
            return self.total_pages
        covered = self.front_end_line + (self.total_lines - self.back_start_line + 1)
        return (covered + self.lines_per_page - 1) // self.lines_per_page

    @property
    def rendered_lines(self) -> int:
        """Material lines that will actually be printed."""
        if self.mode is SelectionMode.ALL:
            return self.total_lines
        return self.front_end_line + (self.total_lines - self.back_start_line + 1)

    @property
    def title(self) -> str:
        """Filename-safe description that never misstates the page counts.

        Only an exact 30+30 result may be described as such. Anything else
        states the total actually rendered, because the front and back segment
        page counts do not add up to the real page count once the segments are
        concatenated and re-paginated.
        """
        if self.mode is SelectionMode.ALL:
            return f"代码(全部{self.rendered_pages}页)"
        if self.exact and self.front_pages == SEGMENT_PAGES and self.back_pages == SEGMENT_PAGES:
            return f"代码(前{SEGMENT_PAGES}页+后{SEGMENT_PAGES}页)"
        return f"代码(共{self.rendered_pages}页)"


class SelectionError(Exception):
    """Raised when no valid material can be produced from the given files."""


def _pages_for(lines: int, lines_per_page: int) -> int:
    return (lines + lines_per_page - 1) // lines_per_page


def _prefix_counts(counts: list[int]) -> list[int]:
    """Running totals, so any prefix line count is a single lookup."""
    out = [0]
    for value in counts:
        out.append(out[-1] + value)
    return out


def _find_cut_prefix(
    counts: list[int], target: int, lines_per_page: int
) -> int | None:
    """Index `k` such that the first k files occupy exactly `target` pages.

    Returns the largest such k, or None when no prefix lands on the target.
    """
    prefix = _prefix_counts(counts)
    best: int | None = None
    for k in range(1, len(counts) + 1):
        if prefix[k] == 0:
            continue
        if _pages_for(prefix[k], lines_per_page) == target:
            best = k
    return best


def _find_cut_suffix(
    counts: list[int], target: int, lines_per_page: int
) -> int | None:
    """Index `k` such that files k..n occupy exactly `target` pages."""
    prefix = _prefix_counts(counts)
    total = prefix[-1]
    best: int | None = None
    for k in range(0, len(counts)):
        suffix_lines = total - prefix[k]
        if suffix_lines == 0:
            continue
        if _pages_for(suffix_lines, lines_per_page) == target:
            best = k
    return best


def _reachable_offsets(files: list[FileSpan]) -> list[int]:
    """Every line offset at which the material may be split.

    Offsets accumulate across files; inside a file only its safe cuts are
    offered. The result is sorted and de-duplicated.
    """
    offsets: set[int] = set()
    base = 0
    for file in files:
        for cut in file.cut_candidates():
            offsets.add(base + cut)
        base += file.line_count
    offsets.add(base)
    return sorted(o for o in offsets if o > 0)


def _arrange_exact(
    files: list[FileSpan], lines_per_page: int
) -> tuple[int, int] | None:
    """Find (front_end, back_start) line offsets giving exactly 30+30 pages.

    Both offsets must be reachable cut points. Crucially they must also fall on
    a page boundary: the two excerpts are concatenated and re-paginated, so a
    front segment ending one line into page 30 leaves a half-empty page that
    the back segment's opening lines flow into, silently merging two pages
    into one. An earlier version only required ceil(front/50) == 30, which
    accepted 1451 lines, and produced a 59-page document labelled "30+30".
    """
    total = sum(f.line_count for f in files)
    if total == 0:
        return None

    target = SEGMENT_PAGES * lines_per_page
    offsets = _reachable_offsets(files)

    # Only offsets that fill their last page exactly are usable here.
    fronts = {o for o in offsets if o == target}
    if not fronts:
        return None

    backs = {o for o in offsets if 0 < o <= total and total - o == target}
    if not backs:
        return None

    best: tuple[int, int, int] | None = None
    for front in fronts:
        for back in backs:
            if back <= front:
                continue
            gap = back - front
            if best is None or gap > best[0]:
                best = (gap, front, back)
    if best is None:
        return None
    return best[1], best[2]


def _arrange_reversed(files: list[FileSpan]) -> list[FileSpan]:
    """Alternate from both ends, which usually brings boundaries closer to the cuts.

    Large files clump; interleaving small ones near the middle makes an exact
    cut far more likely without changing the included file set.
    """
    ordered = sorted(files, key=lambda f: f.line_count, reverse=True)
    head: list[FileSpan] = []
    tail: list[FileSpan] = []
    for index, item in enumerate(ordered):
        (head if index % 2 == 0 else tail).append(item)
    return head + list(reversed(tail))


def select_material(
    files: list[FileSpan],
    *,
    lines_per_page: int = LINES_PER_PAGE,
    unselected_candidate_count: int = 0,
) -> Selection:
    """Decide how the given files become identifying material.

    `unselected_candidate_count` is how many eligible files exist that the
    caller did not include. It matters only in the under-60-page case, where
    silently submitting a short document would be wrong.
    """
    if lines_per_page <= 0:
        raise LayoutViolation("lines_per_page must be positive")

    usable = [f for f in files if f.line_count > 0]
    if not usable:
        raise SelectionError(
            "no source lines remain after cleaning; nothing can be submitted as material"
        )

    total_lines = sum(f.line_count for f in usable)
    total_pages = _pages_for(total_lines, lines_per_page)

    if total_pages < SPLIT_THRESHOLD_PAGES:
        if unselected_candidate_count > 0:
            raise SelectionError(
                f"selected files yield only {total_pages} pages, which is below the "
                f"{SPLIT_THRESHOLD_PAGES}-page threshold, but {unselected_candidate_count} "
                "candidate files were not selected. Select more files instead of "
                "submitting a short document."
            )
        return Selection(
            mode=SelectionMode.ALL,
            ordered_files=tuple(usable),
            lines_per_page=lines_per_page,
            total_lines=total_lines,
            total_pages=total_pages,
            front_pages=total_pages,
            back_pages=0,
        )

    # Try the caller's order first, then the interleaved order.
    for candidate in (usable, _arrange_reversed(usable)):
        arranged = _arrange_exact(candidate, lines_per_page)
        if arranged is None:
            continue
        front_end, back_start_offset = arranged
        back_lines = total_lines - back_start_offset
        return Selection(
            mode=SelectionMode.FRONT_BACK,
            ordered_files=tuple(candidate),
            lines_per_page=lines_per_page,
            total_lines=total_lines,
            total_pages=total_pages,
            front_pages=_pages_for(front_end, lines_per_page),
            back_pages=_pages_for(back_lines, lines_per_page),
            front_end_line=front_end,
            back_start_line=back_start_offset + 1,
            exact=True,
        )

    return _fallback_selection(usable, lines_per_page, total_lines, total_pages)


def _fallback_selection(
    files: list[FileSpan], lines_per_page: int, total_lines: int, total_pages: int
) -> Selection:
    """Align as closely as possible to 30+30, then report the truth."""
    counts = [f.line_count for f in files]
    prefix = _prefix_counts(counts)
    total_lines = prefix[-1]

    # Search at every reachable cut point rather than only at file edges.
    target_lines = SEGMENT_PAGES * lines_per_page
    offsets = _reachable_offsets(files)

    def search(lo: int, hi: int) -> tuple[int, int, int] | None:
        best: tuple[int, int, int] | None = None
        for front in offsets:
            front_pages = _pages_for(front, lines_per_page)
            if not (lo <= front_pages <= hi):
                continue
            for back in offsets:
                if back <= front:
                    continue
                back_lines = total_lines - back
                if back_lines <= 0:
                    continue
                back_pages = _pages_for(back_lines, lines_per_page)
                if not (lo <= back_pages <= hi):
                    continue
                delta = abs(front - target_lines) + abs(back_lines - target_lines)
                if best is None or delta < best[0]:
                    best = (delta, front, back)
        return best

    best = search(FALLBACK_MIN_SEGMENT_PAGES, FALLBACK_MAX_SEGMENT_PAGES)
    used_wider_band = False
    if best is None:
        best = search(FALLBACK_MIN_SEGMENT_PAGES, FALLBACK_HARD_MAX_SEGMENT_PAGES)
        used_wider_band = best is not None

    if best is None:
        approx = min(offsets, key=lambda o: abs(o - target_lines))
        raise SelectionError(
            f"cannot approach 30+30 pages within the allowed tolerance: the closest "
            f"split gives front {_pages_for(approx, lines_per_page)} "
            f"pages and back {_pages_for(total_lines - approx, lines_per_page)} "
            f"pages, outside {FALLBACK_MIN_SEGMENT_PAGES}-{FALLBACK_HARD_MAX_SEGMENT_PAGES}. "
            "Select different files, then retry."
        )

    _, front_lines, back_start_offset = best
    back_lines = total_lines - back_start_offset
    front_pages = _pages_for(front_lines, lines_per_page)
    back_pages = _pages_for(back_lines, lines_per_page)
    front_pages = _pages_for(front_lines, lines_per_page)
    back_pages = _pages_for(back_lines, lines_per_page)

    warnings: list[str] = [
        f"exact 30+30 could not be reached; material is front {front_pages} "
        f"pages and back {back_pages} pages. The filename states the true "
        "counts so it cannot be mistaken for 30+30."
    ]
    if used_wider_band and max(front_pages, back_pages) > FALLBACK_MAX_SEGMENT_PAGES:
        warnings.append(
            f"其中一段为 {max(front_pages, back_pages)} 页，略多于 30 页。"
            "这是在不切断文件的前提下最接近的结果；若要严格凑成 30 页，"
            "请选择更多小体量文件后重试。"
        )

    return Selection(
        mode=SelectionMode.FRONT_BACK,
        ordered_files=tuple(files),
        lines_per_page=lines_per_page,
        total_lines=total_lines,
        total_pages=total_pages,
        front_pages=front_pages,
        back_pages=back_pages,
        front_end_line=front_lines,
        back_start_line=total_lines - back_lines + 1,
        exact=False,
        warnings=tuple(warnings),
    )


def material_slices(selection: Selection) -> tuple[tuple[int, int], ...]:
    """Half-open line ranges to concatenate when rendering.

    For FRONT_BACK mode the two ranges are discontinuous, which the statute
    permits: the back segment need not follow the front segment directly.
    """
    if selection.mode is SelectionMode.ALL:
        return ((0, selection.total_lines),)
    if selection.front_pages + selection.back_pages == _pages_for(
        selection.total_lines, selection.lines_per_page
    ):
        # Front and back already cover everything contiguously, so there is no
        # gap to skip. Slicing it as two halves would still be correct, but a
        # single range lets the renderer treat it as one continuous stream.
        return (
            (0, selection.front_end_line),
            (selection.front_end_line, selection.total_lines),
        )
    return (
        (0, selection.front_end_line),
        (selection.back_start_line - 1, selection.total_lines),
    )
