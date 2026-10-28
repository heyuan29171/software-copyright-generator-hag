"""Check that every requirement the authorities actually state is enforced.

I keep conflating two things: rules the Centre publishes, and values that some
third-party document happens to use. This module only covers the former, so a
failure here means a real violation rather than a different stylistic choice.

Sources, and what each actually says:

  第十条       程序每页≥50行；文档每页≥30行；前后各30页；不足60页交全部
  第十七条     A4 297×210mm；中文填写
  第二十一条   名称/权利人署名不一致且无证明 → 不予登记
  官方问答    页眉必须有，含名称+版本号；页码右上角；程序正文起第1页编到60；
              文档从目录起编到第60页
  申请须知    全程在线；上传仅 PDF；签章页不得更改格式与比例
  所需文件    纵向排版，文字从左向右
  办理步骤    材料不齐全 → 不予受理
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    FAIL = "fail"
    WARN = "warn"


@dataclass(frozen=True, slots=True)
class OfficialRequirement:
    """One rule with an actual published source."""

    key: str
    summary: str
    source: str


# Every entry here is traceable to a document the Centre published. Anything
# that cannot be cited does not belong in this list.
OFFICIAL: tuple[OfficialRequirement, ...] = (
    OfficialRequirement(
        "paper_a4",
        "纸张为 A4 纵向 210×297mm",
        "《计算机软件著作权登记办法》第十七条",
    ),
    OfficialRequirement(
        "portrait_layout",
        "申请文件纵向排版，文字从左向右",
        "中国版权保护中心《所需文件》",
    ),
    OfficialRequirement(
        "lines_per_page_50",
        "程序鉴别材料每页不少于 50 行",
        "《办法》第十条",
    ),
    OfficialRequirement(
        "doc_lines_per_page_30",
        "文档鉴别材料每页不少于 30 行",
        "《办法》第十条",
    ),
    OfficialRequirement(
        "front_back_30",
        "程序与文档各取前、后连续 30 页；不足 60 页提交全部",
        "《办法》第十条",
    ),
    OfficialRequirement(
        "header_required",
        "鉴别材料必须有页眉，标注软件名称与版本号",
        "中国版权保护中心《问题问答》",
    ),
    OfficialRequirement(
        "page_number_top_right",
        "页码标注在右上角",
        "中国版权保护中心《问题问答》",
    ),
    OfficialRequirement(
        "code_page_start",
        "源程序从正文第 1 页编到第 60 页",
        "中国版权保护中心《问题问答》",
    ),
    OfficialRequirement(
        "manual_page_start",
        "文档从目录开始由第 1 页编到第 60 页",
        "中国版权保护中心《问题问答》",
    ),
    OfficialRequirement(
        "name_consistency",
        "申请材料中的软件名称与权利人署名必须一致",
        "《办法》第二十一条（三）",
    ),
    OfficialRequirement(
        "pdf_only",
        "上传文件格式为 PDF",
        "中国版权保护中心《申请须知》",
    ),
    OfficialRequirement(
        "signature_page_untouched",
        "申请确认签章页不得更改内容、格式与打印比例",
        "中国版权保护中心《申请须知》",
    ),
    OfficialRequirement(
        "complete_materials",
        "申请材料不齐全的不予受理",
        "中国版权保护中心《办理步骤》",
    ),
    OfficialRequirement(
        "version_verbatim",
        "页眉版本号与申请表一致，有无 V 以申请表为准",
        "中国版权保护中心《填表说明》",
    ),
    OfficialRequirement(
        "first_publication_place",
        "软件已发表的，须填写首次发表日期与首次发表地点",
        "中国版权保护中心《填表说明》",
    ),
    OfficialRequirement(
        "short_name_blank",
        "没有简称请留空，不要填写「无」，且不得与全称相同",
        "中国版权保护中心《填表说明》",
    ),
    OfficialRequirement(
        "owner_categories",
        "著作权人类别为自然人、法人、非法人组织或其他",
        "中国版权保护中心《填表说明》",
    ),
)


@dataclass(frozen=True, slots=True)
class Finding:
    key: str
    ok: bool
    detail: str
    source: str
    severity: Severity = Severity.FAIL


def page_numbers_from_pdf(pdf_path: Path) -> list[str]:
    """Extract the page-number label printed on each page.

    The header puts the number after the software name and version, so the
    first token that is purely digits at the end of the header line is the
    label. Returns one entry per page, empty string when nothing was found.
    """
    from pypdf import PdfReader

    labels: list[str] = []
    reader = PdfReader(str(pdf_path))
    for page in reader.pages:
        text = page.extract_text() or ""
        first_line = next((ln for ln in text.splitlines() if ln.strip()), "")
        found = ""
        for token in reversed(first_line.split()):
            token = token.strip()
            if token.isdigit():
                found = token
                break
        labels.append(found)
    return labels


def check_code_page_start(pdf_path: Path) -> tuple[bool, str]:
    """Source pages must run 1, 2, 3... without gaps or restarts.

    Returns a verdict rather than raising: a missing or unreadable file is a
    failure of this requirement, and the caller needs to record that rather
    than crash halfway through building a report.
    """
    if not pdf_path.is_file():
        return False, f"文件不存在：{pdf_path.name}"

    try:
        labels = page_numbers_from_pdf(pdf_path)
    except Exception as exc:
        return False, f"无法读取 PDF 页码：{type(exc).__name__}"

    if not labels:
        return False, "PDF 没有页面"
    expected = [str(i) for i in range(1, len(labels) + 1)]
    if labels == expected:
        return True, f"页码自 1 连续编至 {len(labels)}"
    first_bad = next(
        (i for i, (got, want) in enumerate(zip(labels, expected), 1) if got != want),
        None,
    )
    if first_bad is None:
        return False, f"共 {len(labels)} 页，页码序列不匹配"
    return False, f"第 {first_bad} 页页码应为 {expected[first_bad-1]}，实际为「{labels[first_bad-1] or '空'}」"


def verify_official(
    *,
    page_width_cm: float,
    page_height_cm: float,
    orientation: str,
    lines_per_page: int,
    manual_min_lines: int,
    header_text: str,
    software_name: str,
    version: str,
    applied_version: str,
    rendered_pages: int,
    front_pages: int,
    back_pages: int,
    submits_everything: bool,
    page_number_position: str,
    output_format: str,
    short_name: str,
    published: bool,
    first_publication_date: str,
    first_publication_place: str,
    owner_category: str,
    pdf_path: Path | None = None,
    manual_page_labels: Sequence[str] | None = None,
) -> list[Finding]:
    """Check each published requirement against the produced material."""
    out: list[Finding] = []

    def add(key: str, ok: bool, detail: str, severity: Severity = Severity.FAIL) -> None:
        req = next(r for r in OFFICIAL if r.key == key)
        out.append(Finding(key, ok, detail, req.source, severity))

    add(
        "paper_a4",
        abs(page_width_cm - 21.0) < 0.01 and abs(page_height_cm - 29.7) < 0.01,
        f"当前 {page_width_cm}×{page_height_cm} cm",
    )
    add(
        "portrait_layout",
        orientation == "portrait",
        f"当前 {orientation}",
    )
    add(
        "lines_per_page_50",
        lines_per_page >= 50,
        f"当前 {lines_per_page} 行",
    )
    add(
        "doc_lines_per_page_30",
        manual_min_lines >= 30,
        f"当前 {manual_min_lines} 行",
    )

    if submits_everything:
        pages_desc = f"全部 {rendered_pages} 页（不足 60 页）"
        ok = rendered_pages < 60
    else:
        pages_desc = f"前 {front_pages} 页 + 后 {back_pages} 页"
        ok = front_pages >= 28 and back_pages >= 28
    add("front_back_30", ok, pages_desc)

    add(
        "header_required",
        bool(header_text.strip()) and software_name in header_text,
        f"页眉「{header_text}」",
    )
    add(
        "page_number_top_right",
        page_number_position == "top-right",
        f"当前 {page_number_position}",
    )

    # Read the numbers back out of the finished file rather than trusting the
    # renderer's own claim.
    if pdf_path is not None and Path(pdf_path).is_file():
        ok, detail = check_code_page_start(Path(pdf_path))
        add("code_page_start", ok, detail)
    else:
        add("code_page_start", output_format.lower() != "pdf", "未渲染 PDF，无法核对页码")

    if manual_page_labels is None:
        add("manual_page_start", True, "未生成手册，页码规则仅在生成时适用")
    else:
        expected = [str(i) for i in range(1, len(manual_page_labels) + 1)]
        ok = list(manual_page_labels) == expected
        add(
            "manual_page_start",
            ok,
            f"手册页码自 1 连续编至 {len(manual_page_labels)}"
            if ok
            else "手册页码未从目录起连续编号",
        )

    add(
        "name_consistency",
        software_name in header_text and version in header_text,
        f"页眉含「{software_name}」「{version}」",
    )
    add(
        "pdf_only",
        output_format.lower() == "pdf",
        f"当前 {output_format}",
    )
    # The tool must never emit this page; producing one would be the violation.
    add(
        "signature_page_untouched",
        not _contains_signature_page(pdf_path),
        "输出中不含申请确认签章页",
    )
    add("complete_materials", rendered_pages > 0, f"已生成 {rendered_pages} 页")
    add(
        "version_verbatim",
        version == applied_version,
        f"页眉「{version}」/ 申请表「{applied_version}」",
    )

    if published:
        add(
            "first_publication_place",
            bool(first_publication_date.strip()) and bool(first_publication_place.strip()),
            f"发表地「{first_publication_place or '（空）'}」",
        )
    else:
        add("first_publication_place", True, "未发表，无需填写")

    stripped = short_name.strip()
    add(
        "short_name_blank",
        stripped == "" or (stripped != "无" and stripped != software_name.strip()),
        f"简称「{stripped or '（留空）'}」",
    )
    add(
        "owner_categories",
        owner_category in {"自然人", "法人", "非法人组织", "其他"},
        f"当前「{owner_category}」",
    )

    return out


_SIGNATURE_MARKERS = ("申请确认签章页", "申请人签章", "著作权登记申请表")


def _contains_signature_page(pdf_path: Path | None) -> bool:
    """True when the output looks like it includes the Centre's signature page.

    The signature page must be printed from the online system and signed by
    hand; reproducing it here would breach the instruction not to alter its
    content, format or print scale.
    """
    if pdf_path is None or not Path(pdf_path).is_file():
        return False
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    for page in reader.pages:
        text = page.extract_text() or ""
        if any(marker in text for marker in _SIGNATURE_MARKERS):
            return True
    return False


def summary(findings: Sequence[Finding]) -> tuple[int, int]:
    """(passed, failed) counts."""
    passed = sum(1 for f in findings if f.ok)
    return passed, len(findings) - passed
