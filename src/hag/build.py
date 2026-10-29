"""End-to-end artefact generation.

Wraps the pipeline and the audit into one call that produces a directory a
user can submit from, and a report describing what it contains and what still
needs doing by a human.

The ordering matters. The audit runs against the rendered artefacts, so the
page count it checks is the one a reviewer would count, not an estimate. If the
audit blocks, the files are still written — a user needs to see them to fix the
problem — but the result says clearly that it must not be submitted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .application import ApplicationFields, validate
from .audit import (
    Check,
    Level,
    Report,
    check_cjk_line_density,
    check_content_suspicions,
    check_masking,
    check_page_edges,
    check_total_source_size,
    check_comment_residue,
    check_header,
    check_input_completeness,
    check_line_counts,
    check_output_files,
    check_page_geometry,
    check_placement_guards,
    check_version_consistency,
)
from .application import REQUIRED_FIELDS
from .layout import CODE_LAYOUT, MANUAL_MIN_LINES_PER_PAGE, MAX_CJK_LINES_PER_PAGE
from .official import verify_official
from .pipeline import PipelineError, PipelineResult, run_code_pipeline
from .render_pdf import HeaderSpec


DEFAULT_OUTPUT_DIRNAME = "软件著作权申请资料"

# Characters that cannot appear in a filename on Windows, plus the control
# range. A software name containing any of these cannot become a filename, so
# it has to be rejected before work starts rather than failing at write time.
_ILLEGAL_FILENAME_CHARS = frozenset('<>:"/\\|?*')


def _assert_filename_usable(path: Path) -> None:
    """Reject a software name that cannot legally become a filename."""
    name = path.name
    offending = sorted({ch for ch in name if ch in _ILLEGAL_FILENAME_CHARS})
    if offending:
        raise PipelineError(
            "软件全称含有不能在文件名中使用的字符："
            + " ".join(repr(ch) for ch in offending)
            + "。请改用不含这些字符的名称，或通过 output_dir 指定输出位置。"
        )
    if any(ord(ch) < 32 for ch in name):
        raise PipelineError("软件全称含有控制字符，无法作为文件名")
    if name in {".", ".."} or not name.strip():
        raise PipelineError("软件全称不能为空或仅由空白组成")


@dataclass(slots=True)
class BuildResult:
    output_dir: Path
    report: Report
    pipeline: PipelineResult | None
    files: list[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.report.ok


def build_materials(
    project_root: Path,
    software_name: str,
    version: str,
    *,
    output_dir: Path | None = None,
    fields: ApplicationFields | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    render: bool = True,
    drop_cjk: bool = False,
    mask_secrets: bool = True,
) -> BuildResult:
    """Generate the code material, the form values and a verification report."""
    if not software_name.strip():
        raise PipelineError("software_name is required; it appears in every header")
    if not version.strip():
        raise PipelineError("version is required; it appears in every header")

    root = Path(project_root).resolve()
    if not root.is_dir():
        raise PipelineError(f"project root is not a directory: {root}")

    out = Path(output_dir) if output_dir else root / DEFAULT_OUTPUT_DIRNAME
    if out.exists() and not out.is_dir():
        raise PipelineError(
            f"output path exists and is not a directory: {out}"
        )
    out.mkdir(parents=True, exist_ok=True)
    official = out / "正式资料"
    official.mkdir(exist_ok=True)

    header = HeaderSpec(software_name=software_name, version=version)
    pdf_path = official / f"{software_name}-代码.pdf"
    _assert_filename_usable(pdf_path)

    result: PipelineResult | None = None
    pipeline_error: str | None = None
    try:
        result = run_code_pipeline(
            root,
            pdf_path,
            header,
            include=include,
            exclude=exclude,
            render=render,
            drop_cjk=drop_cjk,
            applicant=fields.owner if fields is not None else "",
            mask_secrets=mask_secrets,
        )
    except PipelineError as exc:
        pipeline_error = str(exc)
    except OSError as exc:
        # Writing the PDF can fail for filesystem reasons that have nothing to
        # do with the project: a name the OS rejects, a locked file, a full
        # disk. Reporting success here would tell the user material exists when
        # it does not.
        pipeline_error = f"写入文件失败：{exc}"

    report = Report()

    if result is None:
        report.add(
            Check(
                "I-2",
                Level.FAIL,
                "材料生成失败",
                pipeline_error or "unknown failure",
                "生成前置条件",
                "按提示修正后重试",
            )
        )
        _write_report(out, report)
        return BuildResult(output_dir=out, report=report, pipeline=None)

    files = [result.pdf_path] if result.pdf_path else []

    report.add(
        Check(
            "W-22",
            Level.PASS,
            (
                "已删除纯中文行（会改变源码内容）"
                if drop_cjk
                else "已按中文占比挑选源码文件，未修改任何代码"
            ),
            (
                "可通过开关调整"
                if drop_cjk
                else "若仍需更少中文行，可开启「删除纯中文行」"
            ),
            "工程判断（非法规要求）",
        )
    )

    # A run that was asked to render but produced no file has not succeeded,
    # however clean the rest of the pipeline looked.
    if render and (result.pdf_path is None or not Path(result.pdf_path).is_file()):
        report.add(
            Check(
                "I-15",
                Level.FAIL,
                "PDF 未生成",
                "流程报告成功，但输出目录中没有代码材料 PDF",
                "中国版权保护中心《办理步骤》：材料不齐全的不予受理",
                "检查输出路径是否可写后重试",
            )
        )
        _write_report(out, report)
        return BuildResult(output_dir=out, report=report, pipeline=result, files=files)

    # Cross-check the artefacts.
    report.extend(
        check_page_geometry(result.rendered_pages, declared_pages=_declared_pages(fields))
    )
    if render:
        report.extend(
            check_line_counts(
                [len(p) for p in result.pages], result.selection.lines_per_page
            )
        )
        report.extend(check_cjk_line_density(result.pages, MAX_CJK_LINES_PER_PAGE))
        report.extend(
            check_page_edges(
                result.pages[0][0] if result.pages and result.pages[0] else "",
                result.pages[-1][-1] if result.pages and result.pages[-1] else "",
            )
        )
        if result.render and result.render.replaced_characters:
            report.add(
                Check(
                    "W-21",
                    Level.WARN,
                    f"{len(result.render.replaced_characters)} 个字符无法渲染，已替换为 ?",
                    "、".join(
                        f"U+{ord(c):04X}" for c in result.render.replaced_characters[:10]
                    ),
                    "字体不包含这些字形（通常是 emoji）",
                    "如需保留原字符，可改用包含 emoji 的字体",
                )
            )
    report.extend(check_header(header.left, software_name, version))

    # Practice-derived checks: they do not block a submission, but each one is
    # something a reviewer has been known to query.
    report.extend(check_total_source_size(result.selection.total_lines))
    suspicions = result.cleaned.suspicions
    report.extend(
        check_content_suspicions(
            [(f.path, f.line_no, f.text) for f in suspicions.foreign_companies],
            [(f.path, f.line_no, f.text) for f in suspicions.dates],
            [(f.path, f.line_no, f.text) for f in suspicions.old_years],
        )
    )
    report.extend(
        check_masking(
            result.cleaned.mask_report.total_replacements,
            result.cleaned.mask_report.summary(),
        )
    )
    # Compare what was drawn into the document against what the form will
    # carry. Both currently derive from the same argument, so this only fires
    # if a future change lets them drift apart — which is exactly the class of
    # bug that causes a refused application.
    form_version = fields.version if fields is not None and fields.version else version
    report.extend(
        check_version_consistency(
            {
                "页眉": header.version,
                "申请表": form_version,
            }
        )
    )
    report.extend(
        check_comment_residue(
            _comment_residue(result), result.cleaned.skipped_partial
        )
    )

    # Every rule the authorities actually publish, checked one by one. This is
    # kept separate from the design checks so that a failure here means a real
    # violation rather than a difference of opinion about layout.
    official_findings = verify_official(
        page_width_cm=float(CODE_LAYOUT.page_width_cm),
        page_height_cm=float(CODE_LAYOUT.page_height_cm),
        orientation=CODE_LAYOUT.orientation,
        lines_per_page=result.selection.lines_per_page,
        manual_min_lines=MANUAL_MIN_LINES_PER_PAGE,
        header_text=header.left,
        software_name=software_name,
        version=version,
        applied_version=form_version,
        rendered_pages=result.rendered_pages,
        front_pages=result.selection.front_pages,
        back_pages=result.selection.back_pages,
        submits_everything=result.selection.mode.value == "all",
        page_number_position="top-right",
        output_format="pdf" if result.pdf_path else "none",
        short_name=fields.short_name if fields else "",
        published=(
            fields.publication_status.value == "已发表" if fields else False
        ),
        first_publication_date=fields.first_publication_date if fields else "",
        first_publication_place=fields.first_publication_place if fields else "",
        owner_category=fields.owner_category if fields else "法人",
        pdf_path=result.pdf_path,
    )
    for finding in official_findings:
        if finding.ok:
            continue
        report.add(
            Check(
                f"OFFICIAL-{finding.key}",
                Level.FAIL,
                f"未满足官方要求：{finding.key}",
                finding.detail,
                finding.source,
                "按官方要求修正后重新生成",
            )
        )

    if fields is not None:
        merged = _merge_fields(fields, software_name, version, result.rendered_pages)
        issues = validate(merged)
        if issues:
            report.add(
                Check(
                    "I-14",
                    Level.FAIL,
                    f"申请表有 {len(issues)} 项问题",
                    "；".join(f"{i.field_name}: {i.message}" for i in issues[:8]),
                    "中国版权保护中心《办理步骤》：材料不齐全的不予受理",
                    "修正申请表信息后重新生成",
                )
            )
        else:
            report.add(Check("I-14", Level.PASS, "申请表字段校验通过"))

        stated = {name: value for name, value in merged.as_form_order()}
        report.extend(check_input_completeness(stated, REQUIRED_FIELDS))

        fields_path = official / "申请表信息.txt"
        fields_path.write_text(merged.to_text(), encoding="utf-8")
        files.append(fields_path)

    manifest_path = out / "代码提取清单.json"
    manifest_path.write_text(
        json.dumps(result.manifest(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    files.append(manifest_path)

    if files:
        report.extend(check_output_files(files))

    _write_report(out, report)
    return BuildResult(output_dir=out, report=report, pipeline=result, files=files)


def _declared_pages(fields: ApplicationFields | None) -> int | None:
    if fields is None or not fields.page_count.strip().isdigit():
        return None
    return int(fields.page_count.strip())


def _merge_fields(
    fields: ApplicationFields,
    software_name: str,
    version: str,
    rendered_pages: int,
) -> ApplicationFields:
    """Apply the values the material itself decides, rather than trusting input.

    The name and version come from the header, and the page count from the
    rendered file. Letting a caller supply these independently is how the
    "页数" field ends up disagreeing with the document.
    """
    fields.software_name = software_name
    fields.version = version
    if rendered_pages:
        fields.page_count = str(rendered_pages)
    return fields


def _comment_residue(result: PipelineResult) -> list[tuple[str, int]]:
    """Re-check cleaned material for comment syntax that should have gone.

    Files are re-checked **as a whole**, not line by line. A single line such
    as ``class Point:`` is not valid Python on its own, so stripping it in
    isolation makes the body-repair step insert ``pass`` and every such line
    would be reported as residue. That was a real false positive.

    This reuses the same stripper, so it cannot detect a fault in the stripper
    itself; it only catches lines that survived for another reason, such as a
    file whose suffix was not recognised.
    """
    from .comments import StripMethod, strip_comments

    residues: list[tuple[str, int]] = []
    for file in result.cleaned.files:
        # Files whose comments were never removable cannot have residue in the
        # sense of "should have been removed"; they are reported separately.
        if file.method is not StripMethod.FULL:
            continue
        rejoined = "\n".join(file.lines)
        again = strip_comments(rejoined, file.suffix)
        if again.removed_comment_lines:
            residues.append((file.rel_path, 1))
            continue
        again_lines = [line for line in again.text.splitlines() if line.strip()]
        if again_lines != [line for line in file.lines]:
            residues.append((file.rel_path, 1))
    return residues


def _write_report(out: Path, report: Report) -> None:
    (out / "校验报告.md").write_text(report.to_markdown(), encoding="utf-8")
