"""The GUI's decisions must be testable without a display.

The window itself is not exercised here; what is checked is the logic that
decides what to send and how to read the answer back, because that is where a
desktop user could be told something untrue.
"""

from __future__ import annotations

from hag.gui import FormState, interpret_result, summarize_inspection


class TestFormState:
    def test_missing_required_lists_each_field(self) -> None:
        state = FormState()
        missing = state.missing_required()
        assert "项目目录" in missing
        assert "软件全称" in missing
        assert "著作权人" in missing

    def test_complete_state_has_no_missing(self) -> None:
        state = FormState(
            project_root="C:/proj",
            software_name="学生信息管理系统",
            version="V1.0",
            owner="某某科技有限公司",
            development_completed="2025-03-01",
        )
        assert state.missing_required() == []

    def test_published_requires_date_and_place(self) -> None:
        state = FormState(
            project_root="C:/proj",
            software_name="S",
            version="V1.0",
            owner="O",
            development_completed="2025-03-01",
            published=True,
        )
        missing = state.missing_required()
        assert "首次发表日期" in missing
        assert "首次发表地点" in missing

    def test_unpublished_does_not_require_publication_fields(self) -> None:
        state = FormState(
            project_root="C:/proj",
            software_name="S",
            version="V1.0",
            owner="O",
            development_completed="2025-03-01",
            published=False,
        )
        assert state.missing_required() == []

    def test_to_fields_shapes_payload(self) -> None:
        state = FormState(
            owner="O",
            owner_category="法人",
            development_completed="2025-03-01",
            dev_os="Windows 11",
            dev_ide="VS Code",
            purpose="管理",
            domain="教育",
            main_functions="功能" * 300,
            source_line_count="1000",
        )
        payload = state.to_fields()
        assert payload["owner"] == "O"
        assert payload["dev_os"] == "Windows 11"
        assert payload["publication_status"] == "未发表"

    def test_to_fields_includes_publication_when_published(self) -> None:
        state = FormState(
            owner="O",
            development_completed="2025-03-01",
            published=True,
            first_publication_date="2025-01-01",
            first_publication_place="北京",
        )
        payload = state.to_fields()
        assert payload["publication_status"] == "已发表"
        assert payload["first_publication_place"] == "北京"

    def test_unpublished_omits_publication_fields(self) -> None:
        state = FormState(owner="O", development_completed="2025-03-01", published=False)
        payload = state.to_fields()
        assert "first_publication_date" not in payload


class TestInterpretSuccess:
    def test_success_reports_pages_and_next_step(self) -> None:
        result = {
            "ok": True,
            "rendered_pages": 60,
            "output_dir": "D:/out",
            "warnings": [],
        }
        outcome = interpret_result(result)
        assert outcome.ok
        assert "60" in outcome.summary
        assert "申请表" in outcome.summary

    def test_success_does_not_claim_to_submit(self) -> None:
        """The tool never submits; the summary must not imply it did."""
        result = {"ok": True, "rendered_pages": 60, "output_dir": "x", "warnings": []}
        summary = interpret_result(result).summary
        assert "不会代填" in summary or "签章" in summary

    def test_warnings_surfaced_but_not_blocking(self) -> None:
        result = {
            "ok": True,
            "rendered_pages": 60,
            "output_dir": "x",
            "warnings": [{"name": "末页较短", "detail": "31 行"}],
        }
        outcome = interpret_result(result)
        assert outcome.ok
        assert outcome.warnings


class TestInterpretFailure:
    def test_blocking_items_extracted(self) -> None:
        result = {
            "ok": False,
            "blocking": [{"name": "页数不一致", "detail": "60 对 62"}],
            "warnings": [],
            "output_dir": "x",
        }
        outcome = interpret_result(result)
        assert not outcome.ok
        assert outcome.blocking

    def test_error_without_blocking_still_reported(self) -> None:
        result = {"ok": False, "error": "软件全称含有非法字符"}
        outcome = interpret_result(result)
        assert not outcome.ok
        assert "非法字符" in outcome.blocking[0]

    def test_failure_message_asks_for_a_fix(self) -> None:
        result = {"ok": False, "error": "x", "blocking": []}
        outcome = interpret_result(result)
        assert "修改" in outcome.summary or "重试" in outcome.summary


class TestInspectionSummary:
    def test_describes_found_files(self) -> None:
        result = {
            "ok": True,
            "source_files": 120,
            "total_bytes": 240 * 1024,
            "by_suffix": [{"suffix": ".ts", "files": 100, "bytes": 1}],
            "skipped_count": 8,
        }
        text = summarize_inspection(result)
        assert "120" in text
        assert ".ts" in text
        assert "8" in text

    def test_reports_scan_failure(self) -> None:
        text = summarize_inspection({"ok": False, "error": "not a directory"})
        assert "失败" in text

    def test_no_crash_without_suffixes(self) -> None:
        result = {"ok": True, "source_files": 0, "total_bytes": 0, "by_suffix": []}
        assert summarize_inspection(result)
