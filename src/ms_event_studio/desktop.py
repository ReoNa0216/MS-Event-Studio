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
from .window_service import ProjectWindowService


APP_TITLE = "MS Event Studio"
FILTER_LABELS = {
    "all": "All",
    "unreviewed": "Unreviewed",
    "accepted": "Accepted",
    "rejected": "Rejected",
    "pending": "Pending",
    "manual_added": "Manual added",
    "manual_adjusted": "Manual adjusted",
    "stale": "Stale generation",
}


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
    def __init__(self, app: "MSDesktopApp") -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.title("New MS Event Studio project")
        self.window.geometry("720x490")
        self.window.minsize(640, 440)
        self.window.transient(app.root)
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.prepared: PreparedProjectSource | None = None
        self.prepared_path: Path | None = None
        self.state = CreationState()
        self.messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.closing = False

        outer = ttk.Frame(self.window, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Create a project", font=("TkDefaultFont", 16, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 14)
        )
        outer.columnconfigure(1, weight=1)

        self.source_var = tk.StringVar()
        self.target_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.start_var = tk.StringVar(value="0")
        self.end_var = tk.StringVar(value="60")
        fields = (
            ("Raw MS text", self.source_var, self._browse_source),
            ("Project directory", self.target_var, self._browse_target),
        )
        for row, (label, variable, command) in enumerate(fields, start=1):
            ttk.Label(outer, text=label).grid(row=row, column=0, sticky="w", pady=6)
            ttk.Entry(outer, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=8)
            ttk.Button(outer, text="Browse…", command=command).grid(row=row, column=2)
        ttk.Label(outer, text="Display name").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Entry(outer, textvariable=self.name_var).grid(row=3, column=1, columnspan=2, sticky="ew", padx=8)

        range_frame = ttk.LabelFrame(outer, text="Closed analysis range (minutes)", padding=10)
        range_frame.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(12, 5))
        range_frame.columnconfigure(1, weight=1)
        range_frame.columnconfigure(3, weight=1)
        ttk.Label(range_frame, text="Start").grid(row=0, column=0)
        self.start_entry = ttk.Entry(range_frame, textvariable=self.start_var, width=14)
        self.start_entry.grid(row=0, column=1, sticky="ew", padx=(7, 18))
        ttk.Label(range_frame, text="End").grid(row=0, column=2)
        self.end_entry = ttk.Entry(range_frame, textvariable=self.end_var, width=14)
        self.end_entry.grid(row=0, column=3, sticky="ew", padx=(7, 0))
        self.extent_var = tk.StringVar(value="Analyze the source once to reveal its available range.")
        ttk.Label(range_frame, textvariable=self.extent_var, foreground="#465568").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(8, 0)
        )

        advanced = ttk.LabelFrame(outer, text="Scientific configuration (read only)", padding=10)
        advanced.grid(row=5, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Label(
            advanced,
            text="PC34 760.5851 / QC 782.5616 · closed ±12 ppm · adaptive v0.4.4 baseline",
        ).pack(anchor="w")
        ttk.Label(
            advanced,
            text="Changing scientific parameters is not available in Phase 2 without a versioned recomputation.",
            foreground="#59677a",
        ).pack(anchor="w", pady=(4, 0))

        self.progress = ttk.Progressbar(outer, maximum=1.0, mode="determinate")
        self.progress.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(12, 4))
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(outer, textvariable=self.status_var).grid(row=7, column=0, columnspan=2, sticky="w")
        self.primary = ttk.Button(outer, text="Analyze source", command=self._primary)
        self.primary.grid(row=7, column=2, sticky="e")

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
            self.extent_var.set("Source changed; analyze it before project creation.")
            self.primary.configure(text="Analyze source")

    def _browse_source(self) -> None:
        chosen = self.filedialog.askopenfilename(
            parent=self.window,
            title="Select raw MS text export",
            filetypes=(("MS text exports", "*.txt"), ("All files", "*.*")),
        )
        if chosen:
            self.source_var.set(chosen)
            if not self.name_var.get().strip():
                self.name_var.set(Path(chosen).stem)

    def _browse_target(self) -> None:
        chosen = self.filedialog.askdirectory(
            parent=self.window,
            title="Select an empty project directory or its new location",
            mustexist=False,
        )
        if chosen:
            self.target_var.set(chosen)

    def _set_running(self, text: str) -> None:
        self.primary.configure(text="Cancel")
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
            self.status_var.set("Cancellation requested…")
            return
        source_text = self.source_var.get().strip()
        if not source_text:
            self.messagebox.showerror(APP_TITLE, "Choose a raw MS text file.", parent=self.window)
            return
        source = Path(source_text).resolve()
        if self.prepared is None or self.prepared_path != source:
            self._set_running("Reading, hashing, and extracting the MS source…")
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
                "Choose a project directory and display name.",
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
                "The closed analysis range must stay inside the inspected source extent.",
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
        self._set_running("Detecting events and atomically publishing the project…")
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
                    f"{progress.phase}: {gib:.2f}/{total_gib:.2f} GiB · "
                    f"{progress.parsed_spectra:,} spectra"
                )
            elif kind == "inspected":
                self.prepared = payload
                self.prepared_path = payload.source_path
                self.start_var.set(_minutes_text(payload.start_ns))
                self.end_var.set(_minutes_text(payload.end_ns))
                self.extent_var.set(
                    f"Available closed range: {_minutes_text(payload.start_ns)}–"
                    f"{_minutes_text(payload.end_ns)} min · {len(payload.parsed.scans):,} spectra"
                )
                self.progress.configure(value=1.0)
                self.status_var.set("Source inspection complete; choose the desired range and create.")
                self.primary.configure(text="Create project")
            elif kind == "created":
                self.progress.configure(value=1.0)
                self.status_var.set("Project created.")
                self.window.grab_release()
                self.window.destroy()
                self.app.open_project(payload.project_dir)
                return
            elif kind == "error":
                self.primary.configure(text="Create project" if self.prepared else "Analyze source")
                self.status_var.set("Cancelled" if self.state.cancel_requested else "Operation failed")
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
            self.status_var.set("Cancellation requested; waiting for a safe parser boundary…")
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

        self.frame = ttk.Frame(app.root)
        self.frame.pack(fill="both", expand=True)
        self._build_controls()
        self._build_body()
        self._bind_keys()
        self.reload_window()

    def _build_controls(self) -> None:
        tk, ttk = self.tk, self.ttk
        toolbar = ttk.Frame(self.frame, padding=(8, 7))
        toolbar.pack(fill="x")
        self.start_var = tk.StringVar(value=_minutes_text(self.viewport.start_ns))
        self.window_var = tk.StringVar(
            value=_minutes_text(self.viewport.window_ns)
        )
        self.filter_var = tk.StringVar(value="all")
        self.scale_var = tk.StringVar(value="linear")
        self.labels_var = tk.BooleanVar(value=True)
        self.include_pending_var = tk.BooleanVar(value=False)

        ttk.Label(toolbar, text="Start min").pack(side="left")
        ttk.Entry(toolbar, textvariable=self.start_var, width=10).pack(side="left", padx=(4, 10))
        ttk.Label(toolbar, text="Window min").pack(side="left")
        ttk.Entry(toolbar, textvariable=self.window_var, width=8).pack(side="left", padx=(4, 6))
        ttk.Button(toolbar, text="Go", command=self.apply_viewport).pack(side="left")
        ttk.Button(toolbar, text="◀", width=3, command=lambda: self.pan(-1)).pack(side="left", padx=(10, 2))
        ttk.Button(toolbar, text="▶", width=3, command=lambda: self.pan(1)).pack(side="left")

        ttk.Label(toolbar, text="Filter").pack(side="left", padx=(14, 4))
        filter_box = ttk.Combobox(
            toolbar,
            textvariable=self.filter_var,
            values=FILTERS,
            width=16,
            state="readonly",
        )
        filter_box.pack(side="left")
        filter_box.bind("<<ComboboxSelected>>", lambda _event: self.reload_window())
        ttk.Label(toolbar, text="Y").pack(side="left", padx=(14, 4))
        scale_box = ttk.Combobox(
            toolbar,
            textvariable=self.scale_var,
            values=("linear", "log1p"),
            width=8,
            state="readonly",
        )
        scale_box.pack(side="left")
        scale_box.bind("<<ComboboxSelected>>", lambda _event: self.render())
        ttk.Checkbutton(toolbar, text="Labels", variable=self.labels_var, command=self.render).pack(
            side="left", padx=(10, 0)
        )
        ttk.Button(toolbar, text="Change range…", command=self.change_range).pack(
            side="left", padx=(12, 0)
        )
        ttk.Button(toolbar, text="Home", command=self.app.show_welcome).pack(side="right")

    def _build_body(self) -> None:
        tk, ttk = self.tk, self.ttk
        pane = ttk.Panedwindow(self.frame, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        plot_frame = ttk.Frame(pane)
        side = ttk.Frame(pane, width=320)
        pane.add(plot_frame, weight=4)
        pane.add(side, weight=1)

        self.canvas = tk.Canvas(
            plot_frame,
            background="#ffffff",
            highlightthickness=1,
            highlightbackground="#b8c1cc",
            takefocus=True,
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self.render())
        self.canvas.bind("<Button-1>", self._canvas_click)
        self.canvas.bind("<Motion>", self._canvas_motion)

        self.hover_var = tk.StringVar(value="Ready")
        ttk.Label(plot_frame, textvariable=self.hover_var, anchor="w").pack(fill="x", pady=(4, 0))

        ttk.Label(side, text="Selected event evidence", font=("TkDefaultFont", 12, "bold")).pack(
            anchor="w", pady=(0, 6)
        )
        self.details = tk.Text(side, height=18, width=42, wrap="word", state="disabled")
        self.details.pack(fill="both", expand=True)

        status = ttk.LabelFrame(side, text="Review status", padding=6)
        status.pack(fill="x", pady=(8, 4))
        for column, (label, value) in enumerate(
            (("Accept [A]", "accepted"), ("Reject [R]", "rejected"), ("Pending [P]", "pending"))
        ):
            ttk.Button(status, text=label, command=lambda value=value: self.set_status(value)).grid(
                row=0, column=column, sticky="ew", padx=2
            )
            status.columnconfigure(column, weight=1)
        ttk.Button(status, text="Unreviewed [U]", command=lambda: self.set_status("unreviewed")).grid(
            row=1, column=0, sticky="ew", padx=2, pady=(5, 0)
        )
        ttk.Button(status, text="Restore", command=self.restore).grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=2, pady=(5, 0)
        )

        edit = ttk.LabelFrame(side, text="Physical edit", padding=6)
        edit.pack(fill="x", pady=4)
        self.add_button = ttk.Button(edit, text="Add event [+]", command=lambda: self.set_mode("add"))
        self.add_button.grid(row=0, column=0, sticky="ew", padx=2)
        self.adjust_button = ttk.Button(
            edit, text="Adjust apex [M]", command=lambda: self.set_mode("adjust")
        )
        self.adjust_button.grid(row=0, column=1, sticky="ew", padx=2)
        edit.columnconfigure(0, weight=1)
        edit.columnconfigure(1, weight=1)
        ttk.Button(edit, text="Undo [Ctrl+Z]", command=self.undo).grid(
            row=1, column=0, sticky="ew", padx=2, pady=(5, 0)
        )
        ttk.Button(edit, text="Redo [Ctrl+Y]", command=self.redo).grid(
            row=1, column=1, sticky="ew", padx=2, pady=(5, 0)
        )

        ttk.Label(side, text="Reason (stored in audit)").pack(anchor="w", pady=(6, 2))
        self.reason_var = tk.StringVar()
        ttk.Entry(side, textvariable=self.reason_var).pack(fill="x")

        export = ttk.LabelFrame(side, text="Export", padding=6)
        export.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(export, text="Include pending in human CSV", variable=self.include_pending_var).pack(
            anchor="w"
        )
        ttk.Button(export, text="Human CSV…", command=self.export_human).pack(fill="x", pady=(5, 2))
        ttk.Button(export, text="Machine contract…", command=self.export_machine).pack(fill="x")

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self.frame, textvariable=self.status_var, anchor="w", padding=(8, 3)).pack(fill="x")

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

    def reload_window(self, *, keep_model: bool = False) -> None:
        try:
            snapshot = self.service.window(
                self._request(),
                status_filter=self.filter_var.get(),
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
            f"{len(snapshot.events):,} visible events · {len(snapshot.trace):,} trace points · "
            f"display bucket {snapshot.bucket_size} · one SQLite snapshot"
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
        rows = filter_events(self.model.events(), self.filter_var.get())
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
            log_scale=self.scale_var.get() == "log1p",
        )
        self.transform = transform
        canvas.create_rectangle(
            transform.left,
            transform.top,
            width - transform.right,
            height - transform.bottom,
            outline="#c7ced8",
            fill="#fbfcfe",
        )
        for index in range(6):
            fraction = index / 5
            x = transform.left + fraction * transform.plot_width
            time_ns = int(round(transform.start_ns + fraction * (transform.end_ns - transform.start_ns)))
            canvas.create_line(x, transform.top, x, height - transform.bottom, fill="#edf0f4")
            canvas.create_text(
                x,
                height - transform.bottom + 15,
                text=f"{time_ns / NANOSECONDS_PER_MINUTE:.3f}",
                fill="#526174",
                font=("TkDefaultFont", 8),
            )
        canvas.create_text(
            8,
            transform.top,
            anchor="nw",
            text=("log1p " if transform.log_scale else "") + f"max {maximum:.4g}",
            fill="#526174",
            font=("TkDefaultFont", 8),
        )
        if len(trace):
            coordinates: list[float] = []
            for time_ns, intensity in zip(
                trace["scan_time_ns"].astype("int64"), signal, strict=True
            ):
                coordinates.extend((transform.x_for_time(int(time_ns)), transform.y_for_signal(float(intensity))))
            if len(coordinates) >= 4:
                canvas.create_line(*coordinates, fill="#1769aa", width=1.25, tags=("trace",))

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
            outline = "#111827" if selected else encoding.color
            marker_size = 6 if selected else 5
            self._draw_marker(x, y, marker_size, encoding.shape, encoding.color, outline)
            if row.get("write_pending"):
                canvas.create_oval(x - 10, y - 10, x + 10, y + 10, outline="#111827", dash=(2, 2))
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
            canvas.create_text(legend_x, transform.top + 10, text=f"{token} {status}", anchor="w", fill=encoding.color)
            legend_x += 92
        if self.pending_click_ns is not None:
            x = transform.x_for_time(self.pending_click_ns)
            canvas.create_line(x, transform.top, x, height - transform.bottom, fill="#111827", dash=(4, 3), width=2)

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
            self.status_var.set("Wait for the range change to finish.")
            return
        current = self._selected()
        if current is None:
            self.status_var.set("Select an event first.")
            return
        try:
            token = self.model.begin_status(current["event_id"], status)
        except Exception as exc:
            self.status_var.set(str(exc))
            return
        self.render()
        self._update_details()
        self.status_var.set(f"Saving {status}…")
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
            self.status_var.set("Wait for the range change to finish.")
            return
        current = self._selected()
        if current is None:
            self.status_var.set("Select an event first.")
            return
        try:
            token = self.model.begin_patch(current["event_id"], self._restore_patch(current))
        except Exception as exc:
            self.status_var.set(str(exc))
            return
        self.render()
        self._update_details()
        self.status_var.set("Restoring immutable/created apex…")
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
        self.status_var.set("Saved and appended to audit history.")

    def _fail_optimistic(self, token, error: BaseException) -> None:
        self.model.rollback(token, error)
        self.render()
        self._update_details()
        self.status_var.set(f"Save failed; visual state rolled back: {error}")
        self.messagebox.showerror(APP_TITLE, str(error), parent=self.app.root)

    def set_mode(self, mode: str) -> None:
        if mode not in {"select", "add", "adjust"}:
            raise ValueError(mode)
        if mode == "adjust" and self._selected() is None:
            self.status_var.set("Select an event before adjusting its apex.")
            return
        self.interaction_mode = mode
        self.pending_click_ns = None
        messages = {
            "select": "Selection mode.",
            "add": "Add mode: click near a real positive PC34 local apex; Esc cancels.",
            "adjust": "Adjust mode: click within the permitted physical support; Esc cancels.",
        }
        self.status_var.set(messages[mode])
        self.canvas.configure(cursor="crosshair" if mode != "select" else "arrow")
        self.render()

    def _submit_add(self, time_ns: int) -> None:
        if self.project_mutation_pending:
            self.status_var.set("Wait for the range change to finish.")
            return
        self.pending_click_ns = int(time_ns)
        self.render()
        self.status_var.set("Snapping and saving manual event…")
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
            self.status_var.set(f"Added at real scan; snap offset {result['snap_offset_sec']:.6g} s.")

        def failure(error):
            self.pending_click_ns = None
            if isinstance(error, ExistingEventNavigation):
                self.interaction_mode = "select"
                self.canvas.configure(cursor="arrow")
                self.select_event(error.event_id)
                self.status_var.set("That physical evidence already belongs to an event; navigated to it.")
            else:
                self.render()
                self.status_var.set(f"Add failed; no event created: {error}")
                self.messagebox.showerror(APP_TITLE, str(error), parent=self.app.root)

        self.jobs.submit(work, success, failure)

    def _submit_adjust(self, time_ns: int) -> None:
        if self.project_mutation_pending:
            self.status_var.set("Wait for the range change to finish.")
            return
        current = self._selected()
        if current is None:
            self.set_mode("select")
            return
        self.pending_click_ns = int(time_ns)
        self.render()
        self.status_var.set("Snapping and saving adjusted apex…")
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
            self.status_var.set(f"Apex adjusted; snap offset {result['snap_offset_sec']:.6g} s.")

        def failure(error):
            self.pending_click_ns = None
            self.render()
            self.status_var.set(f"Adjust failed; visual state unchanged: {error}")
            self.messagebox.showerror(APP_TITLE, str(error), parent=self.app.root)

        self.jobs.submit(work, success, failure)

    def undo(self) -> None:
        if self.project_mutation_pending:
            self.status_var.set("Wait for the range change to finish.")
            return
        self.status_var.set("Undoing and appending audit…")
        reason = self._reason()
        self.jobs.submit(
            lambda: self.service.review_store.undo(
                actor=self.actor, session_id=self.session_id, reason=reason
            ),
            lambda result: self._finish_history("Undo complete.", result),
            lambda error: self._history_error("Undo", error),
        )

    def redo(self) -> None:
        if self.project_mutation_pending:
            self.status_var.set("Wait for the range change to finish.")
            return
        self.status_var.set("Redoing and appending audit…")
        reason = self._reason()
        self.jobs.submit(
            lambda: self.service.review_store.redo(
                actor=self.actor, session_id=self.session_id, reason=reason
            ),
            lambda result: self._finish_history("Redo complete.", result),
            lambda error: self._history_error("Redo", error),
        )

    def _finish_history(self, message: str, result: dict[str, Any] | None) -> None:
        if result is not None:
            self.selected_event_id = result["event_id"]
        self.reload_window()
        self.status_var.set(message)

    def _history_error(self, operation: str, error: BaseException) -> None:
        self.status_var.set(f"{operation} failed: {error}")

    def _artifact(self, role: str) -> Path:
        for record in self.service.project.manifest["artifacts"]:
            if record["role"] == role:
                return resolve_project_path(self.service.project.project_dir, record["path"])
        raise ValueError(f"missing project artifact: {role}")

    def change_range(self) -> None:
        if self.project_mutation_pending:
            self.status_var.set("A range change is already running.")
            return
        start = self.simpledialog.askstring(
            APP_TITLE,
            "New closed analysis start (minutes):",
            initialvalue=_minutes_text(self.service.analysis_start_ns),
            parent=self.app.root,
        )
        if start is None:
            return
        end = self.simpledialog.askstring(
            APP_TITLE,
            "New closed analysis end (minutes):",
            initialvalue=_minutes_text(self.service.analysis_end_ns),
            parent=self.app.root,
        )
        if end is None:
            return
        self.project_mutation_pending = True
        self.status_var.set("Computing a read-only range-change diff preview…")
        project_dir = self.service.project.project_dir
        reason = self._reason()

        def preview_success(preview) -> None:
            details = (
                "No project files have changed yet.\n\n"
                f"Old range: {_minutes_text(int(preview.old_analysis_range['start_ns']))}–"
                f"{_minutes_text(int(preview.old_analysis_range['end_ns']))} min\n"
                f"New range: {_minutes_text(preview.new_analysis_range.start_ns)}–"
                f"{_minutes_text(preview.new_analysis_range.end_ns)} min\n\n"
                f"Mapped stable EventIDs: {preview.mapped_count}\n"
                f"Old automatic reviews marked stale: {preview.stale_count}\n"
                f"Ambiguous mappings: {len(preview.plan.ambiguous_event_ids)}\n"
                f"New automatic events: {preview.new_count}\n"
                f"Manual events retained/evaluated: {len(preview.plan.manual_event_ids)}\n\n"
                "Apply this new generation? The previous generation and full audit history "
                "will remain immutable and recoverable."
            )
            confirmed = self.messagebox.askyesno(
                "Confirm range-change diff",
                details,
                parent=self.app.root,
            )
            if not confirmed:
                self.project_mutation_pending = False
                self.status_var.set("Range change cancelled; the active generation is unchanged.")
                return
            self.status_var.set("Publishing the confirmed generation with one atomic manifest switch…")
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
        self.status_var.set("Range change committed; previous generation retained in project history.")

    def _range_change_error(self, error: BaseException) -> None:
        self.project_mutation_pending = False
        self.status_var.set(f"Range change failed; active generation unchanged: {error}")
        self.messagebox.showerror(APP_TITLE, str(error), parent=self.app.root)

    def export_human(self) -> None:
        if self.project_mutation_pending:
            self.status_var.set("Wait for the range change to finish before exporting.")
            return
        path = self.filedialog.asksaveasfilename(
            parent=self.app.root,
            title="Export reviewed events",
            defaultextension=".csv",
            filetypes=(("CSV", "*.csv"),),
            initialdir=str(self.service.project.project_dir / "annotations/exports"),
        )
        if not path:
            return
        include_pending = bool(self.include_pending_var.get())
        reason = self._reason()
        self.status_var.set("Exporting accepted review snapshot…")

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
                f"Exported {result.row_count:,} rows · SHA-256 {result.sha256[:12]}…"
            ),
            lambda error: self.messagebox.showerror(APP_TITLE, str(error), parent=self.app.root),
        )

    def export_machine(self) -> None:
        if self.project_mutation_pending:
            self.status_var.set("Wait for the range change to finish before exporting.")
            return
        parent = self.filedialog.askdirectory(
            parent=self.app.root,
            title="Choose the parent directory for a new machine-contract folder",
        )
        if not parent:
            return
        name = self.simpledialog.askstring(
            APP_TITLE,
            "New machine-contract folder name:",
            initialvalue="ms-event-machine-contract",
            parent=self.app.root,
        )
        if not name:
            return
        target = Path(parent) / Path(name).name
        reason = self._reason()
        self.status_var.set("Exporting all-status machine contract…")

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
                f"Machine contract: {result.row_count:,} rows · {result.manifest_sha256[:12]}…"
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
        self.root.title(APP_TITLE)
        self.root.geometry("1240x780")
        self.root.minsize(900, 600)
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
        frame = self.ttk.Frame(self.root, padding=36)
        frame.pack(fill="both", expand=True)
        self.welcome = frame
        self.ttk.Label(frame, text=APP_TITLE, font=("TkDefaultFont", 24, "bold")).pack(pady=(60, 8))
        self.ttk.Label(
            frame,
            text="MS-only event extraction and auditable review",
            foreground="#526174",
        ).pack(pady=(0, 28))
        actions = self.ttk.Frame(frame)
        actions.pack()
        self.ttk.Button(actions, text="New project", width=22, command=self.new_project).grid(
            row=0, column=0, padx=7
        )
        self.ttk.Button(actions, text="Open project", width=22, command=self.choose_project).grid(
            row=0, column=1, padx=7
        )
        recent = self.recent.load()
        if recent:
            self.ttk.Label(frame, text="Recent projects", font=("TkDefaultFont", 12, "bold")).pack(
                pady=(42, 8)
            )
            for row in recent:
                self.ttk.Button(
                    frame,
                    text=row.display_name,
                    width=46,
                    command=lambda path=row.path: self.open_project(path),
                ).pack(pady=3)
        self.ttk.Label(
            frame,
            text="Recent-project buttons show friendly names only; scientific schema details stay inside the project.",
            foreground="#64748b",
        ).pack(side="bottom", pady=12)

    def new_project(self) -> None:
        NewProjectDialog(self)

    def choose_project(self) -> None:
        path = self.filedialog.askdirectory(parent=self.root, title="Open an MS Event Studio project")
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
