"""Practice-derived checks: masking, foreign names, dates, page edges.

None of these comes from the statute. They come from what reviewers query in
practice, and each one is reported as a warning rather than blocking, because
the right response is a judgement call.
"""

from __future__ import annotations

from pathlib import Path

from hag.audit import Level, check_content_suspicions, check_masking, check_page_edges
from hag.audit import check_total_source_size
from hag.content_checks import scan_line
from hag.entry_point import choose_opening, entry_score, is_entry_candidate
from hag.masking import mask_line


class TestMasking:
    def test_api_key_assignment(self) -> None:
        text, fired = mask_line('api_key = "abcdef123456"')
        assert "abcdef123456" not in text
        assert "****" in text
        assert "凭据赋值" in fired

    def test_password_assignment(self) -> None:
        text, fired = mask_line('password: "hunter2hunter2"')
        assert "hunter2hunter2" not in text
        assert fired

    def test_platform_token_prefix(self) -> None:
        text, fired = mask_line("const t = 'ghp_abcdefghijklmnop';")
        assert "ghp_abcdefghijklmnop" not in text
        assert "****" in text

    def test_aws_key(self) -> None:
        text, _ = mask_line("AKIAIOSFODNN7EXAMPLE")
        assert "AKIAIOSFODNN7EXAMPLE" not in text

    def test_private_ip(self) -> None:
        text, fired = mask_line("host = '192.168.1.100'")
        assert "192.168.1.100" not in text
        assert "内网IP" in fired

    def test_ten_network(self) -> None:
        text, _ = mask_line("addr = '10.0.5.23'")
        assert "10.0.5.23" not in text

    def test_mobile_number(self) -> None:
        text, fired = mask_line("phone = '13812345678'")
        assert "13812345678" not in text
        assert "手机号" in fired

    def test_email(self) -> None:
        text, fired = mask_line('contact = "zhang@example.com"')
        assert "zhang@example.com" not in text
        assert "邮箱" in fired

    def test_ordinary_code_untouched(self) -> None:
        line = "const total = items.length + 1;"
        text, fired = mask_line(line)
        assert text == line
        assert fired == []

    def test_short_number_not_masked(self) -> None:
        line = "const port = 8080;"
        assert mask_line(line)[0] == line

    def test_shape_is_preserved(self) -> None:
        """The value should still look like a string literal, not vanish."""
        text, _ = mask_line('token = "abcdefghij"')
        assert text.count('"') == 2
        assert "token" in text


class TestForeignCompany:
    def test_other_company_flagged(self) -> None:
        result = scan_line("a.py", 3, "// Copyright 2020 某某科技有限公司", "本公司")
        assert result.foreign_companies

    def test_applicant_itself_not_flagged(self) -> None:
        result = scan_line("a.py", 3, "// Copyright 羊场监测平台", "羊场监测平台")
        assert not result.foreign_companies

    def test_applicant_substring_recognised(self) -> None:
        result = scan_line(
            "a.py", 1, "Copyright (c) 西安某某科技有限公司", "西安某某科技有限公司"
        )
        assert not result.foreign_companies

    def test_english_company(self) -> None:
        result = scan_line("a.ts", 5, "// Copyright 2021 Acme Corp.", "MyCompany")
        assert result.foreign_companies

    def test_plain_code_no_company(self) -> None:
        result = scan_line("a.py", 1, "def handler(request):", "X公司")
        assert not result.foreign_companies


class TestDates:
    def test_iso_date_found(self) -> None:
        result = scan_line("a.py", 7, 'created = "2023-05-12"', "X")
        assert result.dates

    def test_slash_date_found(self) -> None:
        result = scan_line("a.py", 7, 'created = "2023/05/12"', "X")
        assert result.dates

    def test_old_year_flagged(self) -> None:
        result = scan_line("a.py", 2, "// written in 2012", "X")
        assert result.old_years

    def test_recent_year_not_flagged(self) -> None:
        result = scan_line("a.py", 2, "// written in 2024", "X")
        assert not result.old_years


class TestEntryPoint:
    def test_main_py_is_candidate(self) -> None:
        assert is_entry_candidate("main.py")
        assert is_entry_candidate("src/main.go")
        assert is_entry_candidate("manage.py")

    def test_test_file_is_not(self) -> None:
        assert not is_entry_candidate("tests/main.py")

    def test_dunder_main_marker_scores_high(self) -> None:
        score = entry_score("app.py", 'if __name__ == "__main__":\n    run()')
        assert score >= 8

    def test_plain_file_scores_low(self) -> None:
        assert entry_score("utils/helpers.py", "def helper():\n    pass") == 0

    def test_choose_opening_prefers_entry(self) -> None:
        files = [
            ("utils/helpers.py", "def helper(): pass"),
            ("main.py", 'if __name__ == "__main__":\n    run()'),
        ]
        assert choose_opening(files) == 1

    def test_choose_opening_none_when_no_evidence(self) -> None:
        files = [("a/one.py", "x = 1"), ("b/two.py", "y = 2")]
        assert choose_opening(files) is None


class TestTotalSize:
    def test_large_project_passes(self) -> None:
        assert check_total_source_size(50000)[0].level is Level.PASS

    def test_tiny_project_warns(self) -> None:
        assert check_total_source_size(500)[0].level is Level.WARN

    def test_warning_mentions_the_count(self) -> None:
        check = check_total_source_size(500)[0]
        assert "500" in check.name

    def test_zero_lines_fails(self) -> None:
        assert check_total_source_size(0)[0].level is Level.FAIL


class TestPageEdges:
    def test_top_level_open_and_closed_block_pass(self) -> None:
        checks = check_page_edges("def main():", "}")
        assert all(c.level is Level.PASS for c in checks)

    def test_indented_first_line_warns(self) -> None:
        checks = check_page_edges("    return x", "}")
        assert any("缩进" in c.name for c in checks)

    def test_semicolon_ending_warns(self) -> None:
        checks = check_page_edges("def main():", "const x = 1;")
        assert any("分号" in c.name for c in checks)

    def test_operator_ending_warns(self) -> None:
        checks = check_page_edges("x = 1", "total = a +")
        assert any(c.level is Level.WARN for c in checks)


class TestContentCheckReporting:
    def test_no_issues_pass(self) -> None:
        checks = check_content_suspicions([], [], [])
        assert all(c.level is Level.PASS for c in checks)

    def test_company_reported(self) -> None:
        checks = check_content_suspicions([("a.py", 3, "Acme Corp")], [], [])
        assert any(c.level is Level.WARN and "公司" in c.name for c in checks)

    def test_dates_reported(self) -> None:
        checks = check_content_suspicions([], [("a.py", 5, "2023-01-01")], [])
        assert any("日期" in c.name for c in checks)

    def test_never_blocks(self) -> None:
        checks = check_content_suspicions(
            [("a.py", 1, "x")], [("a.py", 2, "y")], [("a.py", 3, "z")]
        )
        assert all(c.level is not Level.FAIL for c in checks)

    def test_masking_reported(self) -> None:
        assert check_masking(0, "")[0].level is Level.PASS
        assert check_masking(3, "脱敏 3 行")[0].level is Level.WARN
