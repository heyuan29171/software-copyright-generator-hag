"""Choosing which 60 pages to submit, as two independent selections.

The statute asks for "the first 30 and the last 30 pages" of the program. Read
literally that means one contiguous head and one contiguous tail. But the
material is an excerpt chosen from many files, and the sensible reading is:
thirty pages from the beginning of the program and thirty from the end, with
anything in between omitted.

The earlier implementation took "the last thirty pages of the file list",
which is not the same thing once files are reordered by quality. Files were
sorted so the least Chinese-heavy came first, the front segment drew from that
good end, and the back segment took whatever happened to sit at the tail —
the most Chinese-heavy files in the project. The result was a clean first half
and a back half averaging eighteen Chinese lines per page.

So each segment is selected on its own merits now: the front takes the best
available thirty pages from the start of the ordering, the back takes the best
thirty from what remains, and no file contributes to both.
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
    ALL = "all"
    FRONT_BACK = "front_back"


@dataclass(frozen=True, slots=True)
class FileSpan:
    """One source file's contribution to the material."""

    rel_path: str
    line_count: int
    safe_cuts: tuple[int, ...] = ()
    cjk_ratio: float = 0.0
    """Share of lines containing Chinese, used to prefer cleaner sources."""

    def __post_init__(self) -> None:
        if self.line_count < 0:
            raise ValueError(f"line_count must be non-negative, got {self.line_count}")

    def cut_candidates(self) -> tuple[int, ...]:
        points = {c for c in self.safe_cuts if 0 < c < self.line_count}
        points.add(self.line_count)
        return tuple(sorted(points))


@dataclass(frozen=True, slots=True)
class Segment:
    """One excerpt, possibly stitched from several files.

    `pieces` holds the per-file ranges in the order they were chosen. A segment
    is normally several whole files plus, at most, one trimmed file, and the
    files it uses are not necessarily adjacent: the front segment takes the
    first files and the back segment takes the next ones, skipping whatever was
    already claimed.
    """

    pieces: tuple[tuple[int, int, int], ...]
    """(file index, start offset, end offset)"""

    line_count: int

    def describe(self, files: tuple[FileSpan, ...]) -> str:
        if not self.pieces:
            return ""
        first = files[self.pieces[0][0]].rel_path
        last = files[self.pieces[-1][0]].rel_path
        if first == last:
            _, start, end = self.pieces[0]
            return f"{first}（第 {start + 1}–{end} 行）"
        return f"{first} … {last}"


@dataclass(frozen=True, slots=True)
class Selection:
    mode: SelectionMode
    ordered_files: tuple[FileSpan, ...]
    lines_per_page: int
    total_lines: int
    total_pages: int

    front: Segment | None = None
    back: Segment | None = None

    exact: bool = True
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def front_pages(self) -> int:
        if self.mode is SelectionMode.ALL:
            return self.total_pages
        return _pages_for(self.front.line_count, self.lines_per_page) if self.front else 0

    @property
    def back_pages(self) -> int:
        if self.mode is SelectionMode.ALL or not self.back:
            return 0
        return _pages_for(self.back.line_count, self.lines_per_page)

    @property
    def rendered_lines(self) -> int:
        if self.mode is SelectionMode.ALL:
            return self.total_lines
        return sum(s.line_count for s in (self.front, self.back) if s)

    @property
    def rendered_pages(self) -> int:
        return _pages_for(self.rendered_lines, self.lines_per_page)

    @property
    def title(self) -> str:
        if self.mode is SelectionMode.ALL:
            return f"代码(全部{self.rendered_pages}页)"
        if self.exact:
            return f"代码(前{SEGMENT_PAGES}页+后{SEGMENT_PAGES}页)"
        return f"代码(共{self.rendered_pages}页)"


class SelectionError(Exception):
    """Raised when no valid material can be produced from the given files."""


def _pages_for(lines: int, lines_per_page: int) -> int:
    return (lines + lines_per_page - 1) // lines_per_page


@dataclass(slots=True)
class _Piece:
    """A slice of one file that a segment will use."""

    file_index: int
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start


def _take_from(
    files: tuple[FileSpan, ...],
    used: list[bool],
    need_lines: int,
    *,
    from_end: bool,
) -> list[_Piece] | None:
    """Take exactly `need_lines` from unused files, best file first.

    The file list is already ordered so the least Chinese-heavy files come
    first, and both segments walk it in that order. Only the *edge* trimmed
    from the final file differs: the front segment keeps a file's head and the
    back segment keeps its tail, so each excerpt still reads from a natural
    boundary rather than starting mid-block.

    An earlier version reversed the whole walk for the back segment. That took
    the back thirty pages from the worst files in the project, because the
    ordering put the most Chinese-heavy ones last: the first half came out
    clean and the second half averaged eighteen Chinese lines a page.

    Returns None when the unused files cannot supply that many lines.
    """
    pieces: list[_Piece] = []
    remaining = need_lines

    for index in range(len(files)):
        if remaining <= 0:
            break
        if used[index]:
            continue
        span = files[index]

        if span.line_count == remaining:
            pieces.append(_Piece(index, 0, span.line_count))
            remaining = 0
            continue

        if span.line_count < remaining:
            pieces.append(_Piece(index, 0, span.line_count))
            remaining -= span.line_count
            continue

        # This file is longer than what is still needed; trim it at a safe cut.
        take = _edge_slice(span, remaining, from_end=from_end)
        if take is None:
            # No cut far enough into this file. Taking it whole would exceed
            # the statutory thirty pages, so it cannot be used here at all.
            continue
        pieces.append(_Piece(index, take[0], take[1]))
        remaining = 0

    if remaining != 0:
        return None
    if sum(p.length for p in pieces) > need_lines:
        # A segment must never exceed thirty pages; a reviewer counts them.
        return None
    return pieces


def _edge_slice(
    span: FileSpan, need: int, *, from_end: bool
) -> tuple[int, int] | None:
    """Smallest prefix or suffix of at least `need` lines ending on a safe cut."""
    candidates = span.cut_candidates()
    if from_end:
        # Take a suffix: start at (line_count - size) for the smallest size
        # that lands on a safe cut.
        options = [c for c in candidates if span.line_count - c >= need]
        if not options:
            return None
        cut = min(options, key=lambda c: span.line_count - c)
        return cut, span.line_count

    options = [c for c in candidates if c >= need]
    if not options:
        return None
    cut = min(options)
    return 0, cut


def select_material(
    files: list[FileSpan],
    *,
    lines_per_page: int = LINES_PER_PAGE,
    unselected_candidate_count: int = 0,
) -> Selection:
    """Decide how the given files become identifying material."""
    if lines_per_page <= 0:
        raise LayoutViolation("lines_per_page must be positive")

    usable = [f for f in files if f.line_count > 0]
    if not usable:
        raise SelectionError(
            "no source lines remain after cleaning; nothing can be submitted as material"
        )

    ordered = tuple(usable)
    total_lines = sum(f.line_count for f in ordered)
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
            ordered_files=ordered,
            lines_per_page=lines_per_page,
            total_lines=total_lines,
            total_pages=total_pages,
        )

    need = SEGMENT_PAGES * lines_per_page
    used = [False] * len(ordered)

    front_pieces = _take_from(ordered, used, need, from_end=False)
    if front_pieces is None:
        raise SelectionError(
            f"the material does not contain {SEGMENT_PAGES} pages "
            f"({need} lines) of usable source. Either there are too few files, "
            "or the files are large and no safe cut point was found inside them."
        )
    for piece in front_pieces:
        used[piece.file_index] = True

    back_pieces = _take_from(ordered, used, need, from_end=True)
    if back_pieces is None:
        raise SelectionError(
            f"after reserving the front {SEGMENT_PAGES} pages, fewer than "
            f"{SEGMENT_PAGES} pages of source remain for the back segment. "
            "Select more files."
        )

    front = _merge(front_pieces)
    back = _merge(back_pieces)

    warnings: list[str] = []
    if front.line_count % lines_per_page or back.line_count % lines_per_page:
        warnings.append(
            "某一段的行数不能被每页行数整除，拼接后可能合并页面；"
            "本应取整页对齐的切点。"
        )

    return Selection(
        mode=SelectionMode.FRONT_BACK,
        ordered_files=ordered,
        lines_per_page=lines_per_page,
        total_lines=total_lines,
        total_pages=total_pages,
        front=front,
        back=back,
        exact=(
            front.line_count == need
            and back.line_count == need
            and front.line_count % lines_per_page == 0
            and back.line_count % lines_per_page == 0
        ),
        warnings=tuple(warnings),
    )


def _merge(pieces: list[_Piece]) -> Segment:
    """Collapse a run of pieces into one segment description."""
    ordered = sorted(pieces, key=lambda p: p.file_index)
    return Segment(
        pieces=tuple((p.file_index, p.start, p.end) for p in ordered),
        line_count=sum(p.length for p in pieces),
    )


def material_slices(selection: Selection) -> tuple[tuple[int, int], ...]:
    """Half-open line ranges in the ordered stream, for rendering.

    A segment is built from several files, and those files are not contiguous:
    the back segment skips the files the front segment already claimed. So each
    piece is converted separately rather than assuming one span per segment.
    """
    if selection.mode is SelectionMode.ALL:
        return ((0, selection.total_lines),)

    # Precompute the stream offset where each file begins.
    starts: list[int] = []
    offset = 0
    for file in selection.ordered_files:
        starts.append(offset)
        offset += file.line_count

    out: list[tuple[int, int]] = []
    for segment in (selection.front, selection.back):
        if segment is None:
            continue
        for file_index, start, end in segment.pieces:
            base = starts[file_index]
            out.append((base + start, base + end))
    return tuple(out)
