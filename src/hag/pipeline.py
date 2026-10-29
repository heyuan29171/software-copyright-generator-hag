"""Running the whole code-material pipeline.

Each stage is a pure step with an explicit input and output, so a failure can
be attributed to one stage and re-run without redoing the others.

The pipeline refuses to produce material when a later stage would have to
guess: a file that cannot be decoded cleanly, a selection that leaves the
project short of sixty pages, a layout that cannot hold fifty lines. Every
refusal names the stage and the reason.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .comments import StripMethod, strip_comments
from .content_checks import ContentSuspicion, merge, scan_line
from .discovery import DiscoveryResult, SourceFile, discover
from .entry_point import choose_opening
from .masking import MaskReport, mask_line
from .layout import CODE_LAYOUT, LINES_PER_PAGE, CodeLayout, assert_layout_invariants
from .pagination import material_lines, max_columns_for, paginate
from .render_pdf import HeaderSpec, RenderResult, render_code_pdf
from .selection import FileSpan, Selection, SelectionError, material_slices, select_material


class PipelineError(Exception):
    """Raised when the pipeline cannot produce correct material."""


@dataclass(frozen=True, slots=True)
class CleanedFile:
    """One source file after comment removal."""

    rel_path: str
    suffix: str
    lines: tuple[str, ...]
    method: StripMethod
    removed_comment_lines: int = 0
    warnings: tuple[str, ...] = ()

    @property
    def line_count(self) -> int:
        return len(self.lines)

    @property
    def cjk_line_count(self) -> int:
        """Lines containing Chinese characters."""
        return sum(1 for line in self.lines if _has_cjk(line))

    @property
    def cjk_ratio(self) -> float:
        """Share of lines containing Chinese, used to prefer cleaner files."""
        if not self.lines:
            return 0.0
        return self.cjk_line_count / len(self.lines)


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


UNRENDERABLE_RUN = 3
"""Undrawable characters in one line before it is treated as an icon table.

Counting, not a ratio. A short line holding a single emoji scores a higher
ratio than a long table row holding eight, so ratios cannot separate them.
Three keeps a sentence with two emoji and drops a table row.
"""


def _mostly_unrenderable(line: str, pair: object) -> bool:
    """True when the line is an icon table rather than code."""
    try:
        missing = sum(
            1
            for ch in line
            if not (pair.mono.has(ch) or pair.cjk.has(ch))  # type: ignore[attr-defined]
        )
    except Exception:
        return False
    return missing >= UNRENDERABLE_RUN


def _ends_cleanly(lines: Sequence[str]) -> bool:
    """Whether the last kept line leaves the program balanced.

    Balanced brackets are the reliable signal, not the final character. A file
    ending in `N);` looks truncated only when the matching opener is missing;
    when every bracket closes, `);` is an ordinary statement ending and there
    is nothing wrong with the excerpt.

    A file left with open brackets means the material stops inside a call or a
    block, which a reviewer reads as an arbitrary cut.
    """
    if not lines:
        return False
    text = "\n".join(lines)
    depth = 0
    quote: str | None = None
    escaped = False
    for ch in text:
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "\"'`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
    # Negative depth means the excerpt begins inside a block, which is the
    # front segment's problem rather than this one; only a positive depth means
    # the excerpt stops before closing what it opened.
    if depth > 0:
        return False
    last = lines[-1].strip()
    if not last:
        return False
    return not last.endswith((",", "+", "-", "*", "&&", "||", "=", "."))


def safe_cut_offsets(lines: Sequence[str]) -> tuple[int, ...]:
    """Offsets where the material may be split without breaking a unit.

    A cut is safe when the next kept line is at the file's own base indentation
    and is not a closer, continuation or binary operator. Cutting anywhere else
    would sever an indented block or split an expression, and a reviewer would
    see a fragment that does not stand on its own.

    Base indentation is the *minimum* indentation across the file, not zero.
    Requiring column zero meant a Vue component found no cut points at all,
    because everything inside `<template>` and `<script>` is indented. A file
    whose shallowest line sits at six spaces is still cuttable at six spaces;
    what matters is that the cut falls on a boundary the reader can follow.
    """
    unsafe_start = (
        "}", ")", "]", ".", "+", "-", "*", "/", ",", ";", "&&", "||", "?",
        # HTML/JSX closing tags: cutting before one leaves the opening tag in
        # the previous segment and an orphan closer in the next.
        "</",
    )

    indents = [
        len(line) - len(line.lstrip())
        for line in lines
        if line.strip()
    ]
    if not indents:
        return ()
    base = min(indents)

    offsets: list[int] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or index == 0:
            continue
        if len(line) - len(line.lstrip()) > base:
            continue
        if stripped.startswith(unsafe_start):
            continue
        offsets.append(index)
    return tuple(offsets)


def prefer_low_cjk_order(files: Sequence[CleanedFile]) -> list[CleanedFile]:
    """Order files so the material draws on the least Chinese-heavy sources.

    A Django or Vue project is full of `verbose_name="羊只状态"` and
    `<h3>项目定位</h3>`. Deleting those lines would falsify the code, so the
    remaining option is to reach sixty pages using files that are mostly
    non-Chinese.

    Files are grouped into three bands by the share of lines containing
    Chinese, and each band keeps its original order. Sorting purely by ratio
    would shuffle related files apart, and the material should still read as a
    coherent program.
    """
    def band(file: CleanedFile) -> int:
        ratio = file.cjk_ratio
        if ratio <= 0.10:
            return 0
        if ratio <= 0.40:
            return 1
        return 2

    # Within a band, the cleaner file goes first; ties keep their original
    # order so related files stay adjacent.
    return sorted(files, key=lambda f: (band(f), f.cjk_ratio))


def promote_entry_point(files: Sequence[CleanedFile]) -> list[CleanedFile]:
    """Move the best entry-point candidate to the front, if there is one.

    Only the front position changes; every other file keeps its place. If no
    file looks like an entry point, the order is returned untouched, because
    promoting an arbitrary file would be worse than leaving it alone.
    """
    index = choose_opening(
        [(f.rel_path, "\n".join(f.lines[:40])) for f in files]
    )
    if index is None or index == 0:
        return list(files)
    ordered = list(files)
    ordered.insert(0, ordered.pop(index))
    return ordered


@dataclass(slots=True)
class CleanResult:
    files: list[CleanedFile] = field(default_factory=list)
    skipped_partial: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mask_report: MaskReport = field(default_factory=MaskReport)
    skipped_unrenderable: list[tuple[str, int]] = field(default_factory=list)
    suspicions: ContentSuspicion = field(default_factory=ContentSuspicion)

    def spans(self) -> list[FileSpan]:
        return [
            FileSpan(
                rel_path=f.rel_path,
                line_count=f.line_count,
                safe_cuts=safe_cut_offsets(f.lines),
                cjk_ratio=f.cjk_ratio,
                ends_cleanly=_ends_cleanly(f.lines),
            )
            for f in self.files
        ]


def clean_sources(
    sources: Iterable[SourceFile],
    *,
    layout: CodeLayout = CODE_LAYOUT,
    lines_per_page: int = LINES_PER_PAGE,
    drop_cjk: bool = False,
    applicant: str = "",
    mask_secrets: bool = True,
) -> CleanResult:
    """Strip comments, drop blanks and wrap, producing paginatable lines.

    `drop_cjk` additionally removes lines that are Chinese prose with no code
    in them. It is off by default: deleting source is a last resort, and the
    usual fix for too much Chinese is to draw the pages from cleaner files.

    `mask_secrets` replaces credentials, private addresses and contact details
    with asterisks. It is on by default: the material is sent to a third party,
    and a leaked key in a submitted document is worse than a masked one.

    `applicant` is the copyright holder's name; it is used to tell the
    applicant's own organisation apart from someone else's.
    """
    max_columns = max_columns_for(layout)
    result = CleanResult()

    for source in sources:
        stripped = strip_comments(source.text, source.suffix)
        lines = material_lines(
            stripped.text,
            max_columns,
            tab_width=layout.tab_width,
            drop_pure_cjk=drop_cjk,
        )
        if not lines:
            result.warnings.append(
                f"{source.rel_path}: nothing remains after cleaning (all comments or blank)"
            )
            continue

        # Drop lines that are mostly characters the fonts cannot draw. An icon
        # lookup table is four to eight emoji per line: once replaced it reads
        # as `<U+1F56F><U+FE0F>': 'candle',` noise rather than as a program.
        # Ordinary code carries one or two emoji at most and is kept.
        from .fonts import resolve_font_pair as _resolve

        try:
            pair = _resolve()
        except Exception:
            pair = None

        if pair is not None:
            kept: list[str] = []
            for line_no, line in enumerate(lines, start=1):
                if _mostly_unrenderable(line, pair):
                    result.skipped_unrenderable.append((source.rel_path, line_no))
                    continue
                kept.append(line)
            lines = kept

        if mask_secrets:
            masked_lines: list[str] = []
            for line_no, line in enumerate(lines, start=1):
                masked, fired = mask_line(line)
                if fired:
                    result.mask_report.record(source.rel_path, line_no, fired)
                masked_lines.append(masked)
            lines = masked_lines

        for line_no, line in enumerate(lines, start=1):
            found = scan_line(source.rel_path, line_no, line, applicant)
            merge(result.suspicions, found)

        cleaned = CleanedFile(
            rel_path=source.rel_path,
            suffix=source.suffix,
            lines=tuple(lines),
            method=stripped.method,
            removed_comment_lines=stripped.removed_comment_lines,
            warnings=tuple(stripped.warnings),
        )
        result.files.append(cleaned)
        if stripped.method is StripMethod.LINE_ONLY:
            result.skipped_partial.append(source.rel_path)
        if stripped.method is StripMethod.NONE:
            result.warnings.append(
                f"{source.rel_path}: no comment syntax known; comments, if any, were kept"
            )
        for warning in stripped.warnings:
            result.warnings.append(f"{source.rel_path}: {warning}")

    return result


def build_pages(
    cleaned: Sequence[CleanedFile], selection: Selection
) -> list[list[str]]:
    """Concatenate the selected line ranges and paginate them."""
    stream: list[str] = []
    for file in selection.ordered_files:
        match = next((f for f in cleaned if f.rel_path == file.rel_path), None)
        if match is None:
            raise PipelineError(
                f"selection refers to {file.rel_path}, which is not among the cleaned files"
            )
        stream.extend(match.lines)

    if len(stream) != selection.total_lines:
        raise PipelineError(
            f"selection expected {selection.total_lines} lines but the cleaned files "
            f"hold {len(stream)}; the selection is stale"
        )

    sliced: list[str] = []
    for start, end in material_slices(selection):
        sliced.extend(stream[start:end])

    return paginate(sliced, selection.lines_per_page)


@dataclass(slots=True)
class PipelineResult:
    discovery: DiscoveryResult
    cleaned: CleanResult
    selection: Selection
    render: RenderResult | None
    pages: list[list[str]]
    header: HeaderSpec

    @property
    def rendered_pages(self) -> int:
        if self.render is None:
            return 0
        return self.render.rendered_pages

    @property
    def pdf_path(self) -> Path | None:
        return self.render.pdf_path if self.render else None

    def manifest(self) -> dict[str, object]:
        """Provenance for tracing any material line back to its source file."""
        return {
            "software_name": self.header.software_name,
            "version": self.header.version,
            "mode": self.selection.mode.value,
            "lines_per_page": self.selection.lines_per_page,
            "total_lines": self.selection.total_lines,
            "front_pages": self.selection.front_pages,
            "back_pages": self.selection.back_pages,
            "rendered_pages": self.rendered_pages,
            "exact": self.selection.exact,
            "title": self.selection.title,
            "warnings": list(self.selection.warnings),
            "files": [
                {
                    "path": f.rel_path,
                    "material_lines": f.line_count,
                    "strip_method": f.method.value,
                    "removed_comment_lines": f.removed_comment_lines,
                }
                for f in self.cleaned.files
            ],
            "skipped_partial": list(self.cleaned.skipped_partial),
            "skipped_files": [
                {"path": s.path.name, "reason": s.reason.value, "detail": s.detail}
                for s in self.discovery.skipped
            ],
        }


def run_code_pipeline(
    project_root: Path,
    out_path: Path,
    header: HeaderSpec,
    *,
    layout: CodeLayout = CODE_LAYOUT,
    lines_per_page: int = LINES_PER_PAGE,
    include: Sequence[str] | None = None,
    exclude: Sequence[str] | None = None,
    render: bool = True,
    drop_cjk: bool = False,
    applicant: str = "",
    mask_secrets: bool = True,
) -> PipelineResult:
    """Discover, clean, select, paginate and (optionally) render.

    `include` and `exclude` are relative-path glob-ish filters applied before
    cleaning, for the case where a user wants to steer which files count.
    """
    assert_layout_invariants(layout, lines_per_page)

    discovery = discover(project_root)
    sources = _apply_filters(discovery.sources, include, exclude)
    if not sources:
        raise PipelineError(
            f"no source files found under {project_root}; "
            f"{len(discovery.skipped)} entries were skipped"
        )

    cleaned = clean_sources(
        sources,
        layout=layout,
        lines_per_page=lines_per_page,
        drop_cjk=drop_cjk,
        applicant=applicant,
        mask_secrets=mask_secrets,
    )
    if not cleaned.files:
        raise PipelineError("every discovered file was empty after cleaning")

    unselected = count_unselected(discovery.sources, cleaned.files)

    # Draw the material from the least Chinese-heavy files first. Deleting
    # Chinese lines would falsify the source, so the ordering is what keeps the
    # Chinese-line count down instead.
    ordered = prefer_low_cjk_order(cleaned.files)
    # The first page is what a reviewer sees first, so an entry point makes a
    # stronger opening than whichever file happens to be cleanest. This only
    # reorders; it never changes which files are used.
    ordered = promote_entry_point(ordered)
    spans = [
        FileSpan(
            rel_path=f.rel_path,
            line_count=f.line_count,
            safe_cuts=safe_cut_offsets(f.lines),
            cjk_ratio=f.cjk_ratio,
            ends_cleanly=_ends_cleanly(f.lines),
        )
        for f in ordered
    ]

    try:
        selection = select_material(
            spans,
            lines_per_page=lines_per_page,
            unselected_candidate_count=unselected,
        )
    except SelectionError as exc:
        raise PipelineError(str(exc)) from exc

    pages = build_pages(cleaned.files, selection)

    rendered: RenderResult | None = None
    if render:
        rendered = render_code_pdf(
            pages,
            out_path,
            header,
            layout=layout,
            lines_per_page=lines_per_page,
        )
        if rendered.rendered_pages != selection.rendered_pages:
            raise PipelineError(
                f"pagination predicted {selection.rendered_pages} pages but the "
                f"rendered file has {rendered.rendered_pages}"
            )

    return PipelineResult(
        discovery=discovery,
        cleaned=cleaned,
        selection=selection,
        render=rendered,
        pages=pages,
        header=header,
    )


def count_unselected(sources: Sequence[SourceFile], cleaned: Sequence[CleanedFile]) -> int:
    """Files that were discovered but did not contribute material.

    Filters and comment removal both drop files, and both matter: if a file
    was excluded or turned out to be all comments, the project may really be
    long enough for a 30+30 submission. Counting only one of those sources
    would let a short document be submitted as if nothing were missing.
    """
    included = {f.rel_path for f in cleaned}
    return sum(1 for source in sources if source.rel_path not in included)


def _apply_filters(
    sources: Sequence[SourceFile],
    include: Sequence[str] | None,
    exclude: Sequence[str] | None,
) -> list[SourceFile]:
    def matches(rel_path: str, patterns: Sequence[str]) -> bool:
        lowered = rel_path.lower()
        return any(pattern.lower() in lowered for pattern in patterns)

    selected = list(sources)
    if include:
        selected = [s for s in selected if matches(s.rel_path, include)]
    if exclude:
        selected = [s for s in selected if not matches(s.rel_path, exclude)]
    return selected


__all__ = [
    "CleanResult",
    "CleanedFile",
    "PipelineError",
    "PipelineResult",
    "build_pages",
    "clean_sources",
    "run_code_pipeline",
]
