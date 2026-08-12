"""Native Tk desktop application for MS Event Studio Phase 2."""

from __future__ import annotations

import getpass
import json
import math
import os
import queue
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import __version__
from .desktop_model import (
    FILTERS,
    CreationState,
    OptimisticReviewModel,
    PlotTransform,
    RecentProjects,
    Viewport,
    evidence_lines,
    event_visual_encoding,
    filter_events,
    keyboard_command,
)
from .demo import create_guided_test_assets
from .display import WindowRequest
from .errors import ExistingEventNavigation, MSEventStudioError
from .export import export_human_csv, export_machine_contract
from .parser import ParseProgress
from .paths import resolve_project_path
from .project import (
    CreateProjectRequest,
    PreparedProjectSource,
    create_project,
    inspect_project_source,
)
from .range_change import apply_range_change, preview_range_change
from .timebase import NANOSECONDS_PER_MINUTE, minutes_to_ns
from .theme import PALETTE, configure_theme, font_family, icon_photo
from .window_service import ProjectWindowService


APP_TITLE = "MS Event Studio"
FILTER_LABELS = {
    "all": "全部活动事件",
    "unreviewed": "未审阅",
    "accepted": "已接受",
    "rejected": "已排除",
    "pending": "待定",
    "manual_added": "人工补充",
    "manual_adjusted": "人工调整",
    "stale": "历史失效事件",
}
FILTER_VALUES = {label: value for value, label in FILTER_LABELS.items()}
SCALE_LABELS = {"linear": "线性", "log1p": "对数 log1p"}
SCALE_VALUES = {label: value for value, label in SCALE_LABELS.items()}
STATUS_LABELS = {
    "unreviewed": "未审阅",
    "accepted": "已接受",
    "rejected": "已排除",
    "pending": "待定",
}
PHASE_LABELS = {"parsing": "正在解析", "complete": "解析完成"}


def default_recent_path() -> Path:
    override = os.environ.get("MS_EVENT_STUDIO_CONFIG_DIR")
    if override:
        root = Path(override)
    elif sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / APP_TITLE
    elif sys.platform == "darwin":
        root = Path.home() / "Library/Application Support" / APP_TITLE
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ms-event-studio"
    return root / "recent_projects.json"


def _packaged_scientific_smoke() -> dict[str, Any]:
    """Exercise native numerical, Parquet, SQLite, display, and export runtimes."""

    import tempfile

    import pandas as pd

    from .detector import detect_events
    from .display import DisplayPyramid, WindowRequest
    from .review import ReviewStore
    from .timebase import AnalysisRange

    count = 1201
    time_sec = np.arange(count, dtype=float) * 0.1
    signal = np.zeros(count, dtype=float)
    signal[[300, 600, 900]] = [1000.0, 1500.0, 1200.0]
    scans = pd.DataFrame(
        {
            "scan_row_index": np.arange(count, dtype=np.int64),
            "spectrum_index": np.arange(count, dtype=np.int64),
            "scan_id": [str(100000 + index) for index in range(count)],
            "scan_time_ns": np.rint(time_sec * 1_000_000_000).astype(np.int64),
            "scan_start_time_sec": time_sec,
            "scan_start_time_min": time_sec / 60.0,
            "pc34_760_max_intensity": signal,
            "qc_782_max_intensity": np.full(count, 10.0),
            "tic": np.full(count, 1e7),
            "array_length": np.full(count, 7000, dtype=np.int64),
            "base_peak_mz": np.full(count, 760.5851),
            "pc34_760_ppm_error_at_max_intensity": np.zeros(count),
            "qc_782_ppm_error_at_max_intensity": np.zeros(count),
            "ratio_760_782_max_pseudo1": (signal + 1.0) / 11.0,
        }
    )
    analysis = AnalysisRange(0, 120_000_000_000)
    detected = detect_events(scans, "f" * 64, analysis)
    if len(detected.events) != 3:
        raise RuntimeError(f"packaged detector smoke expected 3 events, found {len(detected.events)}")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        scan_path = root / "scans.parquet"
        scans.to_parquet(scan_path, index=False)
        round_trip = pd.read_parquet(scan_path)
        pyramid = DisplayPyramid.build(round_trip, root / "display", source_binding="f" * 64)
        window = pyramid.read_window(
            WindowRequest(start_ns=0, end_ns=120_000_000_000, point_budget=200),
            [],
        )
        store = ReviewStore.create(
            root / "review.sqlite",
            project_id="packaged-smoke",
            generation_id=detected.generation_id,
            automatic_events=detected.events.to_dict(orient="records"),
        )
        states = store.list_events()
        accepted = store.set_status(
            states[0]["event_id"],
            "accepted",
            expected_revision=0,
            actor="smoke",
            session_id="smoke",
        )
        human = export_human_csv(
            store.list_events(),
            root / "accepted.csv",
            analysis_start_ns=analysis.start_ns,
            analysis_end_ns=analysis.end_ns,
        )
        machine = export_machine_contract(
            store.list_events(),
            detected.events,
            root / "machine",
            source_fingerprint={"sha256": "f" * 64},
            detector_version=detected.parameters["detector_version"],
            parameter_hash=detected.parameter_hash,
            generation_id=detected.generation_id,
            analysis_start_ns=analysis.start_ns,
            analysis_end_ns=analysis.end_ns,
        )
        store.close()
    return {
        "scan_rows": len(round_trip),
        "event_rows": len(detected.events),
        "display_points": len(window.trace),
        "accepted_event_id_prefix": str(accepted["event_id"])[:3],
        "human_rows": human.row_count,
        "machine_rows": machine.row_count,
    }


def _minutes_text(time_ns: int) -> str:
    value = Decimal(int(time_ns)) / Decimal(NANOSECONDS_PER_MINUTE)
    return format(value.quantize(Decimal("0.000001")), "f").rstrip("0").rstrip(".") or "0"


class _AsyncJobs:
    def __init__(self, owner, *, workers: int = 1) -> None:
        self.owner = owner
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ms-event-ui")
        self.results: queue.Queue[tuple[str, Any, BaseException | None]] = queue.Queue()
        self.callbacks: dict[
            str, tuple[Callable[[Any], None], Callable[[BaseException], None]]
        ] = {}
        self.closed = False
        self.owner.after(40, self._poll)

    def submit(
        self,
        function: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[BaseException], None],
    ) -> str:
        if self.closed:
            raise RuntimeError("background job runner is closed")
        identity = uuid.uuid4().hex
        self.callbacks[identity] = (on_success, on_error)

        def run() -> None:
            try:
                result = function()
            except BaseException as exc:  # delivered to the Tk main thread
                self.results.put((identity, None, exc))
            else:
                self.results.put((identity, result, None))

        self.executor.submit(run)
        return identity

    def _poll(self) -> None:
        while True:
            try:
                identity, result, error = self.results.get_nowait()
            except queue.Empty:
                break
            callbacks = self.callbacks.pop(identity, None)
            if callbacks is None:
                continue
            success, failure = callbacks
            if error is None:
                success(result)
            else:
                failure(error)
        if not self.closed:
            self.owner.after(40, self._poll)

    def close(self) -> None:
        self.closed = True
        self.callbacks.clear()
        self.executor.shutdown(wait=False, cancel_futures=True)


class NewProjectDialog:
    def __init__(
        self,
        app: "MSDesktopApp",
        *,
        initial_source: str | Path | None = None,
        initial_target: str | Path | None = None,
        initial_name: str | None = None,
        guided: bool = False,
    ) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.app = app
        self.guided = bool(guided)
        self.window = tk.Toplevel(app.root)
        self.window.title("新建项目 · MS Event Studio")
        self.window.geometry("820x720")
        self.window.minsize(740, 680)
        self.window.configure(background=PALETTE.canvas)
        self.window.iconphoto(False, app.app_icon_64)
        self.window.transient(app.root)
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.prepared: PreparedProjectSource | None = None
        self.prepared_path: Path | None = None
        self.state = CreationState()
        self.messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.closing = False

        outer = ttk.Frame(self.window, padding=22, style="Page.TFrame")
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        header = ttk.Frame(outer, style="Hero.TFrame", padding=(22, 14))
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        ttk.Label(header, image=app.app_icon_64, style="HeroBody.TLabel").pack(
            side="left", padx=(0, 16)
        )
        heading = ttk.Frame(header, style="Hero.TFrame")
        heading.pack(side="left", fill="x", expand=True)
        ttk.Label(heading, text="MS Event Studio · 新建项目", style="HeroSubtitle.TLabel").pack(
            anchor="w"
        )
        ttk.Label(heading, text="一次解析，建立可审计的事件项目", style="HeroTitle.TLabel").pack(
            anchor="w", pady=(2, 0)
        )

        form = ttk.Frame(outer, padding=20, style="Card.TFrame")
        form.grid(row=1, column=0, sticky="nsew")
        form.columnconfigure(1, weight=1)

        if self.guided:
            ttk.Label(
                form,
                text="引导测试 · 已准备 2 分钟合成源；先分析，再创建 0–2 min 项目。",
                style="InfoPill.TLabel",
            ).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 12))

        self.source_var = tk.StringVar(value=str(initial_source or ""))
        self.target_var = tk.StringVar(value=str(initial_target or ""))
        self.name_var = tk.StringVar(value=str(initial_name or ""))
        self.start_var = tk.StringVar(value="0")
        self.end_var = tk.StringVar(value="2" if self.guided else "60")
        fields = (
            ("原始 MS 文本", self.source_var, self._browse_source),
            ("项目目录", self.target_var, self._browse_target),
        )
        row_offset = 1 if self.guided else 0
        for row, (label, variable, command) in enumerate(fields, start=row_offset):
            ttk.Label(form, text=label, style="SurfaceMuted.TLabel").grid(
                row=row, column=0, sticky="w", pady=7
            )
            ttk.Entry(form, textvariable=variable).grid(
                row=row, column=1, sticky="ew", padx=10, pady=4
            )
            ttk.Button(
                form, text="浏览…", command=command, style="Secondary.TButton"
            ).grid(row=row, column=2, pady=4)
        display_row = row_offset + 2
        ttk.Label(form, text="项目名称", style="SurfaceMuted.TLabel").grid(
            row=display_row, column=0, sticky="w", pady=7
        )
        ttk.Entry(form, textvariable=self.name_var).grid(
            row=display_row, column=1, columnspan=2, sticky="ew", padx=(10, 0), pady=4
        )

        range_frame = ttk.LabelFrame(form, text="分析范围 · 闭区间（分钟）", padding=12)
        range_frame.grid(
            row=display_row + 1,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(14, 7),
        )
        range_frame.columnconfigure(1, weight=1)
        range_frame.columnconfigure(3, weight=1)
        ttk.Label(range_frame, text="起点", style="SurfaceMuted.TLabel").grid(row=0, column=0)
        self.start_entry = ttk.Entry(range_frame, textvariable=self.start_var, width=14)
        self.start_entry.grid(row=0, column=1, sticky="ew", padx=(7, 18))
        ttk.Label(range_frame, text="终点", style="SurfaceMuted.TLabel").grid(row=0, column=2)
        self.end_entry = ttk.Entry(range_frame, textvariable=self.end_var, width=14)
        self.end_entry.grid(row=0, column=3, sticky="ew", padx=(7, 0))
        self.extent_var = tk.StringVar(value="分析源文件后，将在此显示可用时间范围。")
        ttk.Label(range_frame, textvariable=self.extent_var, style="SurfaceMuted.TLabel").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(8, 0)
        )

        advanced = ttk.LabelFrame(form, text="科学配置 · 只读", padding=11)
        advanced.grid(
            row=display_row + 2, column=0, columnspan=3, sticky="ew", pady=7
        )
        ttk.Label(
            advanced,
            text="PC34 760.5851 / QC 782.5616 · 闭区间 ±12 ppm · 自适应 v0.4.4 基线",
            style="Surface.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            advanced,
            text="科学参数与版本绑定；修改范围时会先预览差异，再生成新的检测代次。",
            style="SurfaceMuted.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        self.progress = ttk.Progressbar(
            form, maximum=1.0, mode="determinate", style="Accent.Horizontal.TProgressbar"
        )
        self.progress.grid(
            row=display_row + 3,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(16, 7),
        )
        self.status_var = tk.StringVar(
            value="引导源已就绪 · 请先分析源文件。" if self.guided else "准备就绪"
        )
        ttk.Label(form, textvariable=self.status_var, style="SurfaceMuted.TLabel").grid(
            row=display_row + 4, column=0, columnspan=2, sticky="w"
        )
        self.primary = ttk.Button(
            form,
            text="分析源文件",
            command=self._primary,
            style="Primary.TButton",
        )
        self.primary.grid(row=display_row + 4, column=2, sticky="e")

        self.source_var.trace_add("write", self._source_changed)
        self.window.after(50, self._poll)
        self.window.grab_set()

    def _source_changed(self, *_args) -> None:
        if self.state.running:
            return
        try:
            current = Path(self.source_var.get()).resolve()
        except (OSError, ValueError):
            current = None
        if self.prepared_path is not None and current != self.prepared_path:
            self.prepared = None
            self.prepared_path = None
            self.extent_var.set("源文件已更改；创建项目之前请重新分析。")
            self.primary.configure(text="分析源文件")

    def _browse_source(self) -> None:
        chosen = self.filedialog.askopenfilename(
            parent=self.window,
            title="选择原始 MS 文本导出",
            filetypes=(("MS 文本导出", "*.txt"), ("所有文件", "*.*")),
        )
        if chosen:
            self.source_var.set(chosen)
            if not self.name_var.get().strip():
                self.name_var.set(Path(chosen).stem)

    def _browse_target(self) -> None:
        chosen = self.filedialog.askdirectory(
            parent=self.window,
            title="选择空项目目录，或选择新项目的保存位置",
            mustexist=False,
        )
        if chosen:
            self.target_var.set(chosen)

    def _set_running(self, text: str) -> None:
        self.primary.configure(text="取消")
        self.status_var.set(text)
        self.progress.configure(value=0.0)

    def _start_thread(self, function: Callable[[], Any], success_kind: str) -> None:
        self.state.start()

        def run() -> None:
            try:
                result = function()
            except BaseException as exc:
                self.state.fail(exc)
                self.messages.put(("error", exc))
            else:
                self.state.finish(result)
                self.messages.put((success_kind, result))

        self.worker = threading.Thread(target=run, name="ms-event-project-create", daemon=True)
        self.worker.start()

    def _progress_callback(self, progress: ParseProgress) -> None:
        self.state.update_progress(
            bytes_read=progress.bytes_read,
            total_bytes=progress.total_bytes,
            parsed_spectra=progress.parsed_spectra,
        )
        self.messages.put(("progress", progress))

    def _primary(self) -> None:
        if self.state.running:
            self.state.cancel()
            self.status_var.set("已请求取消…")
            return
        source_text = self.source_var.get().strip()
        if not source_text:
            self.messagebox.showerror(APP_TITLE, "请选择原始 MS 文本文件。", parent=self.window)
            return
        source = Path(source_text).resolve()
        if self.prepared is None or self.prepared_path != source:
            self._set_running("正在读取、校验并提取 MS 源文件…")
            self._start_thread(
                lambda: inspect_project_source(
                    source,
                    cancel_check=self.state.cancel_check,
                    progress_callback=self._progress_callback,
                ),
                "inspected",
            )
            return
        target = self.target_var.get().strip()
        name = self.name_var.get().strip()
        if not target or not name:
            self.messagebox.showerror(
                APP_TITLE,
                "请选择项目目录，并填写项目名称。",
                parent=self.window,
            )
            return
        try:
            start_ns = minutes_to_ns(self.start_var.get())
            end_ns = minutes_to_ns(self.end_var.get())
        except ValueError as exc:
            self.messagebox.showerror(APP_TITLE, str(exc), parent=self.window)
            return
        if not self.prepared.start_ns <= start_ns <= end_ns <= self.prepared.end_ns:
            self.messagebox.showerror(
                APP_TITLE,
                "分析闭区间必须位于已解析源文件的可用范围内。",
                parent=self.window,
            )
            return
        request = CreateProjectRequest(
            source_path=source,
            project_dir=target,
            display_name=name,
            analysis_start_min=self.start_var.get(),
            analysis_end_min=self.end_var.get(),
            cancel_check=self.state.cancel_check,
            progress_callback=self._progress_callback,
        )
        prepared = self.prepared
        self._set_running("正在识别事件并原子发布项目…")
        self._start_thread(
            lambda: create_project(request, prepared_source=prepared),
            "created",
        )

    def _poll(self) -> None:
        while True:
            try:
                kind, payload = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                progress: ParseProgress = payload
                self.progress.configure(value=progress.fraction)
                gib = progress.bytes_read / (1024**3)
                total_gib = progress.total_bytes / (1024**3)
                self.status_var.set(
                    f"{PHASE_LABELS.get(progress.phase, progress.phase)}："
                    f"{gib:.2f}/{total_gib:.2f} GiB · {progress.parsed_spectra:,} 个谱图"
                )
            elif kind == "inspected":
                self.prepared = payload
                self.prepared_path = payload.source_path
                self.start_var.set(_minutes_text(payload.start_ns))
                self.end_var.set(_minutes_text(payload.end_ns))
                self.extent_var.set(
                    f"可用闭区间：{_minutes_text(payload.start_ns)}–"
                    f"{_minutes_text(payload.end_ns)} min · {len(payload.parsed.scans):,} 个谱图"
                )
                self.progress.configure(value=1.0)
                self.status_var.set("源文件分析完成；请选择范围并创建项目。")
                self.primary.configure(text="创建项目")
            elif kind == "created":
                self.progress.configure(value=1.0)
                self.status_var.set("项目已创建。")
                self.window.grab_release()
                self.window.destroy()
                self.app.open_project(payload.project_dir)
                return
            elif kind == "error":
                self.primary.configure(text="创建项目" if self.prepared else "分析源文件")
                self.status_var.set("已取消" if self.state.cancel_requested else "操作失败")
                if not self.state.cancel_requested:
                    self.messagebox.showerror(APP_TITLE, str(payload), parent=self.window)
            if self.closing and not self.state.running:
                self.window.grab_release()
                self.window.destroy()
                return
        if self.window.winfo_exists():
            self.window.after(50, self._poll)

    def _close(self) -> None:
        if self.state.running:
            self.closing = True
            self.state.cancel()
            self.status_var.set("已请求取消；正在等待安全的谱图边界…")
        else:
            self.window.grab_release()
            self.window.destroy()


class ReviewView:
    def __init__(self, app: "MSDesktopApp", project_dir: str | Path) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, simpledialog, ttk

        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.simpledialog = simpledialog
        self.app = app
        self.service = ProjectWindowService.open(project_dir)
        self.actor = getpass.getuser() or "desktop-user"
        self.session_id = "desktop-" + uuid.uuid4().hex
        self.jobs = _AsyncJobs(app.root)
        self.closed = False
        self.selected_event_id: str | None = None
        self.snapshot = None
        self.transform: PlotTransform | None = None
        self.hit_events: list[tuple[float, float, str]] = []
        self.interaction_mode = "select"
        self.pending_click_ns: int | None = None
        self.project_mutation_pending = False

        start = self.service.analysis_start_ns
        end = self.service.analysis_end_ns
        span = max(1, end - start)
        default_window = min(span, 10 * NANOSECONDS_PER_MINUTE)
        self.viewport = Viewport(start, end, start, max(1, default_window))
        self.model = OptimisticReviewModel(self.service.all_events())

        self.frame = ttk.Frame(app.root, style="Page.TFrame")
        self.frame.pack(fill="both", expand=True)
        self._build_controls()
        self._build_body()
        self._bind_keys()
        self.reload_window()

    def _build_controls(self) -> None:
        tk, ttk = self.tk, self.ttk
        appbar = ttk.Frame(self.frame, style="Hero.TFrame", padding=(14, 9))
        appbar.pack(fill="x")
        ttk.Label(appbar, image=self.app.app_icon_32, style="HeroBody.TLabel").pack(
            side="left", padx=(0, 10)
        )
        brand = ttk.Frame(appbar, style="Hero.TFrame")
        brand.pack(side="left", fill="x", expand=True)
        ttk.Label(
            brand,
            text="MS Event Studio · 事件审阅",
            style="HeroSubtitle.TLabel",
            font=(font_family(), 9, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            brand,
            text=str(self.service.project.manifest["display_name"]),
            style="HeroBody.TLabel",
            font=(font_family(), 12, "bold"),
        ).pack(anchor="w")
        ttk.Button(
            appbar,
            text="返回首页",
            command=self.app.show_welcome,
            style="Ghost.TButton",
        ).pack(side="right")

        toolbar = ttk.Frame(self.frame, padding=(12, 9), style="Card.TFrame")
        toolbar.pack(fill="x", padx=12, pady=(12, 8))
        toolbar.columnconfigure(7, weight=1)
        self.start_var = tk.StringVar(value=_minutes_text(self.viewport.start_ns))
        self.window_var = tk.StringVar(
            value=_minutes_text(self.viewport.window_ns)
        )
        self.filter_var = tk.StringVar(value=FILTER_LABELS["all"])
        self.scale_var = tk.StringVar(value=SCALE_LABELS["linear"])
        self.labels_var = tk.BooleanVar(value=True)
        self.include_pending_var = tk.BooleanVar(value=False)

        ttk.Label(toolbar, text="信号窗口", style="Eyebrow.TLabel").grid(
            row=0, column=0, columnspan=7, sticky="w", pady=(0, 5)
        )
        ttk.Label(toolbar, text="起点 min", style="SurfaceMuted.TLabel").grid(
            row=1, column=0, sticky="w"
        )
        ttk.Entry(toolbar, textvariable=self.start_var, width=10).grid(
            row=1, column=1, padx=(5, 11)
        )
        ttk.Label(toolbar, text="窗宽 min", style="SurfaceMuted.TLabel").grid(
            row=1, column=2, sticky="w"
        )
        ttk.Entry(toolbar, textvariable=self.window_var, width=9).grid(
            row=1, column=3, padx=(5, 7)
        )
        ttk.Button(
            toolbar, text="应用", command=self.apply_viewport, style="Primary.TButton"
        ).grid(row=1, column=4, padx=(0, 9))
        ttk.Button(
            toolbar,
            text="◀",
            width=3,
            command=lambda: self.pan(-1),
            style="Toolbar.TButton",
        ).grid(row=1, column=5, padx=(0, 3))
        ttk.Button(
            toolbar,
            text="▶",
            width=3,
            command=lambda: self.pan(1),
            style="Toolbar.TButton",
        ).grid(row=1, column=6)

        settings = ttk.Frame(toolbar, style="Surface.TFrame")
        settings.grid(row=0, column=8, rowspan=2, sticky="e")
        ttk.Label(settings, text="筛选", style="SurfaceMuted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 5)
        )
        filter_box = ttk.Combobox(
            settings,
            textvariable=self.filter_var,
            values=tuple(FILTER_LABELS[value] for value in FILTERS),
            width=15,
            state="readonly",
        )
        filter_box.grid(row=0, column=1, padx=(0, 10))
        filter_box.bind("<<ComboboxSelected>>", lambda _event: self.reload_window())
        ttk.Label(settings, text="Y 轴", style="SurfaceMuted.TLabel").grid(
            row=0, column=2, sticky="w", padx=(0, 5)
        )
        scale_box = ttk.Combobox(
            settings,
            textvariable=self.scale_var,
            values=tuple(SCALE_LABELS.values()),
            width=8,
            state="readonly",
        )
        scale_box.grid(row=0, column=3, padx=(0, 9))
        scale_box.bind("<<ComboboxSelected>>", lambda _event: self.render())
        ttk.Checkbutton(
            settings, text="标签", variable=self.labels_var, command=self.render
        ).grid(row=0, column=4, padx=(0, 9))
        ttk.Button(
            settings,
            text="修改范围…",
            command=self.change_range,
            style="Warning.TButton",
        ).grid(row=0, column=5)

        if str(self.service.project.manifest["display_name"]).startswith(("Guided test", "引导测试")):
            ttk.Label(
                self.frame,
                text=(
                    "引导测试 · 自动峰顶应位于 0.5 / 1.0 / 1.5 min；在 0.75 min 附近补峰，"
                    "再测试状态、撤销/重做、导出和 0.6–2 min 范围变更。"
                ),
                style="InfoPill.TLabel",
            ).pack(fill="x", padx=12, pady=(0, 7))

    def _build_body(self) -> None:
        tk, ttk = self.tk, self.ttk
        pane = ttk.Panedwindow(self.frame, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        plot_frame = ttk.Frame(pane, padding=10, style="Card.TFrame")
        side = ttk.Frame(pane, width=340, padding=14, style="Card.TFrame")
        pane.add(plot_frame, weight=4)
        pane.add(side, weight=1)

        plot_heading = ttk.Frame(plot_frame, style="Surface.TFrame")
        plot_heading.pack(fill="x", pady=(0, 8))
        ttk.Label(plot_heading, text="PC34 / MS760 信号", style="Eyebrow.TLabel").pack(
            side="left"
        )
        ttk.Label(
            plot_heading,
            text="显示降采样不会移除事件峰顶",
            style="SurfaceMuted.TLabel",
        ).pack(side="right")

        self.canvas = tk.Canvas(
            plot_frame,
            background=PALETTE.plot,
            highlightthickness=1,
            highlightbackground=PALETTE.border,
            highlightcolor=PALETTE.cyan,
            takefocus=True,
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self.render())
        self.canvas.bind("<Button-1>", self._canvas_click)
        self.canvas.bind("<Motion>", self._canvas_motion)

        self.hover_var = tk.StringVar(value="准备就绪")
        ttk.Label(
            plot_frame,
            textvariable=self.hover_var,
            anchor="w",
            style="SurfaceMuted.TLabel",
        ).pack(fill="x", pady=(6, 0))

        ttk.Label(side, text="事件检查器", style="Eyebrow.TLabel").pack(anchor="w")
        ttk.Label(side, text="所选事件证据", style="SurfaceTitle.TLabel").pack(
            anchor="w", pady=(2, 8)
        )
        evidence_frame = ttk.Frame(side, style="Surface.TFrame")
        evidence_frame.pack(fill="x")
        self.details = tk.Text(
            evidence_frame,
            height=7,
            width=42,
            wrap="word",
            state="disabled",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=PALETTE.border,
            padx=10,
            pady=9,
        )
        evidence_scroll = ttk.Scrollbar(
            evidence_frame,
            orient="vertical",
            command=self.details.yview,
        )
        self.details.configure(yscrollcommand=evidence_scroll.set)
        self.details.pack(side="left", fill="both", expand=True)
        evidence_scroll.pack(side="right", fill="y")

        tabs = ttk.Notebook(side)
        tabs.pack(fill="both", expand=True, pady=(9, 0))
        review_tab = ttk.Frame(tabs, padding=6, style="Surface.TFrame")
        export_tab = ttk.Frame(tabs, padding=10, style="Surface.TFrame")
        tabs.add(review_tab, text="审阅与编辑")
        tabs.add(export_tab, text="导出")

        status = ttk.LabelFrame(review_tab, text="审阅状态", padding=6)
        status.pack(fill="x", pady=(8, 4))
        for column, (label, value) in enumerate(
            (("接受 [A]", "accepted"), ("排除 [R]", "rejected"), ("待定 [P]", "pending"))
        ):
            style = {
                "accepted": "Success.TButton",
                "rejected": "Danger.TButton",
                "pending": "Warning.TButton",
            }[value]
            ttk.Button(
                status,
                text=label,
                command=lambda value=value: self.set_status(value),
                style=style,
                padding=(6, 5),
            ).grid(
                row=0, column=column, sticky="ew", padx=2
            )
            status.columnconfigure(column, weight=1)
        ttk.Button(
            status,
            text="未审阅 [U]",
            command=lambda: self.set_status("unreviewed"),
            style="Secondary.TButton",
            padding=(6, 5),
        ).grid(
            row=1, column=0, sticky="ew", padx=2, pady=(5, 0)
        )
        ttk.Button(
            status,
            text="恢复原始",
            command=self.restore,
            style="Secondary.TButton",
            padding=(6, 5),
        ).grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=2, pady=(5, 0)
        )

        edit = ttk.LabelFrame(review_tab, text="物理峰编辑", padding=6)
        edit.pack(fill="x", pady=4)
        self.add_button = ttk.Button(
            edit,
            text="补充事件 [+]",
            command=lambda: self.set_mode("add"),
            style="Primary.TButton",
            padding=(6, 5),
        )
        self.add_button.grid(row=0, column=0, sticky="ew", padx=2)
        self.adjust_button = ttk.Button(
            edit,
            text="调整峰顶 [M]",
            command=lambda: self.set_mode("adjust"),
            style="Secondary.TButton",
            padding=(6, 5),
        )
        self.adjust_button.grid(row=0, column=1, sticky="ew", padx=2)
        edit.columnconfigure(0, weight=1)
        edit.columnconfigure(1, weight=1)
        ttk.Button(
            edit,
            text="撤销 [Ctrl+Z]",
            command=self.undo,
            style="Toolbar.TButton",
            padding=(6, 4),
        ).grid(
            row=1, column=0, sticky="ew", padx=2, pady=(5, 0)
        )
        ttk.Button(
            edit,
            text="重做 [Ctrl+Y]",
            command=self.redo,
            style="Toolbar.TButton",
            padding=(6, 4),
        ).grid(
            row=1, column=1, sticky="ew", padx=2, pady=(5, 0)
        )

        ttk.Label(review_tab, text="操作理由 · 写入审计历史", style="SurfaceMuted.TLabel").pack(
            anchor="w", pady=(7, 3)
        )
        self.reason_var = tk.StringVar()
        ttk.Entry(review_tab, textvariable=self.reason_var).pack(fill="x")

        ttk.Label(export_tab, text="当前活动代次", style="Eyebrow.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            export_tab,
            text="历史失效代次保留在项目中，但不会进入当前导出。",
            style="SurfaceMuted.TLabel",
            wraplength=300,
            justify="left",
        ).pack(anchor="w", fill="x", pady=(3, 12))
        export = ttk.LabelFrame(export_tab, text="人用 CSV", padding=8)
        export.pack(fill="x")
        ttk.Checkbutton(
            export,
            text="包含待定事件（始终包含已接受事件）",
            variable=self.include_pending_var,
        ).pack(anchor="w")
        ttk.Button(
            export,
            text="导出人用 CSV…",
            command=self.export_human,
            style="Primary.TButton",
        ).pack(fill="x", pady=(7, 0))
        machine = ttk.LabelFrame(export_tab, text="下游机器契约", padding=8)
        machine.pack(fill="x", pady=(9, 0))
        ttk.Label(
            machine,
            text="版本化 manifest + Parquet + SHA-256 校验文件。",
            style="SurfaceMuted.TLabel",
        ).pack(anchor="w", pady=(0, 6))
        ttk.Button(
            machine,
            text="导出机器契约…",
            command=self.export_machine,
            style="Secondary.TButton",
        ).pack(fill="x")

        self.status_var = tk.StringVar(value="准备就绪")
        status_bar = ttk.Frame(self.frame, style="Status.TFrame", padding=(12, 5))
        status_bar.pack(fill="x")
        ttk.Label(
            status_bar,
            textvariable=self.status_var,
            anchor="w",
            style="Status.TLabel",
        ).pack(fill="x")

    def _bind_keys(self) -> None:
        self.app.root.bind("<KeyPress>", self._key_press)

    def _key_press(self, event) -> None:
        control = bool(event.state & 0x0004) or bool(event.state & 0x0008)
        focused = self.app.root.focus_get()
        focus_class = focused.winfo_class() if focused is not None else None
        command = keyboard_command(
            event.keysym,
            control=control,
            focus_widget_class=focus_class,
        )
        actions = {
            "accept": lambda: self.set_status("accepted"),
            "reject": lambda: self.set_status("rejected"),
            "pending": lambda: self.set_status("pending"),
            "unreviewed": lambda: self.set_status("unreviewed"),
            "previous_window": lambda: self.pan(-1),
            "next_window": lambda: self.pan(1),
            "previous_event": lambda: self.select_adjacent(-1),
            "next_event": lambda: self.select_adjacent(1),
            "undo": self.undo,
            "redo": self.redo,
            "add_mode": lambda: self.set_mode("add"),
            "adjust_mode": lambda: self.set_mode("adjust"),
            "cancel_mode": lambda: self.set_mode("select"),
        }
        if command in actions:
            actions[command]()
            return "break"
        return None

    def _request(self) -> WindowRequest:
        return WindowRequest(
            start_ns=self.viewport.start_ns,
            end_ns=self.viewport.end_ns,
            point_budget=max(200, int(self.canvas.winfo_width() * 1.5)),
        )

    def _filter_mode(self) -> str:
        return FILTER_VALUES.get(self.filter_var.get(), "all")

    def _scale_mode(self) -> str:
        return SCALE_VALUES.get(self.scale_var.get(), "linear")

    def reload_window(self, *, keep_model: bool = False) -> None:
        try:
            snapshot = self.service.window(
                self._request(),
                status_filter=self._filter_mode(),
                selected_event_id=self.selected_event_id,
                maximum_labels=max(4, self.canvas.winfo_width() // 90),
            )
        except Exception as exc:
            self.messagebox.showerror(APP_TITLE, str(exc), parent=self.app.root)
            return
        self.snapshot = snapshot
        if not keep_model:
            try:
                self.model.replace(self.service.all_events())
            except RuntimeError:
                pass
        self.render()
        self._update_details()
        self.status_var.set(
            f"{len(snapshot.events):,} 个可见事件 · {len(snapshot.trace):,} 个曲线点 · "
            f"显示分桶 {snapshot.bucket_size} · 单次 SQLite 快照"
        )

    def apply_viewport(self) -> None:
        try:
            start = minutes_to_ns(self.start_var.get())
            window = minutes_to_ns(self.window_var.get())
            self.viewport = self.viewport.with_window(window).with_start(start)
        except (ValueError, TypeError) as exc:
            self.messagebox.showerror(APP_TITLE, str(exc), parent=self.app.root)
            return
        self._sync_view_entries()
        self.reload_window()

    def _sync_view_entries(self) -> None:
        self.start_var.set(_minutes_text(self.viewport.start_ns))
        self.window_var.set(_minutes_text(self.viewport.window_ns))

    def pan(self, direction: int) -> None:
        self.viewport = self.viewport.pan(int(direction) * self.viewport.window_ns)
        self._sync_view_entries()
        self.reload_window()

    def _visible_model_events(self) -> list[dict[str, Any]]:
        rows = filter_events(self.model.events(), self._filter_mode())
        return [row for row in rows if self.viewport.contains(int(row["current_apex_time_ns"]))]

    def render(self) -> None:
        if self.snapshot is None or not self.canvas.winfo_exists():
            return
        canvas = self.canvas
        width = max(240, canvas.winfo_width())
        height = max(180, canvas.winfo_height())
        canvas.delete("all")
        trace = self.snapshot.trace
        signal = trace["pc34_760_max_intensity"].to_numpy(dtype=float) if len(trace) else np.zeros(0)
        maximum = float(np.max(signal)) if len(signal) else 0.0
        visible_events = self._visible_model_events()
        if visible_events:
            maximum = max(maximum, max(float(row.get("current_apex_intensity", 0.0)) for row in visible_events))
        maximum = max(1.0, maximum)
        transform = PlotTransform(
            width=width,
            height=height,
            start_ns=self.viewport.start_ns,
            end_ns=max(self.viewport.start_ns + 1, self.viewport.end_ns),
            maximum_signal=maximum,
            log_scale=self._scale_mode() == "log1p",
            # Keep a dedicated legend lane above the scientific plot so a
            # full-scale apex can never collide with the status key.
            top=44,
        )
        self.transform = transform
        canvas.create_rectangle(
            transform.left,
            transform.top,
            width - transform.right,
            height - transform.bottom,
            outline=PALETTE.border,
            fill=PALETTE.plot,
        )
        for index in range(6):
            fraction = index / 5
            x = transform.left + fraction * transform.plot_width
            time_ns = int(round(transform.start_ns + fraction * (transform.end_ns - transform.start_ns)))
            canvas.create_line(x, transform.top, x, height - transform.bottom, fill=PALETTE.grid)
            canvas.create_text(
                x,
                height - transform.bottom + 15,
                text=f"{time_ns / NANOSECONDS_PER_MINUTE:.3f}",
                fill=PALETTE.muted,
                font=("TkDefaultFont", 8),
            )
        canvas.create_text(
            8,
            transform.top,
            anchor="nw",
            text=("log1p · " if transform.log_scale else "") + f"最大值 {maximum:.4g}",
            fill=PALETTE.muted,
            font=("TkDefaultFont", 8),
        )
        if len(trace):
            coordinates: list[float] = []
            for time_ns, intensity in zip(
                trace["scan_time_ns"].astype("int64"), signal, strict=True
            ):
                coordinates.extend((transform.x_for_time(int(time_ns)), transform.y_for_signal(float(intensity))))
            if len(coordinates) >= 4:
                canvas.create_line(*coordinates, fill=PALETTE.trace, width=1.5, tags=("trace",))

        label_ids = set(self.snapshot.label_event_ids) if self.labels_var.get() else set()
        self.hit_events = []
        for row in visible_events:
            time_ns = int(row["current_apex_time_ns"])
            intensity = float(row.get("current_apex_intensity", 0.0))
            x = transform.x_for_time(time_ns)
            y = transform.y_for_signal(intensity)
            encoding = event_visual_encoding(str(row["status"]), str(row["origin"]))
            auto_left = row.get("original_left_time_ns")
            auto_right = row.get("original_right_time_ns")
            if auto_left is not None and auto_right is not None:
                left_x = transform.x_for_time(int(auto_left))
                right_x = transform.x_for_time(int(auto_right))
                canvas.create_line(
                    left_x,
                    height - transform.bottom - 5,
                    right_x,
                    height - transform.bottom - 5,
                    fill=encoding.color,
                    width=3,
                    dash=encoding.dash or None,
                )
            selected = str(row["event_id"]) == str(self.selected_event_id)
            outline = PALETTE.navy if selected else encoding.color
            marker_size = 6 if selected else 5
            self._draw_marker(x, y, marker_size, encoding.shape, encoding.color, outline)
            if row.get("write_pending"):
                canvas.create_oval(
                    x - 10,
                    y - 10,
                    x + 10,
                    y + 10,
                    outline=PALETTE.navy,
                    dash=(2, 2),
                )
            if str(row["event_id"]) in label_ids:
                canvas.create_text(
                    x + 4,
                    max(transform.top + 8, y - 10),
                    anchor="sw",
                    text=encoding.text_token,
                    fill=encoding.color,
                    font=("TkDefaultFont", 8, "bold"),
                )
            self.hit_events.append((x, y, str(row["event_id"])))

        legend = (("U", "unreviewed"), ("A", "accepted"), ("R", "rejected"), ("P", "pending"))
        legend_x = transform.left + 8
        for token, status in legend:
            encoding = event_visual_encoding(status, "automatic")
            canvas.create_text(
                legend_x,
                18,
                text=f"{token} {STATUS_LABELS[status]}",
                anchor="w",
                fill=encoding.color,
            )
            legend_x += 92
        if self.pending_click_ns is not None:
            x = transform.x_for_time(self.pending_click_ns)
            canvas.create_line(
                x,
                transform.top,
                x,
                height - transform.bottom,
                fill=PALETTE.navy,
                dash=(4, 3),
                width=2,
            )

    def _draw_marker(self, x: float, y: float, size: int, shape: str, color: str, outline: str) -> None:
        c = self.canvas
        if shape == "circle":
            c.create_oval(x - size, y - size, x + size, y + size, fill=color, outline=outline, width=2)
        elif shape == "triangle":
            c.create_polygon(x, y - size, x - size, y + size, x + size, y + size, fill=color, outline=outline, width=2)
        elif shape == "diamond":
            c.create_polygon(x, y - size, x - size, y, x, y + size, x + size, y, fill=color, outline=outline, width=2)
        else:
            c.create_line(x - size, y - size, x + size, y + size, fill=outline, width=2)
            c.create_line(x - size, y + size, x + size, y - size, fill=outline, width=2)

    def _canvas_motion(self, event) -> None:
        if self.transform is None or self.snapshot is None:
            return
        time_ns = self.transform.time_for_x(event.x)
        text = f"{time_ns / NANOSECONDS_PER_MINUTE:.6f} min"
        if self.hit_events:
            closest = min(self.hit_events, key=lambda row: abs(row[0] - event.x))
            if abs(closest[0] - event.x) <= 7:
                text += f" · EventID {closest[2]}"
        self.hover_var.set(text)

    def _canvas_click(self, event) -> None:
        if self.transform is None:
            return
        time_ns = self.transform.time_for_x(event.x)
        if self.interaction_mode == "add":
            self._submit_add(time_ns)
            return
        if self.interaction_mode == "adjust":
            self._submit_adjust(time_ns)
            return
        if not self.hit_events:
            return
        closest = min(
            self.hit_events,
            key=lambda row: math.hypot(row[0] - event.x, row[1] - event.y),
        )
        if math.hypot(closest[0] - event.x, closest[1] - event.y) <= 14:
            self.select_event(closest[2])

    def select_event(self, event_id: str) -> None:
        self.selected_event_id = str(event_id)
        self.reload_window(keep_model=True)

    def select_adjacent(self, direction: int) -> None:
        events = self._visible_model_events()
        if not events:
            return
        ids = [str(row["event_id"]) for row in events]
        if self.selected_event_id not in ids:
            chosen = ids[0 if direction >= 0 else -1]
        else:
            index = ids.index(self.selected_event_id)
            chosen = ids[min(max(0, index + int(direction)), len(ids) - 1)]
        self.select_event(chosen)

    def _selected(self) -> dict[str, Any] | None:
        if self.selected_event_id is None:
            return None
        try:
            return self.model.event(self.selected_event_id)
        except KeyError:
            return None

    def _update_details(self) -> None:
        event = self._selected()
        scan = self.snapshot.selected_scan if self.snapshot else None
        automatic = self.snapshot.selected_automatic if self.snapshot else None
        text = "\n".join(evidence_lines(event, scan, automatic))
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    def _reason(self) -> str:
        return self.reason_var.get().strip()

    def set_status(self, status: str) -> None:
        if self.project_mutation_pending:
            self.status_var.set("请等待分析范围变更完成。")
            return
        current = self._selected()
        if current is None:
            self.status_var.set("请先选择一个事件。")
            return
        try:
            token = self.model.begin_status(current["event_id"], status)
        except Exception as exc:
            self.status_var.set(f"无法更新状态：{exc}")
            return
        self.render()
        self._update_details()
        self.status_var.set(f"正在保存“{STATUS_LABELS.get(status, status)}”…")
        reason = self._reason()

        def work():
            return self.service.review_store.set_status(
                current["event_id"],
                status,
                expected_revision=int(current["revision"]),
                actor=self.actor,
                session_id=self.session_id,
                reason=reason,
            )

        self.jobs.submit(
            work,
            lambda result: self._finish_optimistic(token, result),
            lambda error: self._fail_optimistic(token, error),
        )

    def _restore_patch(self, current: dict[str, Any]) -> dict[str, Any]:
        if current.get("original_auto_event_id"):
            return {
                "current_scan_id": current["original_scan_id"],
                "current_scan_row_index": current["original_scan_row_index"],
                "current_spectrum_index": current["original_spectrum_index"],
                "current_apex_time_ns": current["original_apex_time_ns"],
                "current_apex_time_sec": current["original_apex_time_sec"],
                "current_apex_intensity": current["original_apex_intensity"],
                "status": "unreviewed",
                "origin": "automatic",
                "snap_offset_sec": 0.0,
            }
        return {
            "current_scan_id": current["created_scan_id"],
            "current_scan_row_index": current["created_scan_row_index"],
            "current_spectrum_index": current["created_spectrum_index"],
            "current_apex_time_ns": current["created_apex_time_ns"],
            "current_apex_time_sec": current["created_apex_time_sec"],
            "current_apex_intensity": current["created_apex_intensity"],
            "status": "accepted",
            "origin": "manual_added",
            "snap_offset_sec": current.get("created_snap_offset_sec", 0.0),
        }

    def restore(self) -> None:
        if self.project_mutation_pending:
            self.status_var.set("请等待分析范围变更完成。")
            return
        current = self._selected()
        if current is None:
            self.status_var.set("请先选择一个事件。")
            return
        try:
            token = self.model.begin_patch(current["event_id"], self._restore_patch(current))
        except Exception as exc:
            self.status_var.set(f"无法恢复事件：{exc}")
            return
        self.render()
        self._update_details()
        self.status_var.set("正在恢复原始自动峰顶或人工创建峰顶…")
        reason = self._reason()

        def work():
            return self.service.review_store.restore(
                current["event_id"],
                expected_revision=int(current["revision"]),
                actor=self.actor,
                session_id=self.session_id,
                reason=reason,
            )

        self.jobs.submit(
            work,
            lambda result: self._finish_optimistic(token, result),
            lambda error: self._fail_optimistic(token, error),
        )

    def _finish_optimistic(self, token, result) -> None:
        self.model.commit(token, result)
        self.selected_event_id = result["event_id"]
        self.reload_window()
        self.status_var.set("已保存，并追加到审计历史。")

    def _fail_optimistic(self, token, error: BaseException) -> None:
        self.model.rollback(token, error)
        self.render()
        self._update_details()
        self.status_var.set(f"保存失败，界面状态已回滚：{error}")
        self.messagebox.showerror(APP_TITLE, str(error), parent=self.app.root)

    def set_mode(self, mode: str) -> None:
        if mode not in {"select", "add", "adjust"}:
            raise ValueError(mode)
        if mode == "adjust" and self._selected() is None:
            self.status_var.set("调整峰顶前，请先选择一个事件。")
            return
        self.interaction_mode = mode
        self.pending_click_ns = None
        messages = {
            "select": "选择模式。",
            "add": "补峰模式：请点击真实的 PC34 正局部峰附近；按 Esc 取消。",
            "adjust": "调峰模式：请在允许的物理 support 内点击；按 Esc 取消。",
        }
        self.status_var.set(messages[mode])
        self.canvas.configure(cursor="crosshair" if mode != "select" else "arrow")
        self.render()

    def _submit_add(self, time_ns: int) -> None:
        if self.project_mutation_pending:
            self.status_var.set("请等待分析范围变更完成。")
            return
        self.pending_click_ns = int(time_ns)
        self.render()
        self.status_var.set("正在吸附到真实扫描并保存人工事件…")
        click_sec = time_ns / 1_000_000_000
        reason = self._reason()

        def work():
            return self.service.review_store.add_event(
                click_time_sec=click_sec,
                scans=self.service.scans,
                analysis_start_ns=self.service.analysis_start_ns,
                analysis_end_ns=self.service.analysis_end_ns,
                actor=self.actor,
                session_id=self.session_id,
                reason=reason,
            )

        def success(result):
            self.pending_click_ns = None
            self.interaction_mode = "select"
            self.canvas.configure(cursor="arrow")
            self.selected_event_id = result["event_id"]
            self.reload_window()
            self.status_var.set(f"已绑定真实扫描；吸附偏移 {result['snap_offset_sec']:.6g} s。")

        def failure(error):
            self.pending_click_ns = None
            if isinstance(error, ExistingEventNavigation):
                self.interaction_mode = "select"
                self.canvas.configure(cursor="arrow")
                self.select_event(error.event_id)
                self.status_var.set("该物理证据已属于现有事件，已定位到该事件。")
            else:
                self.render()
                self.status_var.set(f"补充失败，未创建事件：{error}")
                self.messagebox.showerror(APP_TITLE, str(error), parent=self.app.root)

        self.jobs.submit(work, success, failure)

    def _submit_adjust(self, time_ns: int) -> None:
        if self.project_mutation_pending:
            self.status_var.set("请等待分析范围变更完成。")
            return
        current = self._selected()
        if current is None:
            self.set_mode("select")
            return
        self.pending_click_ns = int(time_ns)
        self.render()
        self.status_var.set("正在吸附并保存调整后的峰顶…")
        click_sec = time_ns / 1_000_000_000
        reason = self._reason()

        def work():
            return self.service.review_store.adjust_apex(
                current["event_id"],
                click_time_sec=click_sec,
                scans=self.service.scans,
                analysis_start_ns=self.service.analysis_start_ns,
                analysis_end_ns=self.service.analysis_end_ns,
                expected_revision=int(current["revision"]),
                actor=self.actor,
                session_id=self.session_id,
                reason=reason,
            )

        def success(result):
            self.pending_click_ns = None
            self.interaction_mode = "select"
            self.canvas.configure(cursor="arrow")
            self.selected_event_id = result["event_id"]
            self.reload_window()
            self.status_var.set(f"峰顶已调整；吸附偏移 {result['snap_offset_sec']:.6g} s。")

        def failure(error):
            self.pending_click_ns = None
            self.render()
            self.status_var.set(f"调整失败，界面状态未改变：{error}")
            self.messagebox.showerror(APP_TITLE, str(error), parent=self.app.root)

        self.jobs.submit(work, success, failure)

    def undo(self) -> None:
        if self.project_mutation_pending:
            self.status_var.set("请等待分析范围变更完成。")
            return
        self.status_var.set("正在撤销并追加审计记录…")
        reason = self._reason()
        self.jobs.submit(
            lambda: self.service.review_store.undo(
                actor=self.actor, session_id=self.session_id, reason=reason
            ),
            lambda result: self._finish_history("撤销完成。", result),
            lambda error: self._history_error("撤销", error),
        )

    def redo(self) -> None:
        if self.project_mutation_pending:
            self.status_var.set("请等待分析范围变更完成。")
            return
        self.status_var.set("正在重做并追加审计记录…")
        reason = self._reason()
        self.jobs.submit(
            lambda: self.service.review_store.redo(
                actor=self.actor, session_id=self.session_id, reason=reason
            ),
            lambda result: self._finish_history("重做完成。", result),
            lambda error: self._history_error("重做", error),
        )

    def _finish_history(self, message: str, result: dict[str, Any] | None) -> None:
        if result is not None:
            self.selected_event_id = result["event_id"]
        self.reload_window()
        self.status_var.set(message)

    def _history_error(self, operation: str, error: BaseException) -> None:
        self.status_var.set(f"{operation}失败：{error}")

    def _artifact(self, role: str) -> Path:
        for record in self.service.project.manifest["artifacts"]:
            if record["role"] == role:
                return resolve_project_path(self.service.project.project_dir, record["path"])
        raise ValueError(f"missing project artifact: {role}")

    def change_range(self) -> None:
        if self.project_mutation_pending:
            self.status_var.set("已有分析范围变更正在进行。")
            return
        start = self.simpledialog.askstring(
            APP_TITLE,
            "新的分析闭区间起点（分钟）：",
            initialvalue=_minutes_text(self.service.analysis_start_ns),
            parent=self.app.root,
        )
        if start is None:
            return
        end = self.simpledialog.askstring(
            APP_TITLE,
            "新的分析闭区间终点（分钟）：",
            initialvalue=_minutes_text(self.service.analysis_end_ns),
            parent=self.app.root,
        )
        if end is None:
            return
        self.project_mutation_pending = True
        self.status_var.set("正在只读计算分析范围差异预览…")
        project_dir = self.service.project.project_dir
        reason = self._reason()

        def preview_success(preview) -> None:
            details = (
                "项目文件尚未发生变化。\n\n"
                f"原范围：{_minutes_text(int(preview.old_analysis_range['start_ns']))}–"
                f"{_minutes_text(int(preview.old_analysis_range['end_ns']))} min\n"
                f"新范围：{_minutes_text(preview.new_analysis_range.start_ns)}–"
                f"{_minutes_text(preview.new_analysis_range.end_ns)} min\n\n"
                f"保留稳定 EventID：{preview.mapped_count}\n"
                f"转入历史失效的旧自动审阅：{preview.stale_count}\n"
                f"存在歧义的映射：{len(preview.plan.ambiguous_event_ids)}\n"
                f"新增自动事件：{preview.new_count}\n"
                f"保留或重新判定的人工事件：{len(preview.plan.manual_event_ids)}\n\n"
                "是否应用这个新代次？上一代次和完整审计历史会保持不可变并可恢复。"
            )
            confirmed = self.messagebox.askyesno(
                "确认分析范围差异",
                details,
                parent=self.app.root,
            )
            if not confirmed:
                self.project_mutation_pending = False
                self.status_var.set("已取消范围变更；当前活动代次未改变。")
                return
            self.status_var.set("正在通过一次原子 manifest 切换发布已确认代次…")
            self.jobs.submit(
                lambda: apply_range_change(
                    preview,
                    confirmed=True,
                    actor=self.actor,
                    session_id=self.session_id,
                    reason=reason,
                ),
                self._range_apply_success,
                self._range_change_error,
            )

        self.jobs.submit(
            lambda: preview_range_change(project_dir, start, end),
            preview_success,
            self._range_change_error,
        )

    def _range_apply_success(self, project) -> None:
        self.service.close()
        self.service = ProjectWindowService.open(project.project_dir)
        self.model = OptimisticReviewModel(self.service.all_events())
        self.selected_event_id = None
        start = self.service.analysis_start_ns
        end = self.service.analysis_end_ns
        window = min(max(1, end - start), 10 * NANOSECONDS_PER_MINUTE)
        self.viewport = Viewport(start, end, start, window)
        self._sync_view_entries()
        self.project_mutation_pending = False
        self.reload_window()
        self.status_var.set("范围变更已提交；上一代次保留在项目历史中。")

    def _range_change_error(self, error: BaseException) -> None:
        self.project_mutation_pending = False
        self.status_var.set(f"范围变更失败，当前活动代次未改变：{error}")
        self.messagebox.showerror(APP_TITLE, str(error), parent=self.app.root)

    def export_human(self) -> None:
        if self.project_mutation_pending:
            self.status_var.set("导出前请等待分析范围变更完成。")
            return
        path = self.filedialog.asksaveasfilename(
            parent=self.app.root,
            title="导出已审阅事件",
            defaultextension=".csv",
            filetypes=(("CSV", "*.csv"),),
            initialdir=str(self.service.project.project_dir / "annotations/exports"),
        )
        if not path:
            return
        include_pending = bool(self.include_pending_var.get())
        reason = self._reason()
        self.status_var.set("正在导出已接受事件的审阅快照…")

        def work():
            events = self.service.review_store.list_events()
            result = export_human_csv(
                events,
                path,
                analysis_start_ns=self.service.analysis_start_ns,
                analysis_end_ns=self.service.analysis_end_ns,
                include_pending=include_pending,
            )
            self.service.review_store.record_export(
                actor=self.actor,
                session_id=self.session_id,
                reason=reason,
                details={
                    "contract": "human-csv-v1",
                    "file_name": result.path.name,
                    "sha256": result.sha256,
                    "size_bytes": result.size_bytes,
                    "row_count": result.row_count,
                    "statuses": result.statuses,
                    "analysis_start_ns": self.service.analysis_start_ns,
                    "analysis_end_ns": self.service.analysis_end_ns,
                },
            )
            return result

        self.jobs.submit(
            work,
            lambda result: self.status_var.set(
                f"已导出 {result.row_count:,} 行 · SHA-256 {result.sha256[:12]}…"
            ),
            lambda error: self.messagebox.showerror(APP_TITLE, str(error), parent=self.app.root),
        )

    def export_machine(self) -> None:
        if self.project_mutation_pending:
            self.status_var.set("导出前请等待分析范围变更完成。")
            return
        parent = self.filedialog.askdirectory(
            parent=self.app.root,
            title="选择新机器契约文件夹的上级目录",
        )
        if not parent:
            return
        name = self.simpledialog.askstring(
            APP_TITLE,
            "新机器契约文件夹名称：",
            initialvalue="ms-event-machine-contract",
            parent=self.app.root,
        )
        if not name:
            return
        target = Path(parent) / Path(name).name
        reason = self._reason()
        self.status_var.set("正在导出包含全部状态的机器契约…")

        def work():
            input_manifest = json.loads(self._artifact("input_manifest").read_text(encoding="utf-8"))
            protocol = json.loads(self._artifact("detector_protocol").read_text(encoding="utf-8"))
            result = export_machine_contract(
                self.service.review_store.list_events(),
                self.service.automatic,
                target,
                source_fingerprint=input_manifest["source_fingerprint"],
                detector_version=protocol["detector_version"],
                parameter_hash=protocol["parameter_hash"],
                generation_id=protocol["generation_id"],
                analysis_start_ns=self.service.analysis_start_ns,
                analysis_end_ns=self.service.analysis_end_ns,
                boundary_rule=self.service.project.manifest["analysis_range"]["boundary_rule"],
            )
            self.service.review_store.record_export(
                actor=self.actor,
                session_id=self.session_id,
                reason=reason,
                details={
                    "contract": "ms-event-machine-contract-v1",
                    "directory_name": result.output_dir.name,
                    "event_table_sha256": result.event_table_sha256,
                    "manifest_sha256": result.manifest_sha256,
                    "row_count": result.row_count,
                },
            )
            return result

        self.jobs.submit(
            work,
            lambda result: self.status_var.set(
                f"机器契约：{result.row_count:,} 行 · {result.manifest_sha256[:12]}…"
            ),
            lambda error: self.messagebox.showerror(APP_TITLE, str(error), parent=self.app.root),
        )

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.app.root.unbind("<KeyPress>")
        self.jobs.close()
        self.service.close()
        self.frame.destroy()


class MSDesktopApp:
    def __init__(self, root=None, *, recent_path: str | Path | None = None) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.root = root or tk.Tk()
        self.style = configure_theme(self.root)
        self.app_icon_32 = icon_photo(self.root, 32)
        self.app_icon_64 = icon_photo(self.root, 64)
        self.app_icon_128 = icon_photo(self.root, 128)
        self.app_icon_256 = icon_photo(self.root, 256)
        self.root.iconphoto(True, self.app_icon_256, self.app_icon_32)
        self.root.title(APP_TITLE)
        self.root.geometry("1240x800")
        self.root.minsize(1040, 680)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.recent = RecentProjects(recent_path or default_recent_path())
        self.current_view: ReviewView | None = None
        self.welcome = None
        self.show_welcome()

    def _clear(self) -> None:
        if self.current_view is not None:
            self.current_view.close()
            self.current_view = None
        if self.welcome is not None and self.welcome.winfo_exists():
            self.welcome.destroy()
        self.welcome = None

    def show_welcome(self) -> None:
        self._clear()
        self.root.title(APP_TITLE)
        frame = self.ttk.Frame(self.root, style="Page.TFrame")
        frame.pack(fill="both", expand=True)
        self.welcome = frame
        appbar = self.ttk.Frame(frame, style="Hero.TFrame", padding=(18, 9))
        appbar.pack(fill="x")
        self.ttk.Label(appbar, image=self.app_icon_32, style="HeroBody.TLabel").pack(
            side="left", padx=(0, 10)
        )
        brand = self.ttk.Frame(appbar, style="Hero.TFrame")
        brand.pack(side="left", fill="x", expand=True)
        self.ttk.Label(
            brand,
            text="MS Event Studio",
            style="HeroBody.TLabel",
            font=(font_family(), 12, "bold"),
        ).pack(anchor="w")
        self.ttk.Label(
            brand,
            text="质谱事件提取与可审计审阅",
            style="HeroSubtitle.TLabel",
            font=(font_family(), 9),
        ).pack(anchor="w")
        self.ttk.Button(
            appbar,
            text="使用说明",
            command=self.show_test_guide,
            style="Ghost.TButton",
        ).pack(side="right", padx=(10, 0))
        self.ttk.Label(
            appbar,
            text=f"v{__version__} · MS-only",
            style="HeroSubtitle.TLabel",
        ).pack(side="right")

        content = self.ttk.Frame(frame, style="Page.TFrame", padding=(32, 26))
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(4, weight=1)
        self.ttk.Label(content, text="MS 事件工作台", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.ttk.Label(content, text="从原始信号到可审阅事件", style="Title.TLabel").grid(
            row=1, column=0, sticky="w", pady=(3, 4)
        )
        self.ttk.Label(
            content,
            text="读取 PC34 / MS760 曲线，保留不可变自动证据，并将人工判断作为可追溯叠加层。",
            style="Muted.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(0, 18))

        actions = self.ttk.Frame(content, style="Page.TFrame")
        actions.grid(row=3, column=0, sticky="ew")
        actions.columnconfigure(0, weight=1, uniform="action")
        actions.columnconfigure(1, weight=1, uniform="action")
        new_card = self.ttk.Frame(actions, style="Card.TFrame", padding=20)
        new_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self.ttk.Label(new_card, text="01 · 新建项目", style="Eyebrow.TLabel").pack(anchor="w")
        self.ttk.Label(
            new_card,
            text="分析原始 MS 文本",
            style="SurfaceTitle.TLabel",
        ).pack(anchor="w", pady=(3, 5))
        self.ttk.Label(
            new_card,
            text="单遍读取并校验源文件，选择分析闭区间，再原子创建便携项目。",
            style="SurfaceMuted.TLabel",
            wraplength=430,
            justify="left",
        ).pack(anchor="w", fill="x", pady=(0, 14))
        self.ttk.Button(
            new_card,
            text="新建项目  →",
            command=self.new_project,
            style="Primary.TButton",
        ).pack(anchor="w")

        open_card = self.ttk.Frame(actions, style="Card.TFrame", padding=20)
        open_card.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self.ttk.Label(open_card, text="02 · 继续工作", style="Eyebrow.TLabel").pack(anchor="w")
        self.ttk.Label(
            open_card,
            text="打开已有项目",
            style="SurfaceTitle.TLabel",
        ).pack(anchor="w", pady=(3, 5))
        self.ttk.Label(
            open_card,
            text="校验项目绑定，恢复活动检测代次、人工状态与完整审计历史。",
            style="SurfaceMuted.TLabel",
            wraplength=430,
            justify="left",
        ).pack(anchor="w", fill="x", pady=(0, 14))
        self.ttk.Button(
            open_card,
            text="选择项目  →",
            command=self.choose_project,
            style="Secondary.TButton",
        ).pack(anchor="w")

        lower = self.ttk.Frame(content, style="Page.TFrame")
        lower.grid(row=4, column=0, sticky="nsew", pady=(14, 0))
        lower.columnconfigure(0, weight=1)
        lower.rowconfigure(2, weight=1)
        guide = self.ttk.Frame(lower, style="Card.TFrame", padding=(20, 15))
        guide.grid(row=0, column=0, sticky="ew")
        for column in range(3):
            guide.columnconfigure(column, weight=1, uniform="workflow")
        self.ttk.Label(
            guide,
            text="01  解析与校验\n源文件保持只读，完整计算 SHA-256",
            style="Surface.TLabel",
            wraplength=290,
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        self.ttk.Label(
            guide,
            text="02  审阅与校订\n峰顶绑定真实扫描，状态与修改可追溯",
            style="Surface.TLabel",
            wraplength=290,
            justify="left",
        ).grid(row=0, column=1, sticky="w", padx=18)
        self.ttk.Label(
            guide,
            text="03  受控导出\n人用 CSV 与版本化 machine contract",
            style="Surface.TLabel",
            wraplength=290,
            justify="left",
        ).grid(row=0, column=2, sticky="w")

        test_card = self.ttk.Frame(lower, style="Card.TFrame", padding=(20, 13))
        test_card.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        test_card.columnconfigure(0, weight=1)
        self.ttk.Label(test_card, text="第一次使用？", style="Eyebrow.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.ttk.Label(
            test_card,
            text="先运行 2 分钟引导测试：包含 3 个已知自动事件和 1 个补峰目标。",
            style="SurfaceMuted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))
        self.ttk.Button(
            test_card,
            text="开始引导测试",
            command=self.start_guided_test,
            style="Primary.TButton",
        ).grid(row=0, column=1, rowspan=2, sticky="e", padx=(18, 0))

        recent = self.recent.load()
        recent_card = self.ttk.Frame(lower, style="Card.TFrame", padding=18)
        recent_card.grid(row=2, column=0, sticky="nsew", pady=(14, 0))
        self.ttk.Label(recent_card, text="最近项目", style="Eyebrow.TLabel").pack(
            anchor="w", pady=(0, 7)
        )
        if recent:
            for row in recent[:4]:
                self.ttk.Button(
                    recent_card,
                    text=f"{row.display_name}  →",
                    command=lambda path=row.path: self.open_project(path),
                    style="Secondary.TButton",
                ).pack(fill="x", pady=3)
        else:
            self.ttk.Label(
                recent_card,
                text="暂无最近项目。可以新建项目，或先开始引导测试。",
                style="SurfaceMuted.TLabel",
            ).pack(anchor="w", pady=6)

    def new_project(self) -> None:
        NewProjectDialog(self)

    def show_test_guide(self) -> None:
        self.messagebox.showinfo(
            "引导测试 · 约 10 分钟",
            (
                "1. 创建并分析一次性的 2 分钟合成源。\n"
                "2. 确认 0.5、1.0、1.5 min 处有三个自动峰顶。\n"
                "3. 测试接受、排除、待定、恢复，以及撤销/重做。\n"
                "4. 在 0.75 min 附近补充弱峰，并调整一个自动峰顶。\n"
                "5. 导出人用 CSV 和机器契约。\n"
                "6. 将范围改为 0.6–2 min，确认历史失效事件不进入导出。\n\n"
                "引导源和测试项目均为一次性材料，不会接触 LMA Studio。"
            ),
            parent=self.root,
        )

    def start_guided_test(self) -> None:
        parent = self.filedialog.askdirectory(
            parent=self.root,
            title="选择用于存放一次性引导测试文件的目录",
        )
        if not parent:
            return
        try:
            assets = create_guided_test_assets(parent)
        except Exception as exc:
            self.messagebox.showerror(APP_TITLE, str(exc), parent=self.root)
            return
        NewProjectDialog(
            self,
            initial_source=assets.source_path,
            initial_target=assets.project_path,
            initial_name="引导测试：PC34 事件审阅",
            guided=True,
        )

    def choose_project(self) -> None:
        path = self.filedialog.askdirectory(parent=self.root, title="打开 MS Event Studio 项目")
        if path:
            self.open_project(path)

    def open_project(self, path: str | Path) -> None:
        try:
            view = ReviewView(self, path)
        except Exception as exc:
            self.messagebox.showerror(APP_TITLE, str(exc), parent=self.root)
            return
        self._clear()
        self.current_view = view
        # ReviewView constructed its frame before the welcome was removed; pack
        # it again after clearing to keep creation failure non-destructive.
        view.frame.pack(fill="both", expand=True)
        name = str(view.service.project.manifest["display_name"])
        self.root.title(f"{APP_TITLE} — {name}")
        try:
            self.recent.remember(view.service.project.project_dir, name)
        except OSError:
            pass

    def close(self) -> None:
        self._clear()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main(argv: list[str] | None = None) -> int:
    import argparse
    import traceback

    parser = argparse.ArgumentParser(prog="ms-event-studio-gui")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-report", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    def smoke_report(payload: dict[str, Any]) -> None:
        if args.smoke_report is not None:
            args.smoke_report.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )

    try:
        scientific_smoke = _packaged_scientific_smoke() if args.smoke_test else None
        app = MSDesktopApp()
    except Exception as exc:
        smoke_report(
            {
                "status": "error",
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
        print(f"{APP_TITLE} failed to start: {exc}", file=sys.stderr)
        return 2
    if args.smoke_test:
        app.root.withdraw()
        def finish_smoke() -> None:
            smoke_report(
                {
                    "status": "ok",
                    "application_version": __version__,
                    "window_system": app.root.tk.call("tk", "windowingsystem"),
                    "scientific_runtime": scientific_smoke,
                }
            )
            app.close()

        app.root.after(250, finish_smoke)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
