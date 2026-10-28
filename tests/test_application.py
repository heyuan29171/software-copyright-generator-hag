"""Field validation must catch what actually causes a refused application.

The Centre publishes two rules that third-party tools keep breaking: a missing
short name is blank rather than the character 无, and the version string is
compared exactly including the V. Both are tested here.
"""

from __future__ import annotations

from hag.application import (
    ApplicationFields,
    PublicationStatus,
    SoftwareCategory,
    effective_len,
    validate,
)


def base_fields(**overrides: object) -> ApplicationFields:
    defaults = dict(
        software_name="学生信息管理系统",
        version="V1.0",
        development_completed="2025-03-01",
        owner="某某科技有限公司",
        owner_category="法人",
        dev_tools="开发环境: Windows 11/开发工具: Visual Studio Code",
        main_functions="本系统用于记录学生信息。" * 60,  # comfortably over 500 chars
        page_count="60",
        source_line_count="12345",
    )
    defaults.update(overrides)
    return ApplicationFields(**defaults)  # type: ignore[arg-type]


def messages(issues: list) -> str:
    return " | ".join(f"{i.field_name}:{i.message}" for i in issues)


class TestEffectiveLength:
    def test_ignores_whitespace(self) -> None:
        assert effective_len("a b\tc\n") == 3

    def test_counts_cjk(self) -> None:
        assert effective_len("学生系统") == 4


class TestRequiredFields:
    def test_complete_passes(self) -> None:
        assert validate(base_fields()) == []

    def test_missing_name(self) -> None:
        issues = validate(base_fields(software_name=""))
        assert any(i.field_name == "软件全称" for i in issues)

    def test_missing_owner(self) -> None:
        issues = validate(base_fields(owner="   "))
        assert any(i.field_name == "著作权人" for i in issues)


class TestShortName:
    def test_blank_is_correct(self) -> None:
        assert validate(base_fields(short_name="")) == []

    def test_the_character_wu_is_rejected(self) -> None:
        """The form says leave it blank, do not write 无."""
        issues = validate(base_fields(short_name="无"))
        assert any(i.field_name == "软件简称" for i in issues)

    def test_same_as_full_name_rejected(self) -> None:
        issues = validate(base_fields(short_name="学生信息管理系统"))
        assert any(i.field_name == "软件简称" for i in issues)

    def test_a_real_short_name_passes(self) -> None:
        assert validate(base_fields(short_name="学信管理")) == []


class TestPublication:
    def test_unpublished_needs_nothing(self) -> None:
        assert validate(base_fields()) == []

    def test_published_requires_date(self) -> None:
        issues = validate(
            base_fields(
                publication_status=PublicationStatus.PUBLISHED,
                first_publication_place="北京",
            )
        )
        assert any(i.field_name == "首次发表日期" for i in issues)

    def test_published_requires_place(self) -> None:
        """A field every third-party tool omits."""
        issues = validate(
            base_fields(
                publication_status=PublicationStatus.PUBLISHED,
                first_publication_date="2025-01-01",
            )
        )
        assert any(i.field_name == "首次发表地点" for i in issues)

    def test_published_complete_passes(self) -> None:
        assert (
            validate(
                base_fields(
                    publication_status=PublicationStatus.PUBLISHED,
                    first_publication_date="2025-01-01",
                    first_publication_place="北京市",
                )
            )
            == []
        )


class TestDates:
    def test_bad_format_rejected(self) -> None:
        issues = validate(base_fields(development_completed="2025/03/01"))
        assert any(i.field_name == "开发完成日期" for i in issues)

    def test_iso_accepted(self) -> None:
        assert validate(base_fields(development_completed="2025-03-01")) == []


class TestOwnerCategory:
    def test_four_categories_accepted(self) -> None:
        for category in ("自然人", "法人", "非法人组织", "其他"):
            assert validate(base_fields(owner_category=category)) == []

    def test_unknown_category_rejected(self) -> None:
        issues = validate(base_fields(owner_category="公司"))
        assert any(i.field_name == "著作权人类别" for i in issues)


class TestLengthLimits:
    def test_purpose_over_50(self) -> None:
        issues = validate(base_fields(purpose="目" * 51))
        assert any(i.field_name == "开发目的" for i in issues)

    def test_purpose_at_50(self) -> None:
        assert validate(base_fields(purpose="目" * 50)) == []

    def test_main_functions_under_500(self) -> None:
        issues = validate(base_fields(main_functions="短"))
        assert any(i.field_name == "软件的主要功能" for i in issues)

    def test_main_functions_over_1300(self) -> None:
        issues = validate(base_fields(main_functions="字" * 1301))
        assert any(i.field_name == "软件的主要功能" for i in issues)

    def test_main_functions_in_range(self) -> None:
        assert validate(base_fields(main_functions="字" * 600)) == []

    def test_whitespace_does_not_consume_capacity(self) -> None:
        # 50 visible characters padded with spaces still fits.
        value = " " + "a" * 50 + " "
        assert validate(base_fields(purpose=value)) == []

    def test_languages_over_120(self) -> None:
        issues = validate(base_fields(languages="Java," * 30))
        assert any(i.field_name == "编程语言" for i in issues)


class TestSourceLineCount:
    def test_digits_only(self) -> None:
        assert validate(base_fields(source_line_count="12345")) == []

    def test_rejects_unit_text(self) -> None:
        issues = validate(base_fields(source_line_count="12345行"))
        assert any(i.field_name == "源程序量" for i in issues)


class TestDevTools:
    def test_correct_shape_passes(self) -> None:
        assert validate(base_fields(dev_tools="开发环境: Windows 11/开发工具: IDEA")) == []

    def test_tech_stack_rejected(self) -> None:
        issues = validate(base_fields(dev_tools="React 18 + Vite"))
        assert any(i.field_name == "软件开发环境/开发工具" for i in issues)


class TestPlaceholders:
    def test_placeholder_detected(self) -> None:
        issues = validate(base_fields(owner="待用户确认"))
        assert any("占位" in i.message for i in issues)

    def test_clean_values_pass(self) -> None:
        assert validate(base_fields()) == []

    def test_real_name_containing_shili_is_accepted(self) -> None:
        """A company may legitimately contain 示例; rejecting it blocks valid work."""
        assert validate(base_fields(owner="示例科技有限公司")) == []

    def test_exact_shili_rejected(self) -> None:
        issues = validate(base_fields(owner="示例"))
        assert any("占位" in i.message for i in issues)

    def test_todo_prefix_rejected(self) -> None:
        issues = validate(base_fields(purpose="TODO: 补充开发目的"))
        assert any("占位" in i.message for i in issues)


class TestOrdering:
    def test_form_order_is_stable(self) -> None:
        fields = base_fields()
        order = [name for name, _ in fields.as_form_order()]
        assert order[0] == "软件全称"
        assert order[1] == "软件简称"
        assert order[2] == "版本号"
        assert order[-1] == "页数"
        assert "首次发表地点" in order

    def test_text_output_lists_values(self) -> None:
        text = base_fields().to_text()
        assert "学生信息管理系统" in text
        assert "V1.0" in text
        assert "软件全称" in text

    def test_blank_value_shown_explicitly(self) -> None:
        text = base_fields(short_name="").to_text()
        assert "（留空）" in text

    def test_category_default(self) -> None:
        assert base_fields().category is SoftwareCategory.APPLICATION
