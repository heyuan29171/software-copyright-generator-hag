"""MCP server exposing the material generator as callable tools.

The tools are deliberately separate rather than one do-everything entry point,
because several steps need a human decision in between. An agent can inspect a
project, propose which files to include, and only then generate material. A
single black-box call would have to make those decisions silently.

Every tool returns a plain dictionary. Failures are reported as data with
``ok: false`` and a reason, not raised, because an MCP client needs to show the
user what went wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .application import (
    ApplicationFields,
    DevelopmentMode,
    PublicationStatus,
    RightsScope,
    SoftwareCategory,
    WorkType,
    format_dev_tools,
    validate,
)
from .build import build_materials
from .discovery import discover, summarize_by_suffix
from .layout import LINES_PER_PAGE
from .pipeline import PipelineError

try:  # pragma: no cover - exercised only when the SDK is present
    from mcp.server.mcpserver import MCPServer
except ImportError:  # pragma: no cover
    try:  # mcp 1.x used a different module layout
        from mcp.server.fastmcp import FastMCP as MCPServer  # type: ignore[assignment]
    except ImportError:
        MCPServer = None  # type: ignore[assignment,misc]


TOOL_DESCRIPTIONS = {
    "inspect_project": (
        "Scan a project and report what would be submitted: detected languages, "
        "candidate files with their usable line counts, and every skipped file "
        "with the reason. Call this first to see whether the project has enough "
        "material and to understand what was excluded."
    ),
    "estimate_material": (
        "Estimate how the selected files would be split into identifying "
        "material, without rendering. Reports the front and back page counts and "
        "warns when an exact 30+30 split is not reachable."
    ),
    "generate_materials": (
        "Generate the source-code PDF, the application form values and a "
        "verification report into an output directory. Returns ok=false with the "
        "blocking reasons when the material must not be submitted. Pages are "
        "drawn from whichever files contain the least Chinese; set drop_cjk=true "
        "to also delete Chinese-only lines, which alters the source and is "
        "therefore off by default."
    ),
    "check_fields": (
        "Validate application form values against the published rules without "
        "generating anything. Use this to fix fields before a long generation run."
    ),
    "explain_rules": (
        "Return the layout rules and their authority, so the answer to 'why 50 "
        "lines' or 'why is the page number top right' is traceable."
    ),
    "review_manual": (
        "Check a manual outline against the required structure before writing it "
        "out: the six chapters in order, FAQ and glossary last, a reason and "
        "evidence for any project-specific chapter, and continuous figure "
        "numbering. Returns the problems found and the page budget."
    ),
}


def _fields_from_payload(payload: dict[str, Any]) -> ApplicationFields:
    """Build ApplicationFields from loosely-typed tool input."""
    fields = ApplicationFields()

    mapping = {
        "software_name": "software_name",
        "short_name": "short_name",
        "version": "version",
        "development_completed": "development_completed",
        "first_publication_date": "first_publication_date",
        "first_publication_place": "first_publication_place",
        "owner": "owner",
        "owner_category": "owner_category",
        "owner_country": "owner_country",
        "owner_province": "owner_province",
        "owner_id_type": "owner_id_type",
        "owner_id_number": "owner_id_number",
        "dev_hardware": "dev_hardware",
        "run_hardware": "run_hardware",
        "dev_os": "dev_os",
        "run_platform": "run_platform",
        "run_support": "run_support",
        "languages": "languages",
        "source_line_count": "source_line_count",
        "purpose": "purpose",
        "domain": "domain",
        "main_functions": "main_functions",
        "tech_features": "tech_features",
        "page_count": "page_count",
    }
    for key, attr in mapping.items():
        if key in payload and payload[key] is not None:
            setattr(fields, attr, str(payload[key]))

    # Development tools is either given ready-made or composed from OS and IDE.
    if payload.get("dev_tools"):
        fields.dev_tools = str(payload["dev_tools"])
    elif payload.get("dev_os") or payload.get("dev_ide"):
        fields.dev_tools = format_dev_tools(
            str(payload.get("dev_os", "")), str(payload.get("dev_ide", ""))
        )

    if payload.get("category"):
        fields.category = SoftwareCategory(payload["category"])
    if payload.get("development_mode"):
        fields.development_mode = DevelopmentMode(payload["development_mode"])
    if payload.get("work_type"):
        fields.work_type = WorkType(payload["work_type"])
    if payload.get("publication_status"):
        fields.publication_status = PublicationStatus(payload["publication_status"])
    if payload.get("rights_scope"):
        fields.rights_scope = RightsScope(payload["rights_scope"])

    return fields


def inspect_project(project_root: str) -> dict[str, Any]:
    """Scan a project without modifying anything."""
    root = Path(project_root)
    if not root.is_dir():
        return {"ok": False, "error": f"not a directory: {project_root}"}

    result = discover(root)
    if not result.sources:
        return {
            "ok": False,
            "error": "no source files found",
            "skipped": [
                {"path": s.path.name, "reason": s.reason.value, "detail": s.detail}
                for s in result.skipped[:50]
            ],
        }

    summary = summarize_by_suffix(result.sources)
    return {
        "ok": True,
        "project_root": str(root),
        "source_files": len(result.sources),
        "total_bytes": result.total_bytes,
        "by_suffix": [
            {"suffix": suffix, "files": count, "bytes": size}
            for suffix, count, size in summary
        ],
        "skipped_count": len(result.skipped),
        "skipped": [
            {"path": s.path.name, "reason": s.reason.value, "detail": s.detail}
            for s in result.skipped[:100]
        ],
        "next": "call estimate_material to see how these files would be split",
    }


def estimate_material(
    project_root: str,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> dict[str, Any]:
    """Predict the page split without rendering."""
    from .pipeline import PipelineError, run_code_pipeline
    from .render_pdf import HeaderSpec

    root = Path(project_root)
    if not root.is_dir():
        return {"ok": False, "error": f"not a directory: {project_root}"}

    try:
        result = run_code_pipeline(
            root,
            root / "unused.pdf",
            HeaderSpec("placeholder", "V1.0"),
            include=include,
            exclude=exclude,
            render=False,
        )
    except PipelineError as exc:
        return {"ok": False, "error": str(exc)}

    selection = result.selection
    return {
        "ok": True,
        "mode": selection.mode.value,
        "title": selection.title,
        "front_pages": selection.front_pages,
        "back_pages": selection.back_pages,
        "rendered_pages": selection.rendered_pages,
        "total_lines": selection.total_lines,
        "lines_per_page": LINES_PER_PAGE,
        "exact_30_30": selection.exact,
        "warnings": list(selection.warnings),
        "files": [
            {"path": f.rel_path, "material_lines": f.line_count}
            for f in result.cleaned.files
        ],
        "skipped_partial": list(result.cleaned.skipped_partial),
    }


def generate_materials(
    project_root: str,
    software_name: str,
    version: str,
    output_dir: str | None = None,
    fields: dict[str, Any] | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    drop_cjk: bool = False,
) -> dict[str, Any]:
    """Generate the PDF, form values and report.

    By default the sixty pages are drawn from whichever files contain the least
    Chinese. `drop_cjk` additionally deletes lines that are Chinese prose with
    no code; that alters the source, so it is opt-in.
    """
    if not software_name.strip():
        return {"ok": False, "error": "software_name is required"}
    if not version.strip():
        return {"ok": False, "error": "version is required"}

    parsed = _fields_from_payload(fields or {})
    try:
        built = build_materials(
            Path(project_root),
            software_name,
            version,
            output_dir=Path(output_dir) if output_dir else None,
            fields=parsed,
            include=include,
            exclude=exclude,
            drop_cjk=drop_cjk,
        )
    except PipelineError as exc:
        return {"ok": False, "error": str(exc)}
    except OSError as exc:
        return {
            "ok": False,
            "error": f"文件系统错误：{exc.strerror or exc}",
            "hint": "检查输出目录是否可写，以及软件全称是否含有路径非法字符",
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    pipeline = built.pipeline
    return {
        "ok": built.ok,
        "output_dir": str(built.output_dir),
        "files": [str(p) for p in built.files],
        "rendered_pages": pipeline.rendered_pages if pipeline else 0,
        "front_pages": pipeline.selection.front_pages if pipeline else 0,
        "back_pages": pipeline.selection.back_pages if pipeline else 0,
        "title": pipeline.selection.title if pipeline else "",
        "blocking": [
            {"id": c.check_id, "name": c.name, "detail": c.detail, "fix": c.fix}
            for c in built.report.failures
        ],
        "warnings": [
            {"id": c.check_id, "name": c.name, "detail": c.detail}
            for c in built.report.warnings
        ],
        "report": str(built.output_dir / "校验报告.md"),
        "next": (
            "review 校验报告.md, then fill the online form using 申请表信息.txt"
            if built.ok
            else "fix the blocking items, then call generate_materials again"
        ),
    }


def check_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Validate form values without generating anything."""
    parsed = _fields_from_payload(fields)
    issues = validate(parsed)
    return {
        "ok": not issues,
        "issue_count": len(issues),
        "issues": [
            {
                "field": i.field_name,
                "message": i.message,
                "authority": i.authority,
                "blocking": i.blocking,
            }
            for i in issues
        ],
    }


def review_manual(
    software_name: str,
    version: str,
    chapters: list[dict[str, Any]],
    total_pages: int,
) -> dict[str, Any]:
    """Validate a manual outline and report the page budget.

    A chapter is a dict with ``title`` and ``body``; project-specific chapters
    additionally carry ``reason`` and ``evidence``, and any chapter may carry a
    ``figures`` list of ``{"index": 1, "caption": "..."}``.
    """
    from .manual import (
        Chapter,
        ChapterKind,
        Figure,
        Manual,
        ManualError,
        chapter_number,
        plan_pages,
        validate_structure,
    )

    manual = Manual(software_name=software_name, version=version)
    required = {
        "说明",
        "功能特点",
        "系统要求",
        "各功能页面/模块",
        "常见问题解答",
        "术语表",
    }

    for position, raw in enumerate(chapters, start=1):
        title = str(raw.get("title", "")).strip()
        kind = (
            ChapterKind.REQUIRED if title in required else ChapterKind.PROJECT_SPECIFIC
        )
        figures = [
            Figure(
                chapter_number=str(position),
                index=int(fig.get("index", 0)),
                caption=str(fig.get("caption", "")),
                image_path=fig.get("image_path"),
            )
            for fig in raw.get("figures", []) or []
        ]
        manual.chapters.append(
            Chapter(
                title=title,
                body=str(raw.get("body", "")),
                kind=kind,
                figures=figures,
                reason=str(raw.get("reason", "")),
                evidence=str(raw.get("evidence", "")),
            )
        )

    problems = validate_structure(manual)

    budget: dict[str, Any] = {}
    try:
        planned = plan_pages(total_pages)
        budget = {
            "total_pages": planned.total_pages,
            "submitted_pages": planned.submitted_pages,
            "front_pages": planned.front_pages,
            "back_pages": planned.back_pages,
            "submits_everything": planned.submits_everything,
            "note": planned.note,
        }
    except ManualError as exc:
        problems.append(str(exc))

    return {
        "ok": not problems,
        "problem_count": len(problems),
        "problems": problems,
        "chapters": [
            f"{chapter_number(i)}、{c.title}" for i, c in enumerate(manual.chapters, 1)
        ],
        "budget": budget,
    }


def explain_rules() -> dict[str, Any]:
    """State the layout rules and where each came from."""
    return {
        "lines_per_page": {
            "value": LINES_PER_PAGE,
            "authority": "《计算机软件著作权登记办法》第十条：程序每页不少于50行",
            "note": (
                "This is a floor, not a target. Raising it to 55 does not fit: "
                "A4 with 2cm margins gives 728.5pt of body area, and 55 lines at "
                "14pt need 770pt."
            ),
        },
        "pages": {
            "value": "front 30 + back 30",
            "authority": "第十条；不足60页提交全部",
            "note": "Split points must fall on file boundaries, never mid-function.",
        },
        "header": {
            "value": "left: software name + version; top right: page number",
            "authority": "中国版权保护中心《问题问答》",
            "note": "The version string is compared exactly, including the V.",
        },
        "comments": {
            "value": "removed",
            "authority": "行业实践（法规未明文规定）",
            "note": (
                "Some languages can only have whole-line comments removed; those "
                "files are reported as warnings for manual review."
            ),
        },
        "application_form": {
            "value": "values only, never the form itself",
            "authority": "中国版权保护中心《申请须知》《办理步骤》",
            "note": (
                "The form is filled in online and the signature page must be "
                "printed and signed by the applicant."
            ),
        },
    }


def create_server() -> Any:
    """Build the MCP server, or raise if the SDK is unavailable."""
    if MCPServer is None:
        raise RuntimeError(
            "the mcp package is not installed; install it with: pip install mcp"
        )

    server = MCPServer("hag")

    for name, func in (
        ("inspect_project", inspect_project),
        ("estimate_material", estimate_material),
        ("generate_materials", generate_materials),
        ("check_fields", check_fields),
        ("review_manual", review_manual),
        ("explain_rules", explain_rules),
    ):
        server.tool(name=name, description=TOOL_DESCRIPTIONS[name])(func)

    return server


def main() -> None:
    """Entry point used by the ``hag`` console script."""
    create_server().run()


__all__ = [
    "check_fields",
    "create_server",
    "estimate_material",
    "explain_rules",
    "generate_materials",
    "inspect_project",
    "main",
    "review_manual",
]
