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

    def __post_init__(self) -> None:
        if self.line_count < 0:
            raise ValueError(f"line_count must be non-negative, got {self.line_count}")


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


def _arrange_exact(
    files: list[FileSpan], lines_per_page: int
) -> tuple[int, int] | None:
    """Find a (front_cut, back_start) pair that yields exactly 30+30 pages.

    `front_cut` is the number of files in the front segment. `back_start` is
    the index of the first file in the back segment. There must be at least
    one file strictly between them for the result to be a genuine
    front/back split; if the whole file fits in 60 pages there is nothing to
    split and the caller should have chosen ALL mode.
    """
    counts = [f.line_count for f in files]
    total = sum(counts)
    if total == 0:
        return None

    front_cut = _find_cut_prefix(counts, SEGMENT_PAGES, lines_per_page)
    if front_cut is None:
        return None

    front_lines = sum(counts[:front_cut])
    remaining = counts[front_cut:]
    back_start_offset = _find_cut_suffix(remaining, SEGMENT_PAGES, lines_per_page)
    if back_start_offset is None:
        return None

    back_start = front_cut + back_start_offset
    if back_start <= front_cut:
        # No files in the middle: this is not a real split.
        return None

    _ = front_lines
    return front_cut, back_start


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
        front_cut, back_start = arranged
        front_lines = sum(f.line_count for f in candidate[:front_cut])
        back_lines = sum(f.line_count for f in candidate[back_start:])
        return Selection(
            mode=SelectionMode.FRONT_BACK,
            ordered_files=tuple(candidate),
            lines_per_page=lines_per_page,
            total_lines=total_lines,
            total_pages=total_pages,
            front_pages=_pages_for(front_lines, lines_per_page),
            back_pages=_pages_for(back_lines, lines_per_page),
            front_end_line=front_lines,
            back_start_line=total_lines - back_lines + 1,
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

    # Search both cut points together. Fixing only the front cut and taking
    # "everything else" as the back segment is wrong whenever the project is
    # much longer than 60 pages: the middle files should simply be skipped.
    #
    # The preferred band is tried first. Only if it yields nothing is the
    # wider band considered, because projects with uniformly sized files step
    # in file-sized increments and cannot hit 28..30 at all; refusing to
    # produce anything there would be worse than a documented 31.
    target_lines = SEGMENT_PAGES * lines_per_page

    def search(lo: int, hi: int) -> tuple[int, int, int] | None:
        best: tuple[int, int, int] | None = None
        for front_k in range(1, len(counts)):
            front_lines = prefix[front_k]
            front_pages = _pages_for(front_lines, lines_per_page)
            if not (lo <= front_pages <= hi):
                continue
            for back_start in range(front_k, len(counts)):
                back_lines = total_lines - prefix[back_start]
                if back_lines <= 0:
                    continue
                back_pages = _pages_for(back_lines, lines_per_page)
                if not (lo <= back_pages <= hi):
                    continue
                delta = abs(front_lines - target_lines) + abs(back_lines - target_lines)
                if best is None or delta < best[0]:
                    best = (delta, front_k, back_start)
        return best

    best = search(FALLBACK_MIN_SEGMENT_PAGES, FALLBACK_MAX_SEGMENT_PAGES)
    used_wider_band = False
    if best is None:
        best = search(FALLBACK_MIN_SEGMENT_PAGES, FALLBACK_HARD_MAX_SEGMENT_PAGES)
        used_wider_band = best is not None

    if best is None:
        # Report the closest single-cut attempt to keep the message concrete.
        approx_k = 1
        best_delta = None
        for k in range(1, len(counts)):
            delta = abs(prefix[k] - target_lines)
            if best_delta is None or delta < best_delta:
                best_delta = delta
                approx_k = k
        raise SelectionError(
            f"cannot approach 30+30 pages within the allowed tolerance: the closest "
            f"file-boundary split gives front {_pages_for(prefix[approx_k], lines_per_page)} "
            f"pages and back {_pages_for(total_lines - prefix[approx_k], lines_per_page)} "
            f"pages, outside {FALLBACK_MIN_SEGMENT_PAGES}-{FALLBACK_HARD_MAX_SEGMENT_PAGES}. "
            "Select different files, ideally several smaller ones near the middle, "
            "then retry."
        )

    _, front_k, back_start = best
    front_lines = prefix[front_k]
    back_lines = total_lines - prefix[back_start]
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
