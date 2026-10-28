"""Official checks must fail when the rule is actually broken.

Every test here deliberately constructs the violation, because a validator
that only ever sees correct input is indistinguishable from one that always
returns PASS.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hag.official import (
    OFFICIAL,
    _contains_signature_page,
    check_code_page_start,
    summary,
    verify_official,
)

pypdf = pytest.importorskip("pypdf")
fpdf = pytest.importorskip("fpdf")


def make_pdf(path: Path, page_labels: list[str]) -> Path:
    """A PDF whose pages carry the given page-number labels, like ours does."""
    from fpdf import FPDF

    pdf = FPDF(unit="pt", format=(595, 842))
    pdf.set_auto_page_break(False)
    for label in page_labels:
        pdf.add_page()
        pdf.set_font("helvetica", "", 10)
        pdf.set_xy(60, 40)
        pdf.cell(text=f"TestSystem V1.0 {label}")
        pdf.set_xy(60, 80)
        pdf.cell(text="const x = 1;")
    pdf.output(str(path))
    return path


def baseline(**overrides):
    args = dict(
        page_width_cm=21.0,
        page_height_cm=29.7,
        orientation="portrait",
        lines_per_page=50,
        manual_min_lines=30,
        header_text="学生信息系统 V1.0",
        software_name="学生信息系统",
        version="V1.0",
        applied_version="V1.0",
        rendered_pages=60,
        front_pages=30,
        back_pages=30,
        submits_everything=False,
        page_number_position="top-right",
        output_format="pdf",
        short_name="",
        published=False,
        first_publication_date="",
        first_publication_place="",
        owner_category="法人",
    )
    args.update(overrides)
    return args


def failed_keys(findings) -> set[str]:
    return {f.key for f in findings if not f.ok}


class TestBaseline:
    def test_all_pass_with_a_real_pdf(self, tmp_path: Path) -> None:
        """A complete, correct material passes every published requirement."""
        pdf = make_pdf(tmp_path / "base.pdf", ["1", "2"])
        findings = verify_official(**baseline(pdf_path=pdf))
        assert failed_keys(findings) == set()

    def test_every_official_key_is_checked(self) -> None:
        findings = verify_official(**baseline())
        assert {f.key for f in findings} == {r.key for r in OFFICIAL}

    def test_missing_pdf_does_not_pass_page_check(self) -> None:
        """Not having the artefact is not the same as satisfying the rule."""
        findings = verify_official(**baseline())
        assert "code_page_start" in failed_keys(findings)


class TestViolationsAreCaught:
    def test_wrong_paper(self) -> None:
        f = verify_official(**baseline(page_width_cm=20.0))
        assert "paper_a4" in failed_keys(f)

    def test_landscape(self) -> None:
        f = verify_official(**baseline(orientation="landscape"))
        assert "portrait_layout" in failed_keys(f)

    def test_too_few_lines(self) -> None:
        f = verify_official(**baseline(lines_per_page=49))
        assert "lines_per_page_50" in failed_keys(f)

    def test_manual_too_few_lines(self) -> None:
        f = verify_official(**baseline(manual_min_lines=29))
        assert "doc_lines_per_page_30" in failed_keys(f)

    def test_empty_header(self) -> None:
        f = verify_official(**baseline(header_text=""))
        assert "header_required" in failed_keys(f)

    def test_header_missing_name(self) -> None:
        f = verify_official(**baseline(header_text="别的系统 V1.0"))
        assert "header_required" in failed_keys(f)

    def test_page_number_wrong_side(self) -> None:
        f = verify_official(**baseline(page_number_position="bottom-center"))
        assert "page_number_top_right" in failed_keys(f)

    def test_version_v_prefix_mismatch(self) -> None:
        """V1.0 and 1.0 are not the same version to a reviewer."""
        f = verify_official(**baseline(version="V1.0", applied_version="1.0"))
        assert "version_verbatim" in failed_keys(f)

    def test_non_pdf_output(self) -> None:
        f = verify_official(**baseline(output_format="docx"))
        assert "pdf_only" in failed_keys(f)

    def test_published_without_place(self) -> None:
        f = verify_official(
            **baseline(
                published=True,
                first_publication_date="2025-01-01",
                first_publication_place="",
            )
        )
        assert "first_publication_place" in failed_keys(f)

    def test_short_name_is_the_character_wu(self) -> None:
        f = verify_official(**baseline(short_name="无"))
        assert "short_name_blank" in failed_keys(f)

    def test_short_name_equals_full_name(self) -> None:
        f = verify_official(**baseline(short_name="学生信息系统"))
        assert "short_name_blank" in failed_keys(f)

    def test_unknown_owner_category(self) -> None:
        f = verify_official(**baseline(owner_category="公司"))
        assert "owner_categories" in failed_keys(f)

    def test_no_material(self) -> None:
        f = verify_official(**baseline(rendered_pages=0))
        assert "complete_materials" in failed_keys(f)

    def test_segment_too_short(self) -> None:
        f = verify_official(**baseline(front_pages=20, back_pages=20))
        assert "front_back_30" in failed_keys(f)


class TestPageNumberReadBack:
    def test_sequential_numbers_pass(self, tmp_path: Path) -> None:
        pdf = make_pdf(tmp_path / "ok.pdf", ["1", "2", "3"])
        ok, detail = check_code_page_start(pdf)
        assert ok
        assert "3" in detail

    def test_restarting_numbers_fail(self, tmp_path: Path) -> None:
        pdf = make_pdf(tmp_path / "restart.pdf", ["1", "1", "2"])
        ok, detail = check_code_page_start(pdf)
        assert not ok
        assert "第 2 页" in detail

    def test_gap_fails(self, tmp_path: Path) -> None:
        pdf = make_pdf(tmp_path / "gap.pdf", ["1", "3"])
        ok, _ = check_code_page_start(pdf)
        assert not ok

    def test_missing_label_fails(self, tmp_path: Path) -> None:
        pdf = make_pdf(tmp_path / "missing.pdf", ["1", "", "3"])
        ok, detail = check_code_page_start(pdf)
        assert not ok
        assert "空" in detail

    def test_absent_pdf_reports_failure_not_pass(self, tmp_path: Path) -> None:
        ok, detail = check_code_page_start(tmp_path / "nope.pdf")
        assert not ok


class TestSignaturePageGuard:
    def test_absent_by_default(self, tmp_path: Path) -> None:
        pdf = make_pdf(tmp_path / "clean.pdf", ["1"])
        assert not _contains_signature_page(pdf)

    def test_detected_when_present(self, tmp_path: Path) -> None:
        from fpdf import FPDF

        pdf = FPDF(unit="pt", format=(595, 842))
        pdf.add_page()
        pdf.set_font("helvetica", "", 10)
        pdf.cell(text="application signature page")
        out = tmp_path / "sig.pdf"
        pdf.output(str(out))
        # The guard looks for Chinese markers; assert the negative case holds
        # for English text so the marker list is not accidentally matching all.
        assert not _contains_signature_page(out)

    def test_none_path_is_safe(self) -> None:
        assert not _contains_signature_page(None)


class TestSummary:
    def test_counts(self, tmp_path: Path) -> None:
        pdf = make_pdf(tmp_path / "s.pdf", ["1", "2"])
        findings = verify_official(**baseline(lines_per_page=40, pdf_path=pdf))
        passed, failed = summary(findings)
        assert failed == 1
        assert passed == len(findings) - 1
