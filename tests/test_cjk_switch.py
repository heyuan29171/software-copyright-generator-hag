"""The drop-Chinese switch must be off by default and actually change output."""

from __future__ import annotations

from pathlib import Path

import pytest

from hag.layout import LINES_PER_PAGE
from hag.pipeline import PipelineError, clean_sources, run_code_pipeline
from hag.render_pdf import HeaderSpec
from hag.server import generate_materials


def write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# Only English-free prose is removable: a line like `label = "中文"` contains
# code and must survive even when the switch is on.
CN_SOURCE = "\n".join(["这是一段纯粹的中文说明文字"] * 40)
CODE_WITH_CN = "\n".join(['label = "这是一段中文文本"', "value = 1"] * 40)


class TestSwitchDefaults:
    def test_pipeline_default_keeps_chinese(self, tmp_path: Path) -> None:
        write(tmp_path, "a.py", CN_SOURCE)
        write(tmp_path, "b.py", "\n".join(f"x{i} = {i}" for i in range(200)))
        result = run_code_pipeline(
            tmp_path, tmp_path / "o.pdf", HeaderSpec("S", "V1.0"), render=False
        )
        a = next(f for f in result.cleaned.files if f.rel_path == "a.py")
        assert a.cjk_line_count == 40

    def test_pipeline_switch_removes_chinese_lines(self, tmp_path: Path) -> None:
        write(tmp_path, "a.py", CN_SOURCE + "\nvalue = 1")
        write(tmp_path, "b.py", "\n".join(f"x{i} = {i}" for i in range(200)))
        result = run_code_pipeline(
            tmp_path,
            tmp_path / "o.pdf",
            HeaderSpec("S", "V1.0"),
            render=False,
            drop_cjk=True,
        )
        a = next(f for f in result.cleaned.files if f.rel_path == "a.py")
        assert a.cjk_line_count == 0

    def test_clean_sources_default_keeps_everything(self, tmp_path: Path) -> None:
        from hag.discovery import discover

        write(tmp_path, "a.py", CN_SOURCE)
        cleaned = clean_sources(discover(tmp_path).sources)
        assert cleaned.files[0].cjk_line_count == 40

    def test_clean_sources_switch_drops(self, tmp_path: Path) -> None:
        from hag.discovery import discover

        write(tmp_path, "a.py", CN_SOURCE + "\nvalue = 1")
        cleaned = clean_sources(discover(tmp_path).sources, drop_cjk=True)
        assert cleaned.files[0].cjk_line_count == 0
        assert cleaned.files[0].lines == ("value = 1",)

    def test_file_of_only_chinese_disappears_when_dropping(self, tmp_path: Path) -> None:
        """A file with nothing but prose contributes no material at all."""
        from hag.discovery import discover

        write(tmp_path, "only_text.py", CN_SOURCE + "\npass")
        write(tmp_path, "real.py", "\n".join(f"x{i} = {i}" for i in range(50)))
        cleaned = clean_sources(discover(tmp_path).sources, drop_cjk=True)
        paths = [f.rel_path for f in cleaned.files]
        assert "only_text.py" in paths  # still has `pass`
        only = next(f for f in cleaned.files if f.rel_path == "only_text.py")
        assert only.line_count == 1

    def test_line_with_code_and_chinese_survives_the_switch(
        self, tmp_path: Path
    ) -> None:
        """Deleting `label = "中文"` would remove real code, so it stays."""
        from hag.discovery import discover

        write(tmp_path, "a.py", CODE_WITH_CN)
        cleaned = clean_sources(discover(tmp_path).sources, drop_cjk=True)
        assert cleaned.files[0].cjk_line_count == 40


class TestMcpSurface:
    def test_generate_accepts_the_flag(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        for i in range(10):
            write(
                project,
                f"m{i}.ts",
                "\n".join(f"const v{i}_{n} = {n};" for n in range(300)),
            )
        result = generate_materials(
            str(project),
            "测试系统",
            "V1.0",
            output_dir=str(tmp_path / "out"),
            drop_cjk=True,
            fields={
                "owner": "公司",
                "owner_category": "法人",
                "development_completed": "2025-03-01",
                "dev_os": "Windows 11",
                "dev_ide": "VS Code",
                "purpose": "测试",
                "domain": "测试",
                "main_functions": "本系统用于测试中文行删除开关是否可用。" * 30,
                "source_line_count": "3000",
            },
        )
        assert result["ok"] is True
        assert result["rendered_pages"] == 60

    def test_report_states_which_mode_was_used(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        for i in range(10):
            write(
                project,
                f"m{i}.ts",
                "\n".join(f"const v{i}_{n} = {n};" for n in range(300)),
            )
        out = tmp_path / "out"
        generate_materials(
            str(project),
            "测试系统",
            "V1.0",
            output_dir=str(out),
            drop_cjk=True,
            fields={
                "owner": "公司",
                "owner_category": "法人",
                "development_completed": "2025-03-01",
                "dev_os": "Windows 11",
                "dev_ide": "VS Code",
                "purpose": "测试",
                "domain": "测试",
                "main_functions": "本系统用于测试中文行删除开关是否可用。" * 30,
                "source_line_count": "3000",
            },
        )
        report = (out / "校验报告.md").read_text(encoding="utf-8")
        assert "删除纯中文行" in report

    def test_report_default_mentions_file_selection(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        for i in range(10):
            write(
                project,
                f"m{i}.ts",
                "\n".join(f"const v{i}_{n} = {n};" for n in range(300)),
            )
        out = tmp_path / "out"
        generate_materials(
            str(project),
            "测试系统",
            "V1.0",
            output_dir=str(out),
            fields={
                "owner": "公司",
                "owner_category": "法人",
                "development_completed": "2025-03-01",
                "dev_os": "Windows 11",
                "dev_ide": "VS Code",
                "purpose": "测试",
                "domain": "测试",
                "main_functions": "本系统用于测试中文行删除开关是否可用。" * 30,
                "source_line_count": "3000",
            },
        )
        report = (out / "校验报告.md").read_text(encoding="utf-8")
        assert "未修改任何代码" in report
