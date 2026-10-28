"""Application form fields.

The form itself cannot be generated: it is filled in on the Centre's website
and the signature page must be printed and signed by the applicant. What this
module produces is the *values*, so the user can copy them across, plus a
validation pass that catches the mistakes which cause non-acceptance.

Two rules from the published guidance are easy to get wrong and are enforced
here:

* A missing short name is an empty string, never the character 无.
* The version string is compared exactly, including whether it carries a V.
  The form decides that; everywhere else must follow.

Field length limits are marked as industry practice rather than official
limits. The live form sits behind real-name authentication and could not be
inspected, so claiming they are official would overstate the evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from .layout import FIELD_MAX, FIELD_RANGE, OWNER_CATEGORIES

FIELD_LIMIT_AUTHORITY = "行业实践（官方在线表单需实名登录，未能直接核对）"


class SoftwareCategory(str, Enum):
    APPLICATION = "应用软件"
    EMBEDDED = "嵌入式软件"
    MIDDLEWARE = "中间件"
    SYSTEM = "系统软件"
    OTHER = "其他"


class DevelopmentMode(str, Enum):
    INDEPENDENT = "单独开发"
    COOPERATIVE = "合作开发"
    COMMISSIONED = "委托开发"
    ASSIGNED = "下达任务开发"


class WorkType(str, Enum):
    ORIGINAL = "原创"
    MODIFIED = "修改（含翻译软件、合成软件）"


class PublicationStatus(str, Enum):
    PUBLISHED = "已发表"
    UNPUBLISHED = "未发表"


class RightsScope(str, Enum):
    ALL = "全部权利"
    PARTIAL = "部分权利"


class AcquisitionMode(str, Enum):
    ORIGINAL = "原始取得"
    SUCCESSIVE = "继受取得"


# Fields the material cannot be assembled without. Missing any of these is a
# non-acceptance, not a correction request.
REQUIRED_FIELDS: tuple[str, ...] = (
    "软件全称",
    "版本号",
    "著作权人",
    "开发完成日期",
)

# Markers that mean "this value was never filled in". These are matched as
# whole values or standalone markers, never as substrings: a real company name
# may legitimately contain 示例, and rejecting it would be a false positive
# that blocks valid work.
PLACEHOLDER_EXACT: frozenset[str] = frozenset(
    {
        "待用户确认",
        "待补充",
        "待确认",
        "TODO",
        "TBD",
        "N/A",
        "无",
        "示例",
        "Demo",
        "ACME",
        "XX",
    }
)

PLACEHOLDER_PREFIXES: tuple[str, ...] = (
    "待用户确认",
    "待补充",
    "TODO:",
    "TBD:",
)

_SHORT_NAME_FORBIDDEN = "无"


@dataclass(slots=True)
class FieldIssue:
    field_name: str
    message: str
    authority: str = FIELD_LIMIT_AUTHORITY
    blocking: bool = True


def effective_len(text: str) -> int:
    """Length as the form counts it: whitespace does not consume capacity.

    A single function used by both generation and validation, so they cannot
    disagree about whether a value fits.
    """
    return len("".join(text.split()))


@dataclass(slots=True)
class ApplicationFields:
    """The 25 values the user will type into the online form."""

    software_name: str = ""
    short_name: str = ""
    version: str = ""
    category: SoftwareCategory = SoftwareCategory.APPLICATION
    development_completed: str = ""
    development_mode: DevelopmentMode = DevelopmentMode.INDEPENDENT
    work_type: WorkType = WorkType.ORIGINAL
    publication_status: PublicationStatus = PublicationStatus.UNPUBLISHED
    first_publication_date: str = ""
    first_publication_place: str = ""
    owner: str = ""
    owner_category: str = "法人"
    owner_country: str = "中国"
    owner_province: str = ""
    owner_id_type: str = ""
    owner_id_number: str = ""
    rights_scope: RightsScope = RightsScope.ALL
    acquisition_mode: AcquisitionMode = AcquisitionMode.ORIGINAL
    dev_hardware: str = ""
    run_hardware: str = ""
    dev_os: str = ""
    dev_tools: str = ""
    run_platform: str = ""
    run_support: str = ""
    languages: str = ""
    source_line_count: str = ""
    purpose: str = ""
    domain: str = ""
    main_functions: str = ""
    tech_features: str = ""
    page_count: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    # --- derived views ------------------------------------------------------

    def as_form_order(self) -> list[tuple[str, str]]:
        """Fields in the order the online form presents them."""
        return [
            ("软件全称", self.software_name),
            ("软件简称", self.short_name),
            ("版本号", self.version),
            ("软件分类", self.category.value),
            ("开发完成日期", self.development_completed),
            ("开发方式", self.development_mode.value),
            ("软件说明", self.work_type.value),
            ("发表状态", self.publication_status.value),
            ("首次发表日期", self.first_publication_date),
            ("首次发表地点", self.first_publication_place),
            ("著作权人", self.owner),
            ("著作权人类别", self.owner_category),
            ("国籍", self.owner_country),
            ("省份/城市", self.owner_province),
            ("证件类型", self.owner_id_type),
            ("证件号码", self.owner_id_number),
            ("权利范围", self.rights_scope.value),
            ("权利取得方式", self.acquisition_mode.value),
            ("开发的硬件环境", self.dev_hardware),
            ("运行的硬件环境", self.run_hardware),
            ("开发该软件的操作系统", self.dev_os),
            ("软件开发环境/开发工具", self.dev_tools),
            ("运行平台/操作系统", self.run_platform),
            ("运行支撑环境/支持软件", self.run_support),
            ("编程语言", self.languages),
            ("源程序量", self.source_line_count),
            ("开发目的", self.purpose),
            ("面向领域/行业", self.domain),
            ("软件的主要功能", self.main_functions),
            ("软件的技术特点", self.tech_features),
            ("页数", self.page_count),
        ]

    def to_text(self) -> str:
        """Human-readable block for copying into the online form."""
        lines = ["软件著作权登记申请表信息", "=" * 20, ""]
        for name, value in self.as_form_order():
            shown = value if value else "（留空）"
            lines.append(f"{name}：{shown}")
        if self.extra:
            lines.append("")
            lines.append("其他")
            lines.append("-" * 20)
            for name, value in self.extra.items():
                lines.append(f"{name}：{value}")
        lines.append("")
        return "\n".join(lines)


def validate(fields: ApplicationFields) -> list[FieldIssue]:
    """Every problem with the current field values, not just the first."""
    issues: list[FieldIssue] = []

    # Required values.
    for name in REQUIRED_FIELDS:
        value = _value_of(fields, name)
        if not value.strip():
            issues.append(
                FieldIssue(
                    name,
                    "必填字段不能为空",
                    "中国版权保护中心《办理步骤》：材料不齐全的不予受理",
                )
            )

    # Short name rules. An absent short name is blank, not the character 无.
    if fields.short_name.strip():
        if fields.short_name.strip() == _SHORT_NAME_FORBIDDEN:
            issues.append(
                FieldIssue(
                    "软件简称",
                    "没有简称时应留空，不要填写「无」",
                    "中国版权保护中心《填表说明》",
                )
            )
        if fields.short_name.strip() == fields.software_name.strip():
            issues.append(
                FieldIssue(
                    "软件简称",
                    "软件简称不能与全称完全相同",
                    "中国版权保护中心《填表说明》",
                )
            )

    # Publication fields travel together.
    if fields.publication_status is PublicationStatus.PUBLISHED:
        if not fields.first_publication_date.strip():
            issues.append(
                FieldIssue(
                    "首次发表日期",
                    "已发表时必填",
                    "中国版权保护中心《填表说明》",
                )
            )
        if not fields.first_publication_place.strip():
            issues.append(
                FieldIssue(
                    "首次发表地点",
                    "已发表时必填（第三方工具普遍遗漏此项）",
                    "中国版权保护中心《填表说明》",
                )
            )

    # Dates.
    for name, value in (
        ("开发完成日期", fields.development_completed),
        ("首次发表日期", fields.first_publication_date),
    ):
        if value.strip() and not _is_iso_date(value.strip()):
            issues.append(FieldIssue(name, "日期格式应为 YYYY-MM-DD", "官方表单格式要求"))

    # Owner category is a four-way choice, not a boolean.
    if fields.owner_category not in OWNER_CATEGORIES:
        issues.append(
            FieldIssue(
                "著作权人类别",
                "应为：" + "、".join(OWNER_CATEGORIES),
                "中国版权保护中心《填表说明》",
            )
        )

    # Length limits.
    for name, limit in FIELD_MAX.items():
        value = _value_of(fields, name)
        if not value.strip():
            continue
        length = effective_len(value)
        if length > limit:
            issues.append(
                FieldIssue(name, f"超出 {limit} 字上限（当前 {length} 字）")
            )

    for name, (low, high) in FIELD_RANGE.items():
        value = _value_of(fields, name)
        length = effective_len(value)
        if not value.strip():
            continue
        if length < low:
            issues.append(FieldIssue(name, f"少于 {low} 字下限（当前 {length} 字）"))
        elif length > high:
            issues.append(FieldIssue(name, f"超出 {high} 字上限（当前 {length} 字）"))

    # Source line count is digits only, and is the whole program's total.
    if fields.source_line_count.strip():
        digits = fields.source_line_count.strip()
        if not digits.isdigit():
            issues.append(
                FieldIssue(
                    "源程序量",
                    "应为纯数字，不含「行」等单位",
                    "中国版权保护中心《填表说明》",
                )
            )

    if fields.page_count.strip() and not fields.page_count.strip().isdigit():
        issues.append(FieldIssue("页数", "应为纯数字"))

    # Development tools must describe the environment, not the stack.
    if fields.dev_tools.strip():
        framed = fields.dev_tools.strip()
        if not (framed.startswith("开发环境") and "开发工具" in framed):
            issues.append(
                FieldIssue(
                    "软件开发环境/开发工具",
                    "格式应为「开发环境: <操作系统>/开发工具: <IDE>」，不要填框架或技术栈",
                    "中国版权保护中心《填表说明》",
                )
            )

    # Placeholders must never reach the real material. Matched as whole values
    # so a legitimate name containing 示例 is not rejected.
    for name, value in fields.as_form_order():
        stripped = value.strip()
        if not stripped:
            continue
        if stripped in PLACEHOLDER_EXACT or stripped.startswith(PLACEHOLDER_PREFIXES):
            issues.append(
                FieldIssue(name, f"仍含占位内容「{stripped}」，须替换为真实值", "材料完整性")
            )

    return issues


def _value_of(fields: ApplicationFields, name: str) -> str:
    for candidate, value in fields.as_form_order():
        if candidate == name:
            return value
    return fields.extra.get(name, "")


def _is_iso_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def format_dev_tools(os_name: str, ide: str) -> str:
    """Compose the environment field in the shape the form expects.

    The form asks for the operating system and the IDE, not the framework or
    language stack used to build the software.
    """
    if not os_name.strip() and not ide.strip():
        return ""
    return f"开发环境: {os_name.strip()}/开发工具: {ide.strip()}"
