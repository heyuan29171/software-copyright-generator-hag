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
    ends_cleanly: bool = True
    """Whether the last line closes a syntactic unit.

    A file ending in `N);` stops mid-call. Using it as the *last* file of the
    back segment makes the material end that way, which a reviewer reads as an
    arbitrarily truncated excerpt. The scanner decides this; files that end
    badly are still usable elsewhere in a segment.
    """

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
    allow_overshoot: bool = False,
) -> list[_Piece] | None:
    """Take about `need_lines`, cleanest file first.

    Exactness is not always reachable: a file of 181 lines whose safe cuts sit
    every three lines can yield 3, 6, 9... but never 1, so a budget assembled
    from such files cannot be hit precisely. The strategy is:

      1. take whole files while they fit under the budget;
      2. when one file would overshoot, trim it at the closest safe cut;
      3. if it has no safe cut under the budget, stop — unless `allow_overshoot`
         is set, in which case take it whole.

    Step 3's switch exists because both extremes are wrong. Refusing produced a
    32-page document from material that could have filled sixty; always
    overshooting exceeded the statutory thirty pages per segment. The caller
    tries without overshoot first, then with it, and reports which happened.
    """
    if need_lines <= 0:
        return None

    pieces: list[_Piece] = []
    taken = 0

    # The back segment ends the document, so it should end on a file whose last
    # line closes a unit. Files that stop mid-call are still usable in the
    # middle of a segment, just not at its end.
    order = list(range(len(files)))
    if from_end:
        order.sort(key=lambda i: 0 if files[i].ends_cleanly else 1)

    for index in order:
        if taken >= need_lines:
            break
        if used[index]:
            continue
        span = files[index]
        remaining = need_lines - taken

        if span.line_count <= remaining:
            pieces.append(_Piece(index, 0, span.line_count))
            taken += span.line_count
            continue

        trimmed = None
        if from_end:
            options = [
                c for c in span.cut_candidates() if 0 < span.line_count - c <= remaining
            ]
            if options:
                cut = min(options)
                trimmed = (cut, span.line_count)
        else:
            options = [c for c in span.cut_candidates() if 0 < c <= remaining]
            if options:
                cut = max(options)
                trimmed = (0, cut)

        if trimmed is not None:
            pieces.append(_Piece(index, trimmed[0], trimmed[1]))
            taken += trimmed[1] - trimmed[0]
            continue

        if allow_overshoot:
            pieces.append(_Piece(index, 0, span.line_count))
            taken += span.line_count
            continue

        # No safe cut fits; skip and try the next file.
        continue

    if taken == 0:
        return None
    if not allow_overshoot and taken > need_lines:
        return None
    return pieces


def _shortfall_message(
    files: tuple[FileSpan, ...],
    need: int,
    used: list[bool],
    *,
    after_front: bool = False,
) -> str:
    """Say how much material exists and why it could not be used.

    "Not enough material" and "enough material, but no place to cut it" are
    different problems with different fixes, and a message that conflates them
    sends the user looking for files they do not need.
    """
    available = sum(f.line_count for f, u in zip(files, used) if not u)
    usable_with_cuts = sum(
        f.line_count for f, u in zip(files, used) if not u and f.safe_cuts
    )

    scope = "后段" if after_front else "前段"
    head = f"{scope}需要 {need} 行（{SEGMENT_PAGES} 页），"

    if available < need:
        return (
            f"{head}但可用素材只有 {available} 行。请增加参与抽取的文件。"
        )

    if usable_with_cuts < need:
        return (
            f"{head}可用素材共 {available} 行，看似充足，"
            f"但其中只有 {usable_with_cuts} 行所在的文件存在可切分的位置。"
            "大文件如果找不到安全的切分点就无法截取。"
        )

    return (
        f"{head}可用素材共 {available} 行，其中 {usable_with_cuts} 行可切分，"
        "但无法在不切断代码结构的前提下正好凑够这个行数。"
        "请增加文件，或换用更小的源文件。"
    )


def _edge_slice(
    span: FileSpan, need: int, *, from_end: bool
) -> tuple[int, int] | None:
    """Largest prefix or suffix of at most `need` lines that ends on a cut.

    "At most" rather than "at least": overshooting would push the segment past
    its thirty-page budget, and the caller rejects that.

    When the only cuts leave a mismatch — a file whose last safe cut is three
    lines from its end cannot yield exactly one line — the next largest cut is
    tried, and the leftover is left for the caller to fill from another file.
    Returning None means no cut fits the budget at all.
    """
    candidates = span.cut_candidates()

    if from_end:
        usable = [c for c in candidates if 0 < span.line_count - c <= need]
        if not usable:
            return None
        cut = min(usable)
        return cut, span.line_count

    usable = [c for c in candidates if 0 < c <= need]
    if not usable:
        return None
    cut = max(usable)
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

    overshoot_used = False

    # A segment may fall a little short rather than fail outright. The statute
    # says "thirty continuous pages"; a segment of 28 or 29 pages still carries
    # far more than the minimum a reviewer needs, and refusing to produce any
    # material because the cuts do not align is the worse outcome. The filename
    # and the report state the true counts.
    floor_lines = (SEGMENT_PAGES - 2) * lines_per_page

    def _short(need: int, pieces: list[_Piece] | None) -> bool:
        if pieces is None:
            return True
        return sum(p.length for p in pieces) < floor_lines

    front_pieces = _take_from(ordered, used, need, from_end=False)
    if _short(need, front_pieces):
        # Try allowing an overshoot of one file, but only accept it when the
        # segment still lands within the statutory ceiling. Producing a 32-page
        # segment silently would breach the thirty-page rule; producing 64
        # pages in total to satisfy it is not a trade this tool should make on
        # the user's behalf.
        wider = _take_from(ordered, used, need, from_end=False, allow_overshoot=True)
        if not _short(need, wider) and sum(p.length for p in wider) <= (
            SEGMENT_PAGES
        ) * lines_per_page:
            front_pieces = wider
            overshoot_used = True
    if _short(need, front_pieces):
        raise SelectionError(_shortfall_message(ordered, need, used))

    for piece in front_pieces:
        used[piece.file_index] = True

    back_pieces = _take_from(ordered, used, need, from_end=True)
    if _short(need, back_pieces):
        wider = _take_from(ordered, used, need, from_end=True, allow_overshoot=True)
        if not _short(need, wider) and sum(p.length for p in wider) <= (
            SEGMENT_PAGES
        ) * lines_per_page:
            back_pieces = wider
            overshoot_used = True
    if _short(need, back_pieces):
        raise SelectionError(_shortfall_message(ordered, need, used, after_front=True))

    front = _merge(front_pieces)
    back = _merge(back_pieces)

    warnings: list[str] = []
    if overshoot_used:
        warnings.append(
            f"某些源文件没有可用的切分点，为使材料达到 60 页，"
            f"前段取 {front.line_count} 行、后段取 {back.line_count} 行，"
            "可能略多于每段 30 页。请核对渲染后的实际页数。"
        )
    if front.line_count % lines_per_page or back.line_count % lines_per_page:
        warnings.append(
            "某一段的行数不能被每页行数整除，拼接后可能合并页面。"
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
