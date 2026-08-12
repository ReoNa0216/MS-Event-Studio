"""Headless desktop state primitives shared by Tk and automated UI tests."""

from __future__ import annotations

import copy
import json
import math
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


FILTERS = (
    "all",
    "unreviewed",
    "accepted",
    "rejected",
    "pending",
    "manual_added",
    "manual_adjusted",
    "stale",
)

STATUS_LABELS = {
    "unreviewed": "未审阅",
    "accepted": "已接受",
    "rejected": "已排除",
    "pending": "待定",
}
ORIGIN_LABELS = {
    "automatic": "自动识别",
    "manual_added": "人工补充",
    "manual_adjusted": "人工调整",
}
QUALITY_FLAG_LABELS = {
    "collision_risk_high": "碰撞风险高（collision_risk_high）",
    "broad_peak_width_gt_1p5_sec": "宽峰 > 1.5 s（broad_peak_width_gt_1p5_sec）",
    "low_quality_scan_window": "扫描窗口质量低（low_quality_scan_window）",
    "low_array_length_lt_6000_window": "数组长度偏低 < 6000（low_array_length_lt_6000_window）",
    "low_array_length_lt_1000_window": "数组长度过低 < 1000（low_array_length_lt_1000_window）",
    "low_tic_lt_1e6_window": "TIC 偏低 < 1e6（low_tic_lt_1e6_window）",
}


@dataclass(frozen=True, slots=True)
class PlotTransform:
    width: int
    height: int
    start_ns: int
    end_ns: int
    maximum_signal: float
    log_scale: bool = False
    left: int = 58
    right: int = 18
    top: int = 22
    bottom: int = 42

    def __post_init__(self) -> None:
        if self.width <= self.left + self.right or self.height <= self.top + self.bottom:
            raise ValueError("plot area is too small")
        if self.end_ns <= self.start_ns:
            raise ValueError("plot time extent must be positive")
        if not math.isfinite(float(self.maximum_signal)) or self.maximum_signal < 0:
            raise ValueError("plot maximum signal must be finite and non-negative")

    @property
    def plot_width(self) -> int:
        return self.width - self.left - self.right

    @property
    def plot_height(self) -> int:
        return self.height - self.top - self.bottom

    def x_for_time(self, time_ns: int) -> float:
        fraction = (int(time_ns) - self.start_ns) / (self.end_ns - self.start_ns)
        return self.left + min(1.0, max(0.0, fraction)) * self.plot_width

    def time_for_x(self, x: float) -> int:
        fraction = (float(x) - self.left) / self.plot_width
        fraction = min(1.0, max(0.0, fraction))
        return int(round(self.start_ns + fraction * (self.end_ns - self.start_ns)))

    def y_for_signal(self, value: float) -> float:
        maximum = max(float(self.maximum_signal), 0.0)
        numeric = max(0.0, float(value))
        if maximum <= 0:
            fraction = 0.0
        elif self.log_scale:
            fraction = math.log1p(numeric) / math.log1p(maximum)
        else:
            fraction = numeric / maximum
        return self.top + (1.0 - min(1.0, max(0.0, fraction))) * self.plot_height


def _fmt(value: Any, digits: int = 6) -> str:
    if value is None:
        return "—"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(numeric):
        return "—"
    return f"{numeric:.{digits}g}"


def evidence_lines(
    event: dict[str, Any] | None,
    scan: dict[str, Any] | None,
    automatic: dict[str, Any] | None,
) -> tuple[str, ...]:
    if event is None:
        return ("请选择一个事件以查看其物理证据。",)
    scan = scan or {}
    automatic = automatic or {}
    quality_names = (
        "collision_risk_high",
        "broad_peak_width_gt_1p5_sec",
        "low_quality_scan_window",
        "low_array_length_lt_6000_window",
        "low_array_length_lt_1000_window",
        "low_tic_lt_1e6_window",
    )
    flags = [QUALITY_FLAG_LABELS[name] for name in quality_names if bool(automatic.get(name, False))]
    status = str(event.get("status", "—"))
    origin = str(event.get("origin", "—"))
    return (
        f"EventID: {event.get('event_id', '—')}",
        f"状态 / 来源: {STATUS_LABELS.get(status, status)} / {ORIGIN_LABELS.get(origin, origin)}",
        f"审阅修订号: {event.get('revision', '—')}",
        f"扫描 ID: {event.get('current_scan_id', '—')}",
        f"峰顶时间: {_fmt(event.get('current_apex_time_sec'))} s",
        f"PC34 强度: {_fmt(event.get('current_apex_intensity'))}",
        f"PC34 m/z: {_fmt(scan.get('pc34_760_mz_at_max_intensity'), 9)}",
        f"PC34 ppm 误差: {_fmt(scan.get('pc34_760_ppm_error_at_max_intensity'))}",
        f"MS782 强度: {_fmt(scan.get('qc_782_max_intensity'))}",
        f"TIC: {_fmt(scan.get('tic'))}",
        f"峰显著度 Prominence: {_fmt(automatic.get('peak_prominence'))}",
        f"峰宽 Width: {_fmt(automatic.get('peak_width_sec'))} s",
        f"原始 support: {_fmt(automatic.get('left_sec'))}–{_fmt(automatic.get('right_sec'))} s",
        f"吸附偏移: {_fmt(event.get('snap_offset_sec'))} s",
        "质量标记: " + ("；".join(flags) if flags else "无"),
    )


TEXT_INPUT_WIDGET_CLASSES = frozenset(
    {"Entry", "TEntry", "Text", "Spinbox", "TSpinbox", "Combobox", "TCombobox"}
)


def keyboard_command(
    keysym: str,
    *,
    control: bool,
    focus_widget_class: str | None = None,
) -> str | None:
    # Plain-letter and navigation shortcuts must never turn text entry into a
    # scientific write. This also leaves Ctrl+Z/Y to the focused text widget.
    if focus_widget_class in TEXT_INPUT_WIDGET_CLASSES:
        return None
    key = str(keysym)
    if control:
        return {"z": "undo", "Z": "undo", "y": "redo", "Y": "redo"}.get(key)
    return {
        "a": "accept",
        "A": "accept",
        "r": "reject",
        "R": "reject",
        "p": "pending",
        "P": "pending",
        "u": "unreviewed",
        "U": "unreviewed",
        "Left": "previous_window",
        "Right": "next_window",
        "bracketleft": "previous_event",
        "bracketright": "next_event",
        "plus": "add_mode",
        "equal": "add_mode",
        "m": "adjust_mode",
        "M": "adjust_mode",
        "Escape": "cancel_mode",
    }.get(key)


@dataclass(frozen=True, slots=True)
class Viewport:
    analysis_start_ns: int
    analysis_end_ns: int
    start_ns: int
    window_ns: int

    def __post_init__(self) -> None:
        values = (self.analysis_start_ns, self.analysis_end_ns, self.start_ns, self.window_ns)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise TypeError("viewport values must be integer nanoseconds")
        if self.analysis_end_ns < self.analysis_start_ns:
            raise ValueError("viewport analysis end precedes start")
        if self.window_ns <= 0:
            raise ValueError("viewport window must be positive")
        if not self.analysis_start_ns <= self.start_ns <= self.analysis_end_ns:
            raise ValueError("viewport start is outside the analysis range")

    @property
    def end_ns(self) -> int:
        return min(self.analysis_end_ns, self.start_ns + self.window_ns)

    def contains(self, time_ns: int) -> bool:
        return self.start_ns <= int(time_ns) <= self.end_ns

    def _clamped_start(self, proposed: int, window_ns: int) -> int:
        span = self.analysis_end_ns - self.analysis_start_ns
        if window_ns >= span:
            return self.analysis_start_ns
        maximum = self.analysis_end_ns - window_ns
        return min(max(int(proposed), self.analysis_start_ns), maximum)

    def pan(self, delta_ns: int) -> "Viewport":
        start = self._clamped_start(self.start_ns + int(delta_ns), self.window_ns)
        return Viewport(self.analysis_start_ns, self.analysis_end_ns, start, self.window_ns)

    def with_window(self, window_ns: int) -> "Viewport":
        requested = max(1, int(window_ns))
        span = self.analysis_end_ns - self.analysis_start_ns
        effective = max(1, min(requested, max(1, span)))
        start = self._clamped_start(self.start_ns, effective)
        return Viewport(self.analysis_start_ns, self.analysis_end_ns, start, effective)

    def with_start(self, start_ns: int) -> "Viewport":
        start = self._clamped_start(int(start_ns), self.window_ns)
        return Viewport(self.analysis_start_ns, self.analysis_end_ns, start, self.window_ns)


def filter_events(events: Iterable[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    if mode not in FILTERS:
        raise ValueError(f"unsupported event filter: {mode}")
    rows = [dict(row) for row in events]
    if mode == "all":
        rows = [row for row in rows if row.get("generation_state") != "stale"]
    elif mode == "stale":
        rows = [row for row in rows if row.get("generation_state") == "stale"]
    else:
        field = "origin" if mode.startswith("manual_") else "status"
        rows = [
            row
            for row in rows
            if row.get("generation_state") != "stale" and str(row.get(field)) == mode
        ]
    rows.sort(key=lambda row: (int(row["current_apex_time_ns"]), str(row["event_id"])))
    return rows


@dataclass(frozen=True, slots=True)
class VisualEncoding:
    color: str
    shape: str
    text_token: str
    dash: tuple[int, ...]


def event_visual_encoding(status: str, origin: str) -> VisualEncoding:
    status_map = {
        "unreviewed": ("#5f6b7a", "triangle", "U"),
        "accepted": ("#12805c", "circle", "A"),
        "rejected": ("#c2382b", "cross", "R"),
        "pending": ("#b26a00", "diamond", "P"),
    }
    if status not in status_map:
        raise ValueError(f"unsupported review status: {status}")
    color, shape, token = status_map[status]
    if origin == "manual_added":
        token += "+"
        dash = (2, 2)
    elif origin == "manual_adjusted":
        token += "~"
        dash = (5, 2)
    elif origin == "automatic":
        dash = ()
    else:
        raise ValueError(f"unsupported event origin: {origin}")
    return VisualEncoding(color=color, shape=shape, text_token=token, dash=dash)


@dataclass(frozen=True, slots=True)
class RecentProject:
    path: Path
    display_name: str
    opened_at: str


class RecentProjects:
    def __init__(self, path: str | Path, *, limit: int = 8) -> None:
        self.path = Path(path).resolve()
        self.limit = max(1, int(limit))

    def load(self) -> list[RecentProject]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        result: list[RecentProject] = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            path = row.get("path")
            name = row.get("display_name")
            opened = row.get("opened_at")
            if all(isinstance(value, str) and value for value in (path, name, opened)):
                result.append(RecentProject(Path(path), name, opened))
        return result[: self.limit]

    def remember(self, project_path: str | Path, display_name: str) -> None:
        resolved = Path(project_path).resolve()
        name = str(display_name).strip()
        if not name:
            raise ValueError("recent project display name cannot be empty")
        key = os.path.normcase(str(resolved))
        existing = [row for row in self.load() if os.path.normcase(str(row.path.resolve())) != key]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        rows = [RecentProject(resolved, name, now), *existing][: self.limit]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.writing-{uuid.uuid4().hex}")
        try:
            temporary.write_text(
                json.dumps(
                    [
                        {
                            "path": str(row.path),
                            "display_name": row.display_name,
                            "opened_at": row.opened_at,
                        }
                        for row in rows
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


class CreationState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running = False
        self.cancel_requested = False
        self.bytes_read = 0
        self.total_bytes = 0
        self.parsed_spectra = 0
        self.error: str | None = None
        self.result: Any = None

    @property
    def fraction(self) -> float:
        with self._lock:
            return 0.0 if self.total_bytes <= 0 else min(1.0, self.bytes_read / self.total_bytes)

    def start(self) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError("project creation is already running")
            self.running = True
            self.cancel_requested = False
            self.error = None
            self.result = None
            self.bytes_read = self.total_bytes = self.parsed_spectra = 0

    def update_progress(self, *, bytes_read: int, total_bytes: int, parsed_spectra: int) -> None:
        with self._lock:
            if not self.running:
                raise RuntimeError("cannot update an idle creation state")
            if int(total_bytes) < 0 or not 0 <= int(bytes_read) <= int(total_bytes):
                raise ValueError("invalid byte progress")
            self.bytes_read = int(bytes_read)
            self.total_bytes = int(total_bytes)
            self.parsed_spectra = int(parsed_spectra)

    def cancel(self) -> None:
        with self._lock:
            if self.running:
                self.cancel_requested = True

    def cancel_check(self) -> bool:
        with self._lock:
            return self.cancel_requested

    def finish(self, result: Any) -> None:
        with self._lock:
            self.result = result
            self.running = False

    def fail(self, error: BaseException) -> None:
        with self._lock:
            self.error = str(error)
            self.running = False


@dataclass(frozen=True, slots=True)
class OptimisticToken:
    token_id: str
    event_id: str
    before: dict[str, Any]


class OptimisticReviewModel:
    """Immediate visual state with explicit commit or failure rollback."""

    def __init__(self, events: Iterable[dict[str, Any]]) -> None:
        self._events = {str(row["event_id"]): copy.deepcopy(dict(row)) for row in events}
        self._tokens: dict[str, OptimisticToken] = {}
        self.last_error: str | None = None

    def event(self, event_id: str) -> dict[str, Any]:
        return self._events[str(event_id)]

    def events(self) -> list[dict[str, Any]]:
        return sorted(
            (copy.deepcopy(row) for row in self._events.values()),
            key=lambda row: (int(row["current_apex_time_ns"]), str(row["event_id"])),
        )

    def replace(self, events: Iterable[dict[str, Any]]) -> None:
        if self._tokens:
            raise RuntimeError("cannot replace events while a write is pending")
        self._events = {str(row["event_id"]): copy.deepcopy(dict(row)) for row in events}

    def begin_status(self, event_id: str, status: str) -> OptimisticToken:
        identity = str(event_id)
        current = self._events[identity]
        if current.get("write_pending"):
            raise RuntimeError("event already has a pending write")
        if status not in {"unreviewed", "accepted", "rejected", "pending"}:
            raise ValueError(f"invalid optimistic status: {status}")
        return self.begin_patch(identity, {"status": status})

    def begin_patch(self, event_id: str, patch: dict[str, Any]) -> OptimisticToken:
        identity = str(event_id)
        current = self._events[identity]
        if current.get("write_pending"):
            raise RuntimeError("event already has a pending write")
        token = OptimisticToken(uuid.uuid4().hex, identity, copy.deepcopy(current))
        optimistic = copy.deepcopy(current)
        optimistic.update(copy.deepcopy(dict(patch)))
        optimistic["write_pending"] = True
        self._events[identity] = optimistic
        self._tokens[token.token_id] = token
        self.last_error = None
        return token

    def commit(self, token: OptimisticToken, committed: dict[str, Any]) -> None:
        active = self._tokens.pop(token.token_id, None)
        if active != token:
            raise RuntimeError("optimistic write token is stale")
        row = copy.deepcopy(dict(committed))
        row.pop("write_pending", None)
        self._events[token.event_id] = row
        self.last_error = None

    def rollback(self, token: OptimisticToken, error: BaseException) -> None:
        active = self._tokens.pop(token.token_id, None)
        if active != token:
            raise RuntimeError("optimistic write token is stale")
        before = copy.deepcopy(token.before)
        before.pop("write_pending", None)
        self._events[token.event_id] = before
        self.last_error = str(error)
