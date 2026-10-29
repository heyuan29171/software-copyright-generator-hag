"""Desktop interface for people who are not driving an agent.

The GUI is a thin shell over the same functions the MCP server exposes. It
holds no rules of its own: every validation, every refusal and every page
count comes from the library, so the desktop and the agent cannot drift apart.

Generation runs on a worker thread. A long project takes minutes, and blocking
the event loop would freeze the window with no sign of progress — the
complaint that usually follows is "it hung", not "it is working".
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .server import generate_materials, inspect_project

WINDOW_TITLE = "软著申请材料生成器 — by heyuan"
WINDOW_MIN_SIZE = (820, 640)


@dataclass
class FormState:
    """Values bound to the widgets, kept separate so the logic stays testable."""

    project_root: str = ""
    software_name: str = ""
    version: str = "V1.0"
    output_dir: str = ""
    owner: str = ""
    owner_category: str = "法人"
    development_completed: str = ""
    dev_os: str = ""
    dev_ide: str = ""
    purpose: str = ""
    domain: str = ""
    main_functions: str = ""
    source_line_count: str = ""
    published: bool = False
    first_publication_date: str = ""
    first_publication_place: str = ""

    def to_fields(self) -> dict[str, Any]:
        """Shape the values the way the generation call expects them."""
        payload: dict[str, Any] = {
            "owner": self.owner,
            "owner_category": self.owner_category,
            "development_completed": self.development_completed,
            "dev_os": self.dev_os,
            "dev_ide": self.dev_ide,
            "purpose": self.purpose,
            "domain": self.domain,
            "main_functions": self.main_functions,
            "source_line_count": self.source_line_count,
            "publication_status": "已发表" if self.published else "未发表",
        }
        if self.published:
            payload["first_publication_date"] = self.first_publication_date
            payload["first_publication_place"] = self.first_publication_place
        return payload

    def missing_required(self) -> list[str]:
        """Fields the user must fill before a run is worth starting."""
        missing: list[str] = []
        if not self.project_root.strip():
            missing.append("项目目录")
        if not self.software_name.strip():
            missing.append("软件全称")
        if not self.version.strip():
            missing.append("版本号")
        if not self.owner.strip():
            missing.append("著作权人")
        if not self.development_completed.strip():
            missing.append("开发完成日期")
        if self.published:
            if not self.first_publication_date.strip():
                missing.append("首次发表日期")
            if not self.first_publication_place.strip():
                missing.append("首次发表地点")
        return missing


@dataclass
class RunOutcome:
    ok: bool
    summary: str
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    output_dir: str = ""


def summarize_inspection(result: dict[str, Any]) -> str:
    """Short human summary of a project scan."""
    if not result.get("ok"):
        return f"扫描失败：{result.get('error', '未知原因')}"

    lines = [
        f"发现 {result.get('source_files', 0)} 个源文件",
        f"合计 {result.get('total_bytes', 0) / 1024:.0f} KB",
    ]
    by_suffix = result.get("by_suffix") or []
    if by_suffix:
        top = "、".join(
            f"{item['suffix']} {item['files']} 个" for item in by_suffix[:5]
        )
        lines.append(f"主要类型：{top}")
    skipped = result.get("skipped_count", 0)
    if skipped:
        lines.append(f"已跳过 {skipped} 项（依赖、构建产物、二进制等）")
    return "；".join(lines)


def interpret_result(result: dict[str, Any]) -> RunOutcome:
    """Turn the generation response into something a person can act on."""
    if not result.get("ok"):
        blocking = [
            f"{item.get('name', '')}：{item.get('detail', '')}".strip("：")
            for item in result.get("blocking", [])
        ]
        if not blocking and result.get("error"):
            blocking = [result["error"]]
        return RunOutcome(
            ok=False,
            summary="材料未通过校验，请按下列提示修改后重试。",
            blocking=blocking,
            warnings=[
                f"{item.get('name', '')}：{item.get('detail', '')}".strip("：")
                for item in result.get("warnings", [])
            ],
            output_dir=result.get("output_dir", ""),
        )

    pages = result.get("rendered_pages", 0)
    return RunOutcome(
        ok=True,
        summary=(
            f"已生成 {pages} 页代码材料，位于：{result.get('output_dir', '')}。"
            "\n下一步：打开「申请表信息.txt」，逐项填入官网在线表单。"
            "申请表需本人签章后扫描上传，程序不会代填或代签。"
        ),
        warnings=[
            f"{item.get('name', '')}：{item.get('detail', '')}".strip("：")
            for item in result.get("warnings", [])
        ],
        output_dir=result.get("output_dir", ""),
    )


class App:
    """The main window."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.state = FormState()
        self.queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None

        root.title(WINDOW_TITLE)
        root.minsize(*WINDOW_MIN_SIZE)
        root.geometry("900x720")

        self._build_ui()
        self.root.after(120, self._drain_queue)

    # --- construction -------------------------------------------------------

    def _build_ui(self) -> None:
        # Author and version sit at the top, above everything else: the window
        # is the first thing a buyer sees, and a product without an author on
        # it looks anonymous.
        from . import __author__, __version__

        header = ttk.Frame(self.root, padding=(12, 10, 12, 0))
        header.pack(fill="x")
        ttk.Label(
            header,
            text="软著申请材料生成器",
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(side="left")
        ttk.Label(
            header,
            text=f"v{__version__}　作者：{__author__}　联系：heyuan29171@outlook.com",
            foreground="#666666",
        ).pack(side="left", padx=(12, 0))

        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        self._build_project_section(outer)
        self._build_details_section(outer)
        self._build_actions(outer)
        self._build_output(outer)

        footer = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        footer.pack(fill="x")
        ttk.Label(
            footer,
            text="版权所有 (c) 2026 heyuan。本软件为专有软件，未经许可不得复制、分发或用于商业用途。",
            foreground="#999999",
        ).pack(side="left")

    def _build_project_section(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="1. 项目与输出", padding=10)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="项目目录").grid(row=0, column=0, sticky="w", pady=4)
        self.project_var = tk.StringVar()
        ttk.Entry(box, textvariable=self.project_var).grid(
            row=0, column=1, sticky="ew", padx=6
        )
        ttk.Button(box, text="浏览…", command=self._choose_project).grid(row=0, column=2)

        ttk.Label(box, text="输出目录").grid(row=1, column=0, sticky="w", pady=4)
        self.output_var = tk.StringVar()
        ttk.Entry(box, textvariable=self.output_var).grid(
            row=1, column=1, sticky="ew", padx=6
        )
        ttk.Button(box, text="浏览…", command=self._choose_output).grid(row=1, column=2)

        ttk.Label(
            box,
            text="留空则输出到项目目录下的「软件著作权申请资料」。",
            foreground="#666666",
        ).grid(row=2, column=1, sticky="w", padx=6)

    def _build_details_section(self, parent: ttk.Frame) -> None:
        notebook = ttk.Notebook(parent)
        notebook.pack(fill="both", expand=True, pady=(10, 0))

        basics = ttk.Frame(notebook, padding=10)
        notebook.add(basics, text="软件与著作权人")
        self._build_basics(basics)

        env = ttk.Frame(notebook, padding=10)
        notebook.add(env, text="环境与功能")
        self._build_environment(env)

    def _build_basics(self, box: ttk.Frame) -> None:
        box.columnconfigure(1, weight=1)

        self.name_var = tk.StringVar()
        self.version_var = tk.StringVar(value="V1.0")
        self.owner_var = tk.StringVar()
        self.owner_category_var = tk.StringVar(value="法人")
        self.completed_var = tk.StringVar()

        rows = [
            ("软件全称", self.name_var, "会写入每页页眉，须与申请表完全一致"),
            ("版本号", self.version_var, "V1.0 或 1.0 均可，但全文必须一致"),
            ("著作权人", self.owner_var, "填错会导致不予登记"),
        ]
        for row, (label, var, hint) in enumerate(rows):
            ttk.Label(box, text=label).grid(row=row * 2, column=0, sticky="w", pady=(6, 0))
            ttk.Entry(box, textvariable=var).grid(
                row=row * 2, column=1, sticky="ew", padx=6, pady=(6, 0)
            )
            ttk.Label(box, text=hint, foreground="#666666").grid(
                row=row * 2 + 1, column=1, sticky="w", padx=6
            )

        base = len(rows) * 2
        ttk.Label(box, text="著作权人类型").grid(row=base, column=0, sticky="w", pady=(10, 0))
        ttk.Combobox(
            box,
            textvariable=self.owner_category_var,
            values=["自然人", "法人", "非法人组织", "其他"],
            state="readonly",
        ).grid(row=base, column=1, sticky="w", padx=6, pady=(10, 0))

        ttk.Label(box, text="开发完成日期").grid(row=base + 1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(box, textvariable=self.completed_var).grid(
            row=base + 1, column=1, sticky="ew", padx=6, pady=(6, 0)
        )
        ttk.Label(box, text="格式 YYYY-MM-DD", foreground="#666666").grid(
            row=base + 2, column=1, sticky="w", padx=6
        )

        self.published_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            box,
            text="软件已发表（勾选后须填写发表日期与地点）",
            variable=self.published_var,
            command=self._toggle_publication,
        ).grid(row=base + 3, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self.pub_date_var = tk.StringVar()
        self.pub_place_var = tk.StringVar()
        self.pub_date_entry = ttk.Entry(box, textvariable=self.pub_date_var, state="disabled")
        self.pub_place_entry = ttk.Entry(box, textvariable=self.pub_place_var, state="disabled")

        ttk.Label(box, text="首次发表日期").grid(row=base + 4, column=0, sticky="w", pady=(6, 0))
        self.pub_date_entry.grid(row=base + 4, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Label(box, text="首次发表地点").grid(row=base + 5, column=0, sticky="w", pady=(6, 0))
        self.pub_place_entry.grid(row=base + 5, column=1, sticky="ew", padx=6, pady=(6, 0))

    def _build_environment(self, box: ttk.Frame) -> None:
        box.columnconfigure(1, weight=1)

        self.os_var = tk.StringVar()
        self.ide_var = tk.StringVar()
        self.purpose_var = tk.StringVar()
        self.domain_var = tk.StringVar()
        self.lines_var = tk.StringVar()

        simple = [
            ("开发操作系统", self.os_var, "例如 Windows 11"),
            ("开发工具", self.ide_var, "例如 Visual Studio Code"),
            ("开发目的", self.purpose_var, "不超过 50 字，不能只写软件名"),
            ("面向领域/行业", self.domain_var, "不超过 50 字"),
            ("源程序总行数", self.lines_var, "纯数字，不含「行」字"),
        ]
        for row, (label, var, hint) in enumerate(simple):
            ttk.Label(box, text=label).grid(row=row * 2, column=0, sticky="w", pady=(6, 0))
            ttk.Entry(box, textvariable=var).grid(
                row=row * 2, column=1, sticky="ew", padx=6, pady=(6, 0)
            )
            ttk.Label(box, text=hint, foreground="#666666").grid(
                row=row * 2 + 1, column=1, sticky="w", padx=6
            )

        base = len(simple) * 2
        ttk.Label(box, text="软件的主要功能").grid(row=base, column=0, sticky="nw", pady=(8, 0))
        self.functions_text = tk.Text(box, height=8, wrap="word")
        self.functions_text.grid(row=base, column=1, sticky="nsew", padx=6, pady=(8, 0))
        box.rowconfigure(base, weight=1)
        ttk.Label(box, text="要求 500–1300 字", foreground="#666666").grid(
            row=base + 1, column=1, sticky="w", padx=6
        )

    def _build_actions(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=10)

        self.scan_button = ttk.Button(bar, text="扫描项目", command=self._on_scan)
        self.scan_button.pack(side="left")

        self.env_button = ttk.Button(bar, text="环境检查", command=self._on_check_env)
        self.env_button.pack(side="left", padx=(8, 0))

        self.run_button = ttk.Button(bar, text="生成材料", command=self._on_generate)
        self.run_button.pack(side="left", padx=8)

        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=180)
        self.progress.pack(side="left", padx=8)

        ttk.Button(bar, text="打开输出目录", command=self._open_output).pack(side="right")

        # The default already avoids Chinese by choosing cleaner files; this
        # switch is for people who want the count pushed lower still, at the
        # cost of removing lines from the material.
        options = ttk.Frame(parent)
        options.pack(fill="x")

        self.drop_cjk_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            options,
            text="额外删除纯中文行（会改变源码内容，仅在默认方式仍超标时使用）",
            variable=self.drop_cjk_var,
        ).pack(anchor="w")

        ttk.Label(
            options,
            text=(
                "默认：自动从中文最少的源码文件中挑选 60 页，不修改任何代码。"
                "若仍需更少中文，才勾选上面一项。"
            ),
            foreground="#666666",
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

    def _build_output(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="结果", padding=6)
        box.pack(fill="both", expand=True)

        self.output_text = tk.Text(box, height=10, wrap="word", state="disabled")
        self.output_text.pack(fill="both", expand=True)

    # --- actions ------------------------------------------------------------

    def _choose_project(self) -> None:
        chosen = filedialog.askdirectory(title="选择要申请软著的项目目录")
        if chosen:
            self.project_var.set(chosen)

    def _choose_output(self) -> None:
        chosen = filedialog.askdirectory(title="选择输出目录")
        if chosen:
            self.output_var.set(chosen)

    def _toggle_publication(self) -> None:
        state = "normal" if self.published_var.get() else "disabled"
        self.pub_date_entry.configure(state=state)
        self.pub_place_entry.configure(state=state)

    def _collect_state(self) -> FormState:
        return FormState(
            project_root=self.project_var.get().strip(),
            software_name=self.name_var.get().strip(),
            version=self.version_var.get().strip(),
            output_dir=self.output_var.get().strip(),
            owner=self.owner_var.get().strip(),
            owner_category=self.owner_category_var.get(),
            development_completed=self.completed_var.get().strip(),
            dev_os=self.os_var.get().strip(),
            dev_ide=self.ide_var.get().strip(),
            purpose=self.purpose_var.get().strip(),
            domain=self.domain_var.get().strip(),
            main_functions=self.functions_text.get("1.0", "end").strip(),
            source_line_count=self.lines_var.get().strip(),
            published=self.published_var.get(),
            first_publication_date=self.pub_date_var.get().strip(),
            first_publication_place=self.pub_place_var.get().strip(),
        )

    def _on_check_env(self) -> None:
        """Report whether this machine can actually produce material."""
        from .environment import run_all

        out = self.output_var.get().strip()
        report = run_all(Path(out) if out else None)
        self._append("环境检查：")
        for line in report.to_text().splitlines():
            self._append(line)
        if report.can_run:
            self._append("结论：可以生成材料。")
        else:
            messagebox.showerror(
                "环境不满足要求",
                "\n".join(f"{i.name}：{i.detail}" for i in report.failures),
            )

    def _on_scan(self) -> None:
        root = self.project_var.get().strip()
        if not root:
            messagebox.showwarning("缺少项目目录", "请先选择要扫描的项目目录。")
            return
        if not Path(root).is_dir():
            messagebox.showerror("目录不存在", f"找不到目录：{root}")
            return

        self._append("正在扫描项目…")
        self._start_worker(lambda: ("scan", inspect_project(root)))

    def _on_generate(self) -> None:
        state = self._collect_state()
        missing = state.missing_required()
        if missing:
            messagebox.showwarning(
                "信息不完整", "请先填写：" + "、".join(missing)
            )
            return

        if not Path(state.project_root).is_dir():
            messagebox.showerror("目录不存在", f"找不到目录：{state.project_root}")
            return

        drop_cjk = self.drop_cjk_var.get()
        if drop_cjk:
            self._append(
                "正在生成材料，已启用「删除纯中文行」——注意这会改变源码内容。"
            )
        else:
            self._append(
                "正在生成材料。将从中文最少的源码文件中挑选 60 页，不修改任何代码。"
            )
        self._set_busy(True)

        def job() -> tuple[str, Any]:
            result = generate_materials(
                project_root=state.project_root,
                software_name=state.software_name,
                version=state.version,
                output_dir=state.output_dir or None,
                fields=state.to_fields(),
                drop_cjk=drop_cjk,
            )
            return "generate", result

        self._start_worker(job)

    def _open_output(self) -> None:
        target = self.output_var.get().strip()
        if not target:
            target = str(
                Path(self.project_var.get().strip() or ".") / "软件著作权申请资料"
            )
        path = Path(target)
        if not path.is_dir():
            messagebox.showinfo("目录尚未生成", f"还没有生成内容：{path}")
            return
        self._reveal(path)

    def _reveal(self, path: Path) -> None:
        import os
        import subprocess
        import sys

        try:
            if sys.platform == "win32":
                os.startfile(str(path))  # noqa: S606 - opening a folder is intended
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except Exception as exc:  # pragma: no cover - platform specific
            messagebox.showerror("无法打开目录", str(exc))

    # --- worker plumbing ----------------------------------------------------

    def _start_worker(self, job) -> None:  # noqa: ANN001 - callable returning (tag, payload)
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo("正在处理", "上一个任务还没结束，请稍候。")
            return

        self.progress.start(12)

        def run() -> None:
            try:
                tag, payload = job()
                self.queue.put((tag, payload))
            except Exception as exc:  # pragma: no cover - defensive
                self.queue.put(("error", f"{type(exc).__name__}: {exc}"))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _drain_queue(self) -> None:
        try:
            while True:
                tag, payload = self.queue.get_nowait()
                self._handle(tag, payload)
        except queue.Empty:
            pass
        self.root.after(120, self._drain_queue)

    def _handle(self, tag: str, payload: Any) -> None:
        self.progress.stop()
        self._set_busy(False)

        if tag == "scan":
            self._append(summarize_inspection(payload))
            return

        if tag == "generate":
            outcome = interpret_result(payload)
            self._append(outcome.summary)
            for item in outcome.blocking:
                self._append(f"  阻断：{item}")
            for item in outcome.warnings:
                self._append(f"  提示：{item}")
            if outcome.ok:
                messagebox.showinfo("生成完成", outcome.summary)
            else:
                messagebox.showerror(
                    "未通过校验",
                    "材料未通过校验：\n\n" + "\n".join(outcome.blocking[:6]),
                )
            return

        self._append(f"出错：{payload}")

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.run_button.configure(state=state)
        self.scan_button.configure(state=state)

    def _append(self, text: str) -> None:
        self.output_text.configure(state="normal")
        self.output_text.insert("end", text.rstrip() + "\n")
        self.output_text.see("end")
        self.output_text.configure(state="disabled")


def main() -> int:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    app = App(root)
    # Set the title again after the first frame: launching via `python -m`
    # leaves the interpreter path in the title bar otherwise.
    root.after(200, lambda: root.title(WINDOW_TITLE))
    _ = app
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
