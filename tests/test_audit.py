"""Audit verdicts must be honest about severity and completeness.

A FAIL means the material would not be accepted; a WARN means it is likely to
attract scrutiny but could be submitted. Conflating the two either blocks
valid work or lets invalid work through.
"""

from __future__ import annotations

from pathlib import Path

from hag.audit import (
    Check,
    Level,
    Report,
    check_comment_residue,
    check_header,
    check_input_completeness,
    check_line_counts,
    check_output_files,
    check_page_geometry,
    check_placement_guards,
    check_version_consistency,
)
from hag.layout import LINES_PER_PAGE


class TestReport:
    def test_empty_report_is_ok(self) -> None:
        assert Report().ok
        assert Report().exit_code == 0

    def test_failure_blocks(self) -> None:
        report = Report()
        report.add(Check("X", Level.FAIL, "bad"))
        assert not report.ok
        assert report.exit_code == 1

    def test_warning_does_not_block(self) -> None:
        report = Report()
        report.add(Check("X", Level.WARN, "hmm"))
        assert report.ok
        assert report.exit_code == 0

    def test_failures_and_warnings_partitioned(self) -> None:
        report = Report()
        report.add(Check("A", Level.FAIL, "f"))
        report.add(Check("B", Level.WARN, "w"))
        report.add(Check("C", Level.PASS, "p"))
        assert len(report.failures) == 1
        assert len(report.warnings) == 1

    def test_worst_level(self) -> None:
        report = Report()
        report.add(Check("A", Level.PASS, "p"))
        report.add(Check("B", Level.WARN, "w"))
        assert report.worst() is Level.WARN
        report.add(Check("C", Level.FAIL, "f"))
        assert report.worst() is Level.FAIL

    def test_markdown_lists_blockers_first(self) -> None:
        report = Report()
        report.add(Check("P", Level.PASS, "ok"))
        report.add(Check("F", Level.FAIL, "broken", "detail"))
        text = report.to_markdown()
        assert text.index("阻断项") < text.index("通过项")
        assert "broken" in text
        assert "detail" in text

    def test_markdown_includes_authority_and_fix(self) -> None:
        report = Report()
        report.add(
            Check("F", Level.FAIL, "x", authority="第十条", fix="改小行数")
        )
        text = report.to_markdown()
        assert "第十条" in text
        assert "改小行数" in text


class TestPageGeometry:
    def test_matching_page_counts_pass(self) -> None:
        checks = check_page_geometry(60, declared_pages=60)
        assert all(c.level is Level.PASS for c in checks)

    def test_mismatch_fails(self) -> None:
        checks = check_page_geometry(62, declared_pages=60)
        failures = [c for c in checks if c.level is Level.FAIL]
        assert len(failures) == 1
        assert "62" in failures[0].detail

    def test_zero_pages_fails(self) -> None:
        checks = check_page_geometry(0)
        assert any(c.level is Level.FAIL for c in checks)


class TestLineCounts:
    def test_uniform_passes(self) -> None:
        checks = check_line_counts([50, 50, 50], LINES_PER_PAGE)
        assert all(c.level is Level.PASS for c in checks)

    def test_short_middle_page_fails(self) -> None:
        checks = check_line_counts([50, 30, 50], LINES_PER_PAGE)
        assert any(c.level is Level.FAIL for c in checks)

    def test_short_last_page_only_warns(self) -> None:
        checks = check_line_counts([50, 50, 20], LINES_PER_PAGE)
        assert not any(c.level is Level.FAIL for c in checks)
        assert any(c.level is Level.WARN for c in checks)

    def test_very_short_last_page_warns(self) -> None:
        checks = check_line_counts([50, 50, 3], LINES_PER_PAGE)
        assert any(c.level is Level.WARN for c in checks)

    def test_single_page_no_warning(self) -> None:
        checks = check_line_counts([10], LINES_PER_PAGE)
        assert not any(c.level is Level.WARN for c in checks)

    def test_empty_fails(self) -> None:
        checks = check_line_counts([], LINES_PER_PAGE)
        assert any(c.level is Level.FAIL for c in checks)


class TestHeader:
    def test_matching_header_passes(self) -> None:
        checks = check_header("MySystem V1.0", "MySystem", "V1.0")
        assert all(c.level is Level.PASS for c in checks)

    def test_empty_header_fails(self) -> None:
        checks = check_header("", "MySystem", "V1.0")
        assert any(c.level is Level.FAIL for c in checks)

    def test_wrong_name_fails(self) -> None:
        checks = check_header("OtherSystem V1.0", "MySystem", "V1.0")
        assert any(c.level is Level.FAIL and "名称" in c.name for c in checks)

    def test_wrong_version_fails(self) -> None:
        checks = check_header("MySystem V2.0", "MySystem", "V1.0")
        assert any(c.level is Level.FAIL and "版本号" in c.name for c in checks)


class TestVersionConsistency:
    def test_identical_passes(self) -> None:
        checks = check_version_consistency({"header": "V1.0", "form": "V1.0"})
        assert checks[0].level is Level.PASS

    def test_v_prefix_mismatch_fails(self) -> None:
        """V1.0 and 1.0 are not the same version to a reviewer."""
        checks = check_version_consistency({"header": "V1.0", "form": "1.0"})
        assert checks[0].level is Level.FAIL


class TestCommentResidue:
    def test_clean_passes(self) -> None:
        checks = check_comment_residue([], [])
        assert all(c.level is Level.PASS for c in checks)

    def test_residue_fails(self) -> None:
        checks = check_comment_residue([("a.py", 12)], [])
        assert any(c.level is Level.FAIL for c in checks)

    def test_partial_coverage_warns_only(self) -> None:
        checks = check_comment_residue([], ["a.sh"])
        assert not any(c.level is Level.FAIL for c in checks)
        assert any(c.level is Level.WARN for c in checks)


class TestCompleteness:
    def test_all_present_passes(self) -> None:
        checks = check_input_completeness({"a": "x", "b": "y"}, ["a", "b"])
        assert checks[0].level is Level.PASS

    def test_missing_field_fails(self) -> None:
        checks = check_input_completeness({"a": "x", "b": "  "}, ["a", "b"])
        assert checks[0].level is Level.FAIL
        assert "b" in checks[0].detail


class TestPlaceholders:
    def test_none_passes(self) -> None:
        assert check_placement_guards([])[0].level is Level.PASS

    def test_placeholder_fails(self) -> None:
        checks = check_placement_guards([("著作权人", "待用户确认")])
        assert checks[0].level is Level.FAIL


class TestOutputFiles:
    def test_all_present_passes(self, tmp_path: Path) -> None:
        a = tmp_path / "a.pdf"
        a.write_bytes(b"x")
        assert check_output_files([a])[0].level is Level.PASS

    def test_missing_fails(self, tmp_path: Path) -> None:
        checks = check_output_files([tmp_path / "nope.pdf"])
        assert checks[0].level is Level.FAIL

    def test_empty_fails(self, tmp_path: Path) -> None:
        a = tmp_path / "empty.pdf"
        a.write_bytes(b"")
        checks = check_output_files([a])
        assert checks[0].level is Level.FAIL
