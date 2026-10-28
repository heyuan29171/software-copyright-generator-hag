"""Tools must return usable answers, including when things go wrong."""

from __future__ import annotations

from pathlib import Path

import pytest

from hag.application import ApplicationFields
from hag.server import (
    check_fields,
    create_server,
    estimate_material,
    explain_rules,
    generate_materials,
    inspect_project,
)


def write(root: Path, rel: str, content: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def make_project(root: Path, files: int = 10, lines_each: int = 300) -> Path:
    for index in range(files):
        body = "\n".join(f"const value{index}_{n} = {n};" for n in range(lines_each))
        write(root, f"src/module{index}.ts", body + "\n")
    return root


def valid_field_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "software_name": "学生信息管理系统",
        "version": "V1.0",
        "owner": "某某科技有限公司",
        "owner_category": "法人",
        "development_completed": "2025-03-01",
        "dev_os": "Windows 11",
        "dev_ide": "Visual Studio Code",
        "main_functions": "本系统用于记录学生信息。" * 60,
        "source_line_count": "30000",
        "purpose": "用于学校日常学生信息管理",
        "domain": "教育",
    }
    payload.update(overrides)
    return payload


class TestInspectProject:
    def test_reports_sources(self, tmp_path: Path) -> None:
        make_project(tmp_path, files=3, lines_each=10)
        result = inspect_project(str(tmp_path))
        assert result["ok"] is True
        assert result["source_files"] == 3
        assert result["by_suffix"][0]["suffix"] == ".ts"

    def test_missing_directory(self, tmp_path: Path) -> None:
        result = inspect_project(str(tmp_path / "nope"))
        assert result["ok"] is False
        assert "not a directory" in result["error"]

    def test_empty_directory(self, tmp_path: Path) -> None:
        result = inspect_project(str(tmp_path))
        assert result["ok"] is False

    def test_skipped_are_explained(self, tmp_path: Path) -> None:
        write(tmp_path, "a.ts", "const x = 1;\n")
        write(tmp_path, "logo.png", "not really a png")
        result = inspect_project(str(tmp_path))
        assert result["skipped_count"] >= 1
        assert all("reason" in s for s in result["skipped"])


class TestEstimate:
    def test_predicts_sixty_pages(self, tmp_path: Path) -> None:
        make_project(tmp_path)
        result = estimate_material(str(tmp_path))
        assert result["ok"] is True
        assert result["rendered_pages"] == 60
        assert result["front_pages"] == 30
        assert result["back_pages"] == 30

    def test_does_not_write_a_pdf(self, tmp_path: Path) -> None:
        make_project(tmp_path)
        estimate_material(str(tmp_path))
        assert not (tmp_path / "unused.pdf").exists()

    def test_short_project_reported(self, tmp_path: Path) -> None:
        make_project(tmp_path, files=2, lines_each=30)
        result = estimate_material(str(tmp_path))
        assert result["ok"] is True
        assert result["mode"] == "all"
        assert result["rendered_pages"] < 60

    def test_missing_directory(self, tmp_path: Path) -> None:
        assert estimate_material(str(tmp_path / "nope"))["ok"] is False


class TestGenerate:
    def test_full_generation(self, tmp_path: Path) -> None:
        project = make_project(tmp_path / "proj")
        out = tmp_path / "out"
        result = generate_materials(
            str(project),
            "学生信息管理系统",
            "V1.0",
            output_dir=str(out),
            fields=valid_field_payload(),
        )
        assert result["ok"] is True, result["blocking"]
        assert result["rendered_pages"] == 60
        assert Path(result["report"]).is_file()

    def test_form_values_written(self, tmp_path: Path) -> None:
        project = make_project(tmp_path / "proj")
        out = tmp_path / "out"
        generate_materials(
            str(project), "学生信息管理系统", "V1.0", output_dir=str(out),
            fields=valid_field_payload(),
        )
        text = (out / "正式资料" / "申请表信息.txt").read_text(encoding="utf-8")
        assert "学生信息管理系统" in text
        assert "V1.0" in text
        assert "60" in text

    def test_requires_name(self, tmp_path: Path) -> None:
        project = make_project(tmp_path / "proj")
        assert generate_materials(str(project), "  ", "V1.0")["ok"] is False

    def test_requires_version(self, tmp_path: Path) -> None:
        project = make_project(tmp_path / "proj")
        assert generate_materials(str(project), "Sys", "")["ok"] is False

    def test_page_count_comes_from_rendering(self, tmp_path: Path) -> None:
        """A caller-supplied page count must not override the rendered value."""
        project = make_project(tmp_path / "proj")
        out = tmp_path / "out"
        result = generate_materials(
            str(project),
            "学生信息管理系统",
            "V1.0",
            output_dir=str(out),
            fields=valid_field_payload(page_count="999"),
        )
        text = (out / "正式资料" / "申请表信息.txt").read_text(encoding="utf-8")
        assert "999" not in text

    def test_invalid_fields_reported(self, tmp_path: Path) -> None:
        project = make_project(tmp_path / "proj")
        result = generate_materials(
            str(project),
            "学生信息管理系统",
            "V1.0",
            output_dir=str(tmp_path / "out"),
            fields=valid_field_payload(owner=""),
        )
        assert result["ok"] is False
        assert any("申请表" in item["name"] for item in result["blocking"])

    def test_pipeline_failure_returns_error(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        result = generate_materials(str(empty), "Sys", "V1.0")
        assert result["ok"] is False
        assert result["blocking"]

    def test_report_written_even_on_failure(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        out = tmp_path / "out"
        generate_materials(str(empty), "Sys", "V1.0", output_dir=str(out))
        assert (out / "校验报告.md").is_file()


class TestCheckFields:
    def test_valid_payload(self) -> None:
        result = check_fields(valid_field_payload())
        assert result["ok"] is True

    def test_missing_owner(self) -> None:
        result = check_fields(valid_field_payload(owner=""))
        assert result["ok"] is False
        assert any(i["field"] == "著作权人" for i in result["issues"])

    def test_short_name_wu_rejected(self) -> None:
        result = check_fields(valid_field_payload(short_name="无"))
        assert any(i["field"] == "软件简称" for i in result["issues"])

    def test_issues_carry_authority(self) -> None:
        result = check_fields(valid_field_payload(purpose="短"))
        assert all("authority" in i for i in result["issues"])


class TestExplainRules:
    def test_mentions_fifty_lines(self) -> None:
        rules = explain_rules()
        assert rules["lines_per_page"]["value"] == 50

    def test_states_authority(self) -> None:
        rules = explain_rules()
        assert "第十条" in rules["lines_per_page"]["authority"]

    def test_notes_why_fifty_five_fails(self) -> None:
        rules = explain_rules()
        assert "55" in rules["lines_per_page"]["note"]

    def test_comments_are_not_claimed_as_law(self) -> None:
        """Comments are practice, not statute; saying otherwise would be false."""
        rules = explain_rules()
        assert "未明文" in rules["comments"]["authority"]


class TestServerConstruction:
    def test_server_builds(self) -> None:
        pytest.importorskip("mcp")
        assert create_server() is not None


class TestFieldsPayload:
    def test_dev_tools_composed_from_os_and_ide(self) -> None:
        fields = ApplicationFields()
        from hag.server import _fields_from_payload

        parsed = _fields_from_payload({"dev_os": "macOS", "dev_ide": "Xcode"})
        assert parsed.dev_tools == "开发环境: macOS/开发工具: Xcode"
        _ = fields

    def test_explicit_dev_tools_wins(self) -> None:
        from hag.server import _fields_from_payload

        parsed = _fields_from_payload({"dev_tools": "开发环境: Linux/开发工具: Vim"})
        assert parsed.dev_tools == "开发环境: Linux/开发工具: Vim"
