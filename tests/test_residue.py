"""The residue check must not fire on valid material.

An earlier version re-checked each cleaned line in isolation. A line such as
``class Point:`` is not valid Python by itself, so the body-repair step
appended ``pass`` and every class definition was reported as comment residue.
That blocked generation of otherwise correct material.
"""

from __future__ import annotations

from pathlib import Path

from hag.pipeline import PipelineResult, clean_sources, run_code_pipeline
from hag.build import _comment_residue
from hag.render_pdf import HeaderSpec


def write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build(project: Path) -> PipelineResult:
    return run_code_pipeline(
        project,
        project / "out.pdf",
        HeaderSpec("Sys", "V1.0"),
        render=False,
    )


class TestResidueCheck:
    def test_python_class_definition_not_flagged(self, tmp_path: Path) -> None:
        """A bare `class Foo:` is not a comment and must not be reported."""
        write(
            tmp_path,
            "a.py",
            '"""Module."""\n'
            "class Foo:\n"
            "    x = 1\n\n"
            "class Bar(Exception):\n"
            '    """Doc."""\n'
            "    pass\n",
        )
        result = build(tmp_path)
        assert _comment_residue(result) == []

    def test_python_if_main_not_flagged(self, tmp_path: Path) -> None:
        write(tmp_path, "a.py", 'if __name__ == "__main__":\n    print(1)\n')
        result = build(tmp_path)
        assert _comment_residue(result) == []

    def test_clean_typescript_not_flagged(self, tmp_path: Path) -> None:
        write(tmp_path, "a.ts", "// gone\nexport class A { run() { return 1; } }\n")
        result = build(tmp_path)
        assert _comment_residue(result) == []

    def test_real_residue_is_flagged(self, tmp_path: Path) -> None:
        """A file whose comments could not be stripped is still reported."""
        path = tmp_path / "a.xyz"
        path.write_text("// comment\nlet x = 1;\n", encoding="utf-8")
        from hag.discovery import discover

        cleaned = clean_sources(discover(tmp_path).sources)
        # .xyz has no registered syntax, so the method is NONE and residue is
        # reported through the partial-coverage warning instead.
        assert all(f.method.value == "none" for f in cleaned.files)
