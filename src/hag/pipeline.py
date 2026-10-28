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
from .discovery import DiscoveryResult, SourceFile, discover
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


@dataclass(slots=True)
class CleanResult:
    files: list[CleanedFile] = field(default_factory=list)
    skipped_partial: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def spans(self) -> list[FileSpan]:
        return [FileSpan(rel_path=f.rel_path, line_count=f.line_count) for f in self.files]


def clean_sources(
    sources: Iterable[SourceFile],
    *,
    layout: CodeLayout = CODE_LAYOUT,
    lines_per_page: int = LINES_PER_PAGE,
) -> CleanResult:
    """Strip comments, drop blanks and wrap, producing paginatable lines."""
    max_columns = max_columns_for(layout)
    result = CleanResult()

    for source in sources:
        stripped = strip_comments(source.text, source.suffix)
        lines = material_lines(
            stripped.text,
            max_columns,
            tab_width=layout.tab_width,
        )
        if not lines:
            result.warnings.append(
                f"{source.rel_path}: nothing remains after cleaning (all comments or blank)"
            )
            continue

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

    cleaned = clean_sources(sources, layout=layout, lines_per_page=lines_per_page)
    if not cleaned.files:
        raise PipelineError("every discovered file was empty after cleaning")

    unselected = count_unselected(discovery.sources, cleaned.files)

    try:
        selection = select_material(
            cleaned.spans(),
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
