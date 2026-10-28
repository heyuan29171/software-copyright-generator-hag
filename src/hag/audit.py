"""Checks that decide whether material may be submitted.

Every check carries the authority it enforces, so a failure message can say
which rule was broken rather than just "invalid". Checks return a verdict
instead of raising: the caller decides whether to abort, and the report has
to describe *all* problems rather than only the first one encountered.

Severity follows the Centre's own language. Material that is missing or wrong
is **not accepted** (不予受理), which is worse than a correction request, so
those are ``FAIL``. Anything merely likely to attract scrutiny is ``WARN``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Level(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"

    @property
    def rank(self) -> int:
        return {"pass": 0, "warn": 1, "fail": 2}[self.value]


@dataclass(frozen=True, slots=True)
class Check:
    """One verdict about one property of the material."""

    check_id: str
    level: Level
    name: str
    detail: str = ""
    authority: str = ""
    fix: str = ""

    @property
    def is_blocking(self) -> bool:
        return self.level is Level.FAIL


@dataclass(slots=True)
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> None:
        self.checks.append(check)

    def extend(self, checks: Iterable[Check]) -> None:
        self.checks.extend(checks)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.level is Level.FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.level is Level.WARN]

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def worst(self) -> Level:
        if not self.checks:
            return Level.PASS
        return max((c.level for c in self.checks), key=lambda lv: lv.rank)

    def to_markdown(self) -> str:
        lines = ["# 校验报告", ""]
        counts = {level: 0 for level in Level}
        for check in self.checks:
            counts[check.level] += 1
        lines.append(
            f"通过 {counts[Level.PASS]} 项，警告 {counts[Level.WARN]} 项，"
            f"失败 {counts[Level.FAIL]} 项。"
        )
        lines.append("")

        if not self.ok:
            lines.append("## 阻断项（须修正后才能提交）")
            lines.append("")
            for check in self.failures:
                lines.extend(_format_check(check))

        if self.warnings:
            lines.append("## 警告项")
            lines.append("")
            for check in self.warnings:
                lines.extend(_format_check(check))

        passed = [c for c in self.checks if c.level is Level.PASS]
        if passed:
            lines.append("## 通过项")
            lines.append("")
            for check in passed:
                lines.append(f"- [{check.check_id}] {check.name}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"


def _format_check(check: Check) -> list[str]:
    out = [f"### [{check.check_id}] {check.name}", ""]
    if check.detail:
        out.append(check.detail)
        out.append("")
    if check.authority:
        out.append(f"依据：{check.authority}")
        out.append("")
    if check.fix:
        out.append(f"处理：{check.fix}")
        out.append("")
    return out


# --- Individual checks --------------------------------------------------------


def check_page_geometry(rendered_pages: int, *, declared_pages: int | None = None) -> list[Check]:
    """A4 portrait and a plausible page count."""
    checks: list[Check] = []
    from decimal import Decimal

    from .layout import CODE_LAYOUT

    # Compare with Decimal, not float: Decimal("29.7") != 29.7 is True, so a
    # float literal here would report every document as the wrong size.
    if (
        CODE_LAYOUT.page_width_cm != Decimal("21.0")
        or CODE_LAYOUT.page_height_cm != Decimal("29.7")
    ):
        checks.append(
            Check(
                "I-3",
                Level.FAIL,
                "纸张不是 A4",
                f"当前 {CODE_LAYOUT.page_width_cm}cm x {CODE_LAYOUT.page_height_cm}cm",
                "《计算机软件著作权登记办法》第十七条",
                "恢复为 21cm x 29.7cm",
            )
        )
    else:
        checks.append(Check("I-3", Level.PASS, "纸张为 A4 纵向"))

    if rendered_pages <= 0:
        checks.append(
            Check("I-6", Level.FAIL, "渲染页数为 0", "未生成任何页面", "第十条", "重新生成材料")
        )
        return checks

    if declared_pages is not None:
        if declared_pages != rendered_pages:
            checks.append(
                Check(
                    "I-4",
                    Level.FAIL,
                    "申请表页数与实际页数不一致",
                    f"申请表填写 {declared_pages} 页，PDF 实际 {rendered_pages} 页",
                    "《计算机软件著作权登记办法》第二十一条（三）",
                    "以 PDF 实际页数回填申请表「页数」字段",
                )
            )
        else:
            checks.append(Check("I-4", Level.PASS, "申请表页数与实际页数一致"))

    return checks


def check_line_counts(
    page_line_counts: Sequence[int], lines_per_page: int
) -> list[Check]:
    """Every page but the last must hold the statutory minimum."""
    if not page_line_counts:
        return [Check("I-6", Level.FAIL, "没有可检查的页面")]

    short = [
        (index, count)
        for index, count in enumerate(page_line_counts, start=1)
        if count < lines_per_page and index != len(page_line_counts)
    ]
    if short:
        listed = "、".join(f"第 {i} 页 {c} 行" for i, c in short[:10])
        return [
            Check(
                "I-6",
                Level.FAIL,
                f"{len(short)} 页行数不足 {lines_per_page} 行",
                listed + ("…" if len(short) > 10 else ""),
                "《计算机软件著作权登记办法》第十条",
                "重新分页；只有末页允许不满",
            )
        ]

    checks = [Check("I-6", Level.PASS, f"每页行数均不少于 {lines_per_page} 行")]

    last = page_line_counts[-1]
    if len(page_line_counts) > 1 and last * 3 < lines_per_page * 2:
        checks.append(
            Check(
                "W-19",
                Level.WARN,
                f"末页仅 {last} 行，不足整页 2/3",
                "过短的末页容易被认定为凑页",
                "行业惯例（非条文）",
                "补充文件或调整截取范围",
            )
        )
    return checks


def check_header(header_left: str, software_name: str, version: str) -> list[Check]:
    """Header must exist and match the application form exactly."""
    checks: list[Check] = []

    if not header_left.strip():
        checks.append(
            Check(
                "I-11",
                Level.FAIL,
                "页眉为空",
                "页眉必须标注软件名称与版本号",
                "中国版权保护中心《问题问答》：鉴别材料需要有页眉",
                "填写软件全称与版本号后重新生成",
            )
        )
        return checks

    if software_name not in header_left:
        checks.append(
            Check(
                "I-5",
                Level.FAIL,
                "页眉软件名称与申请表不一致",
                f"页眉为「{header_left}」，申请表软件全称为「{software_name}」",
                "《计算机软件著作权登记办法》第二十一条（三）",
                "以申请表的软件全称为准重新生成",
            )
        )

    if version not in header_left:
        checks.append(
            Check(
                "I-5",
                Level.FAIL,
                "页眉版本号与申请表不一致",
                f"页眉为「{header_left}」，申请表版本号为「{version}」",
                "《计算机软件著作权登记办法》第二十一条（三）",
                "以申请表的版本号为准重新生成",
            )
        )

    if not checks:
        checks.append(Check("I-5", Level.PASS, "页眉与申请表一致"))

    return checks


def check_version_consistency(versions: dict[str, str]) -> list[Check]:
    """The V prefix is decided by the form; everywhere else must match it exactly."""
    distinct = set(versions.values())
    if len(distinct) <= 1:
        return [Check("I-13", Level.PASS, "版本号全文一致")]

    listed = "；".join(f"{where}=「{value}」" for where, value in versions.items())
    return [
        Check(
            "I-13",
            Level.FAIL,
            "版本号写法不一致",
            listed,
            "中国版权保护中心《填表说明》：有无 V 以申请表中填报为准",
            "统一为申请表中的写法，包括是否带 V",
        )
    ]


def check_comment_residue(
    residues: Sequence[tuple[str, int]], partial_files: Sequence[str]
) -> list[Check]:
    """Comments must not remain in the material."""
    checks: list[Check] = []

    if residues:
        listed = "、".join(f"{path}:{line}" for path, line in residues[:10])
        checks.append(
            Check(
                "I-9",
                Level.FAIL,
                f"{len(residues)} 处注释残留",
                listed + ("…" if len(residues) > 10 else ""),
                "中国版权保护中心审查惯例（非法条）",
                "重新剔除注释；检查剔除器是否误判",
            )
        )
    else:
        checks.append(Check("I-9", Level.PASS, "未发现注释残留"))

    if partial_files:
        checks.append(
            Check(
                "W-15",
                Level.WARN,
                f"{len(partial_files)} 个文件仅剔除了整行注释",
                "、".join(partial_files[:10]) + ("…" if len(partial_files) > 10 else ""),
                "剔除器的已知限制",
                "人工复核这些文件的行尾注释",
            )
        )

    return checks


def check_input_completeness(stated: dict[str, str], required: Sequence[str]) -> list[Check]:
    """Missing application fields cause non-acceptance, not a correction request."""
    missing = [name for name in required if not stated.get(name, "").strip()]
    if not missing:
        return [Check("I-18", Level.PASS, "必填字段均已填写")]

    return [
        Check(
            "I-18",
            Level.FAIL,
            f"{len(missing)} 个必填字段为空",
            "、".join(missing),
            "中国版权保护中心《办理步骤》：申请材料不齐全的不予受理",
            "补齐后重新生成",
        )
    ]


def check_placement_guards(found: Sequence[tuple[str, str]]) -> list[Check]:
    """Placeholder text must not reach the submitted material."""
    if not found:
        return [Check("I-17", Level.PASS, "未发现占位内容")]

    listed = "；".join(f"{name}=「{value}」" for name, value in found[:10])
    return [
        Check(
            "I-17",
            Level.FAIL,
            f"{len(found)} 个字段仍是占位内容",
            listed,
            "材料完整性",
            "替换为真实值",
        )
    ]


def check_output_files(paths: Sequence[Path]) -> list[Check]:
    """Every promised artifact must exist and be non-empty."""
    missing = [p for p in paths if not p.is_file()]
    empty = [p for p in paths if p.is_file() and p.stat().st_size == 0]

    if missing or empty:
        parts = []
        if missing:
            parts.append("缺失：" + "、".join(p.name for p in missing))
        if empty:
            parts.append("为空：" + "、".join(p.name for p in empty))
        return [
            Check(
                "I-15",
                Level.FAIL,
                "正式资料不完整",
                "；".join(parts),
                "中国版权保护中心《办理步骤》：申请材料不齐全的不予受理",
                "重新生成缺失的文件",
            )
        ]

    return [Check("I-15", Level.PASS, "正式资料齐全且非空")]
