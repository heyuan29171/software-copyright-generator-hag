"""The whole pipeline must fail loudly rather than emit doubtful material."""

from __future__ import annotations

from pathlib import Path

import pytest

from hag.layout import LINES_PER_PAGE
from hag.pipeline import PipelineError, build_pages, clean_sources, run_code_pipeline
from hag.render_pdf import HeaderSpec
from hag.selection import SelectionMode


def write(root: Path, rel: str, content: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def make_project(root: Path, files: int = 100, lines_each: int = 60) -> None:
    for index in range(files):
        body = "\n".join(f"const value{index}_{n} = {n};" for n in range(lines_each))
        write(root, f"src/module{index}.ts", body + "\n")


class TestCleanSources:
    def test_removes_comments_and_tracks_method(self, tmp_path: Path) -> None:
        write(tmp_path, "a.ts", "// c\nconst x = 1;\n")
        write(tmp_path, "b.py", "# c\nx = 1\n")
        from hag.discovery import discover

        result = clean_sources(discover(tmp_path).sources)
        assert len(result.files) == 2
        assert all(f.method.value == "full" for f in result.files)

    def test_partial_languages_are_reported(self, tmp_path: Path) -> None:
        write(tmp_path, "run.sh", "# c\necho hi\n")
        from hag.discovery import discover

        result = clean_sources(discover(tmp_path).sources)
        assert "run.sh" in result.skipped_partial

    def test_fully_commented_file_dropped_with_warning(self, tmp_path: Path) -> None:
        write(tmp_path, "empty.ts", "// only a comment\n")
        write(tmp_path, "real.ts", "const x = 1;\n")
        from hag.discovery import discover

        result = clean_sources(discover(tmp_path).sources)
        assert [f.rel_path for f in result.files] == ["real.ts"]
        assert any("empty.ts" in w for w in result.warnings)


class TestFullPipeline:
    def test_produces_sixty_pages(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        make_project(project)
        out = tmp_path / "out.pdf"
        result = run_code_pipeline(project, out, HeaderSpec("TestSystem", "V1.0"))
        assert result.rendered_pages == 60
        assert result.pdf_path == out
        assert out.is_file()

    def test_header_matches_request(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        make_project(project)
        header = HeaderSpec("学生管理系统", "V1.0")
        result = run_code_pipeline(project, tmp_path / "o.pdf", header)
        assert result.header.software_name == "学生管理系统"

    def test_manifest_records_sources(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        make_project(project, files=5, lines_each=20)
        result = run_code_pipeline(project, tmp_path / "o.pdf", HeaderSpec("S", "V1.0"))
        manifest = result.manifest()
        assert manifest["version"] == "V1.0"
        assert isinstance(manifest["files"], list)
        assert manifest["rendered_pages"] == result.rendered_pages

    def test_small_project_submits_everything(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        make_project(project, files=3, lines_each=30)
        result = run_code_pipeline(project, tmp_path / "o.pdf", HeaderSpec("S", "V1.0"))
        assert result.selection.mode is SelectionMode.ALL
        assert result.rendered_pages == result.selection.total_pages

    def test_no_render_mode_still_paginates(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        make_project(project, files=3, lines_each=30)
        result = run_code_pipeline(
            project, tmp_path / "o.pdf", HeaderSpec("S", "V1.0"), render=False
        )
        assert result.render is None
        assert result.rendered_pages == 0
        assert result.pages


class TestRefusals:
    def test_empty_project_raises(self, tmp_path: Path) -> None:
        project = tmp_path / "empty"
        project.mkdir()
        with pytest.raises(PipelineError, match="no source files"):
            run_code_pipeline(project, tmp_path / "o.pdf", HeaderSpec("S", "V1.0"))

    def test_all_comments_raises(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        write(project, "a.ts", "// nothing here\n")
        with pytest.raises(PipelineError, match="empty after cleaning"):
            run_code_pipeline(project, tmp_path / "o.pdf", HeaderSpec("S", "V1.0"))

    def test_short_project_with_excluded_files_raises(self, tmp_path: Path) -> None:
        """Excluding files must not silently turn a long project into a short one.

        Ten files of 300 lines each is exactly 60 pages; dropping one leaves
        54, which is short of the threshold while eligible files remain.
        """
        project = tmp_path / "proj"
        make_project(project, files=10, lines_each=300)
        with pytest.raises(PipelineError, match="not selected|below the"):
            run_code_pipeline(
                project,
                tmp_path / "o.pdf",
                HeaderSpec("S", "V1.0"),
                exclude=["module9"],
            )

    def test_stale_selection_detected(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        make_project(project, files=3, lines_each=10)
        from hag.discovery import discover
        from hag.selection import FileSpan, select_material

        cleaned = clean_sources(discover(project).sources)
        stale = select_material([FileSpan("does-not-exist.ts", 500)])
        with pytest.raises(PipelineError, match="not among the cleaned files"):
            build_pages(cleaned.files, stale)


class TestPageAssembly:
    def test_line_count_matches_selection(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        make_project(project)
        result = run_code_pipeline(project, tmp_path / "o.pdf", HeaderSpec("S", "V1.0"))
        total = sum(len(page) for page in result.pages)
        assert total == result.selection.rendered_pages * LINES_PER_PAGE

    def test_no_page_exceeds_capacity(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        make_project(project)
        result = run_code_pipeline(project, tmp_path / "o.pdf", HeaderSpec("S", "V1.0"))
        assert all(len(page) <= LINES_PER_PAGE for page in result.pages)
