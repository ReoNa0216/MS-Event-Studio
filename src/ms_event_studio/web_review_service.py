"""Browser-safe adapter for the validated project review services.

This module owns session-local opaque event capabilities.  Scientific event
identities, optimistic versions, integer-nanosecond coordinates, project
manifests, and storage paths never cross this boundary.
"""

from __future__ import annotations

import json
import math
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .desktop_model import (
    ORIGIN_LABELS,
    QUALITY_FLAG_LABELS,
    event_visual_encoding,
    filter_events,
)
from .display import WindowRequest, choose_event_labels
from .errors import ExistingEventNavigation, ReviewConflict, SnapError
from .export import export_human_csv, export_machine_contract
from .paths import resolve_project_path
from .project import Project
from .timebase import minutes_to_ns, seconds_to_ns
from .web_models import (
    AdjustmentRangeView,
    AllowedIntervalView,
    AnalysisRangeView,
    ApexPointView,
    CoreEvidenceView,
    EventEditAimView,
    EventEditCandidateView,
    EventEditMode,
    EventEditPreviewView,
    ExportSummaryView,
    EventFilterView,
    EventOrigin,
    EventSelectionView,
    HistoryView,
    MarkerView,
    MoreEvidenceView,
    ProjectSummaryView,
    QualityConclusionView,
    ReviewProgressView,
    ReviewStatus,
    TracePointView,
    ViewportView,
    WorkspaceEventView,
    WorkspaceView,
    WorkspaceWindowView,
    minutes_text,
)
from .window_service import ProjectWindowService, ProjectWindowSnapshot


FILTER_OPTIONS = (
    ("all", "全部事件"),
    ("unreviewed", "未审阅"),
    ("accepted", "已保留"),
    ("rejected", "已排除"),
    ("pending", "待定"),
    ("manual_added", "人工补充"),
    ("manual_adjusted", "人工调整"),
)
FILTER_VALUES = frozenset(value for value, _label in FILTER_OPTIONS)
DECISION_STATUSES = {
    "keep": "accepted",
    "exclude": "rejected",
    "pending": "pending",
    "clear": "unreviewed",
}
STATUS_LABELS = {
    "unreviewed": "未审阅",
    "accepted": "已保留",
    "rejected": "已排除",
    "pending": "待定",
}
QUALITY_FIELDS = tuple(QUALITY_FLAG_LABELS)


class WorkspaceRequestError(ValueError):
    """A browser request that cannot be mapped to a safe scientific action."""

    def __init__(self, message: str, *, code: str = "invalid_workspace_request") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class _ActionCapability:
    event_identity: str
    expected_version: int


@dataclass(frozen=True, slots=True)
class _AimCapability:
    project_identity: str
    mode: EventEditMode
    event_identity: str | None
    expected_version: int | None
    before_time_sec: float | None
    before_intensity: float | None
    minimum_sec: float
    maximum_sec: float
    snap_minimum_sec: float | None
    snap_maximum_sec: float | None


@dataclass(frozen=True, slots=True)
class _PreviewCapability:
    project_identity: str
    mode: EventEditMode
    event_identity: str | None
    expected_version: int | None
    before_time_sec: float | None
    before_intensity: float | None
    minimum_sec: float
    maximum_sec: float
    snap_minimum_sec: float | None
    snap_maximum_sec: float | None
    click_time_sec: float
    candidate_scan_id: str
    candidate_scan_row: int
    candidate_spectrum: int
    candidate_time_ns: int
    candidate_time_sec: float
    candidate_intensity: float
    offset_sec: float


def _optional_number(value: object, *, non_negative: bool = False) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or (non_negative and numeric < 0):
        return None
    return numeric


def _note(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > 2_000:
        raise WorkspaceRequestError("操作备注不能超过 2000 个字符。", code="invalid_note")
    if any(ord(character) < 32 and character not in "\t\r\n" for character in value):
        raise WorkspaceRequestError("操作备注包含不支持的字符。", code="invalid_note")
    return value.strip()


class BrowserWorkspaceService:
    """Serve one open project through opaque, browser-safe view models."""

    def __init__(self, project: Project) -> None:
        self._window_service = ProjectWindowService.open(project.project_dir)
        self._lock = threading.RLock()
        self._closed = False
        self._actor = "desktop-user"
        self._session_id = "web-review-" + secrets.token_urlsafe(24)
        self._event_tokens: dict[str, str] = {}
        self._event_identities: dict[str, str] = {}
        self._action_tokens: dict[str, _ActionCapability] = {}
        self._action_keys: dict[tuple[str, int], str] = {}
        self._aim_tokens: dict[str, _AimCapability] = {}
        self._preview_tokens: dict[str, _PreviewCapability] = {}
        self._selected_identity: str | None = None
        self._start = self._window_service.analysis_start_ns
        self._end = self._window_service.analysis_end_ns
        self._point_budget = 2_000
        self._maximum_labels = 30
        self._status_filter = "all"

    @property
    def project(self) -> Project:
        return self._window_service.project

    def _require_open(self) -> None:
        if self._closed:
            raise WorkspaceRequestError("项目工作区正在关闭。", code="workspace_closed")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._event_tokens.clear()
            self._event_identities.clear()
            self._action_tokens.clear()
            self._action_keys.clear()
            self._aim_tokens.clear()
            self._preview_tokens.clear()
            self._window_service.close()

    def _event_token(self, identity: str) -> str:
        token = self._event_tokens.get(identity)
        if token is None:
            token = secrets.token_urlsafe(24)
            self._event_tokens[identity] = token
            self._event_identities[token] = identity
        return token

    def _action_token(self, identity: str, version: int) -> str:
        key = (identity, int(version))
        token = self._action_keys.get(key)
        if token is None:
            token = secrets.token_urlsafe(24)
            self._action_keys[key] = token
            self._action_tokens[token] = _ActionCapability(identity, int(version))
        return token

    def _invalidate_actions(self, identity: str | None = None) -> None:
        if identity is None:
            self._action_tokens.clear()
            self._action_keys.clear()
            return
        doomed = [
            token
            for token, capability in self._action_tokens.items()
            if capability.event_identity == identity
        ]
        for token in doomed:
            capability = self._action_tokens.pop(token)
            self._action_keys.pop(
                (capability.event_identity, capability.expected_version),
                None,
            )

    def _invalidate_edits(self) -> None:
        self._aim_tokens.clear()
        self._preview_tokens.clear()

    def _resolve_event_token(self, token: object) -> str:
        if not isinstance(token, str):
            raise WorkspaceRequestError("事件选择已失效，请重新选择。", code="stale_event")
        identity = self._event_identities.get(token)
        if identity is None:
            raise WorkspaceRequestError("事件选择已失效，请重新选择。", code="stale_event")
        return identity

    def _resolve_action_token(self, token: object) -> _ActionCapability:
        if not isinstance(token, str):
            raise WorkspaceRequestError("操作已失效，请重新载入项目。", code="stale_action")
        capability = self._action_tokens.get(token)
        if capability is None:
            raise WorkspaceRequestError("操作已失效，请重新载入项目。", code="stale_action")
        return capability

    @staticmethod
    def _active_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        active = [dict(row) for row in rows if row.get("generation_state") != "stale"]
        active.sort(key=lambda row: (int(row["current_apex_time_ns"]), str(row["event_id"])))
        return active

    @staticmethod
    def _apex_modified(row: Mapping[str, Any]) -> bool:
        if row.get("original_auto_event_id"):
            return (
                int(row["current_apex_time_ns"]) != int(row["original_apex_time_ns"])
                or int(row["current_scan_row_index"])
                != int(row["original_scan_row_index"])
            )
        if row.get("created_apex_time_ns") is not None:
            return (
                int(row["current_apex_time_ns"]) != int(row["created_apex_time_ns"])
                or int(row["current_scan_row_index"])
                != int(row["created_scan_row_index"])
            )
        return False

    def _event_view(
        self,
        row: Mapping[str, Any],
        *,
        sequence_by_identity: Mapping[str, int],
    ) -> WorkspaceEventView:
        identity = str(row["event_id"])
        status = ReviewStatus(str(row["status"]))
        origin = EventOrigin(str(row["origin"]))
        encoding = event_visual_encoding(status.value, origin.value)
        modified = self._apex_modified(row)
        return WorkspaceEventView(
            event_token=self._event_token(identity),
            action_token=self._action_token(identity, int(row["revision"])),
            sequence=int(sequence_by_identity[identity]),
            apex_time_min=float(row["current_apex_time_sec"]) / 60.0,
            apex_time_sec=float(row["current_apex_time_sec"]),
            apex_intensity=float(row["current_apex_intensity"]),
            status=status,
            status_label=STATUS_LABELS[status.value],
            origin=origin,
            origin_label=ORIGIN_LABELS[origin.value],
            marker=MarkerView(
                shape=encoding.shape,
                color=encoding.color,
                code=encoding.text_token[:1],
                dash=tuple(int(value) for value in encoding.dash),
            ),
            apex_modified=modified,
            can_restore_automatic_apex=bool(row.get("original_auto_event_id")) and modified,
        )

    @staticmethod
    def _quality(automatic: Mapping[str, Any] | None) -> QualityConclusionView:
        evidence = automatic or {}
        notes = tuple(
            QUALITY_FLAG_LABELS[field]
            for field in QUALITY_FIELDS
            if bool(evidence.get(field, False))
        )
        if notes:
            return QualityConclusionView("attention", "存在需要关注的质量提示", notes)
        return QualityConclusionView("ok", "未发现明显异常", ())

    def _evidence(
        self,
        snapshot: ProjectWindowSnapshot,
    ) -> tuple[CoreEvidenceView | None, MoreEvidenceView | None]:
        event = snapshot.selected_event
        if event is None:
            return None, None
        scan = snapshot.selected_scan or {}
        automatic = snapshot.selected_automatic
        measured_mz = _optional_number(scan.get("pc34_760_mz_at_max_intensity"))
        mass_error = _optional_number(scan.get("pc34_760_ppm_error_at_max_intensity"))
        core = CoreEvidenceView(
            pc34_intensity=float(event["current_apex_intensity"]),
            measured_mz=measured_mz,
            mass_error_ppm=mass_error,
            quality=self._quality(automatic),
        )
        adjustment_range = None
        if automatic is not None:
            left = _optional_number(automatic.get("left_sec"))
            right = _optional_number(automatic.get("right_sec"))
            if left is not None and right is not None and right >= left:
                adjustment_range = AdjustmentRangeView(left, right)
        origin = str(event.get("origin", "automatic"))
        more = MoreEvidenceView(
            scan_number=str(event.get("current_scan_id") or "—"),
            ms782_intensity=_optional_number(
                scan.get("qc_782_max_intensity"),
                non_negative=True,
            ),
            tic=_optional_number(scan.get("tic"), non_negative=True),
            prominence=(
                None
                if automatic is None
                else _optional_number(automatic.get("peak_prominence"), non_negative=True)
            ),
            physical_width_sec=(
                None
                if automatic is None
                else _optional_number(automatic.get("peak_width_sec"), non_negative=True)
            ),
            adjustment_range=adjustment_range,
            adjustment_offset_sec=(
                None
                if origin == "automatic"
                else _optional_number(event.get("snap_offset_sec"))
            ),
        )
        return core, more

    @staticmethod
    def _filter_count(rows: list[dict[str, Any]], value: str) -> int:
        if value == "all":
            return len(rows)
        field = "origin" if value.startswith("manual_") else "status"
        return sum(str(row.get(field)) == value for row in rows)

    @staticmethod
    def _next_unreviewed(
        rows: list[dict[str, Any]],
        current_identity: str | None,
    ) -> str | None:
        if not rows:
            return None
        start = next(
            (
                index
                for index, row in enumerate(rows)
                if str(row["event_id"]) == str(current_identity)
            ),
            -1,
        )
        for offset in range(1, len(rows) + 1):
            row = rows[(start + offset) % len(rows)]
            if str(row.get("status")) == "unreviewed":
                return str(row["event_id"])
        return None

    def _selected(self, rows: list[dict[str, Any]]) -> str | None:
        identities = {str(row["event_id"]) for row in rows}
        if self._selected_identity in identities:
            return self._selected_identity
        unreviewed = next(
            (str(row["event_id"]) for row in rows if row.get("status") == "unreviewed"),
            None,
        )
        return unreviewed or (str(rows[0]["event_id"]) if rows else None)

    def _parse_window_request(self, payload: Mapping[str, Any] | None) -> None:
        if payload is None:
            return
        if not isinstance(payload, Mapping):
            raise WorkspaceRequestError("工作区请求必须是对象。")
        allowed = {
            "start_min",
            "end_min",
            "point_budget",
            "status_filter",
            "selected_event_token",
            "maximum_labels",
        }
        if set(payload).difference(allowed):
            raise WorkspaceRequestError("工作区请求包含不支持的字段。")

        has_start = "start_min" in payload
        has_end = "end_min" in payload
        if has_start != has_end:
            raise WorkspaceRequestError("请同时提供窗口起点和终点。", code="invalid_window")
        proposed_start = self._start
        proposed_end = self._end
        if has_start:
            start_value = payload.get("start_min")
            end_value = payload.get("end_min")
            valid_types = (str, int, float)
            if (
                isinstance(start_value, bool)
                or isinstance(end_value, bool)
                or not isinstance(start_value, valid_types)
                or not isinstance(end_value, valid_types)
            ):
                raise WorkspaceRequestError("窗口范围无效。", code="invalid_window")
            try:
                proposed_start = minutes_to_ns(start_value)
                proposed_end = minutes_to_ns(end_value)
            except ValueError as exc:
                raise WorkspaceRequestError("窗口范围无效。", code="invalid_window") from exc
            if not (
                self._window_service.analysis_start_ns
                <= proposed_start
                <= proposed_end
                <= self._window_service.analysis_end_ns
            ) or (
                self._window_service.analysis_end_ns
                > self._window_service.analysis_start_ns
                and proposed_end <= proposed_start
            ):
                raise WorkspaceRequestError(
                    "窗口必须位于项目分析范围内且具有有效宽度。",
                    code="invalid_window",
                )

        proposed_budget = self._point_budget
        if "point_budget" in payload:
            value = payload.get("point_budget")
            if isinstance(value, bool) or not isinstance(value, int) or not 32 <= value <= 20_000:
                raise WorkspaceRequestError("绘图点数设置无效。", code="invalid_window")
            proposed_budget = value

        proposed_labels = self._maximum_labels
        if "maximum_labels" in payload:
            value = payload.get("maximum_labels")
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 200:
                raise WorkspaceRequestError("标签数量设置无效。", code="invalid_window")
            proposed_labels = value

        proposed_filter = self._status_filter
        if "status_filter" in payload:
            value = payload.get("status_filter")
            if not isinstance(value, str) or value not in FILTER_VALUES:
                raise WorkspaceRequestError("事件筛选条件无效。", code="invalid_filter")
            proposed_filter = value

        proposed_selected = self._selected_identity
        selection_changed = False
        if "selected_event_token" in payload:
            token = payload.get("selected_event_token")
            # The browser sends null for window-only changes.  Null therefore
            # preserves the current event; a concrete opaque token is the only
            # way to change selection.
            if token is not None:
                proposed_selected = self._resolve_event_token(token)
                selection_changed = proposed_selected != self._selected_identity

        self._start = proposed_start
        self._end = proposed_end
        self._point_budget = proposed_budget
        self._maximum_labels = proposed_labels
        self._status_filter = proposed_filter
        self._selected_identity = proposed_selected
        if selection_changed:
            self._invalidate_edits()

    def workspace(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._require_open()
            self._parse_window_request(payload)
            # One full-range ProjectWindowService request is the authoritative
            # review read for this response.  Its overlay is not decimated and
            # therefore carries every active event from one database snapshot.
            # If this is the first request, repeat once with the default event
            # selected so evidence and the list still come from one final read.
            snapshot = self._window_service.window(
                WindowRequest(
                    start_ns=self._window_service.analysis_start_ns,
                    end_ns=self._window_service.analysis_end_ns,
                    point_budget=32,
                ),
                status_filter="all",
                selected_event_id=self._selected_identity,
                maximum_labels=0,
            )
            active = self._active_events(list(snapshot.events))
            if (
                payload is not None
                and payload.get("selected_event_token") is not None
                and self._selected_identity
                not in {str(row["event_id"]) for row in active}
            ):
                raise WorkspaceRequestError(
                    "事件选择已失效，请重新选择。",
                    code="stale_event",
                )
            selected = self._selected(active)
            if selected != self._selected_identity:
                self._selected_identity = selected
                snapshot = self._window_service.window(
                    WindowRequest(
                        start_ns=self._window_service.analysis_start_ns,
                        end_ns=self._window_service.analysis_end_ns,
                        point_budget=32,
                    ),
                    status_filter="all",
                    selected_event_id=self._selected_identity,
                    maximum_labels=0,
                )
                active = self._active_events(list(snapshot.events))
                self._selected_identity = self._selected(active)
            else:
                self._selected_identity = selected
            sequence = {
                str(row["event_id"]): index
                for index, row in enumerate(active, start=1)
            }
            display = self._window_service.pyramid.read_window(
                WindowRequest(
                    start_ns=self._start,
                    end_ns=self._end,
                    point_budget=self._point_budget,
                ),
                filter_events(active, self._status_filter),
            )
            label_identities = choose_event_labels(
                display.events,
                maximum_labels=self._maximum_labels,
                selected_event_id=self._selected_identity,
            )
            all_views = tuple(
                self._event_view(row, sequence_by_identity=sequence) for row in active
            )
            by_identity = {
                str(row["event_id"]): view for row, view in zip(active, all_views)
            }
            overlay = tuple(
                self._event_view(row, sequence_by_identity=sequence)
                for row in display.events
            )
            selected_view = (
                None
                if self._selected_identity is None
                else by_identity.get(self._selected_identity)
            )
            selected_index = (
                0
                if self._selected_identity is None
                else int(sequence.get(self._selected_identity, 0))
            )
            previous_token = next_token = None
            if selected_index:
                if selected_index > 1:
                    previous_token = all_views[selected_index - 2].event_token
                if selected_index < len(all_views):
                    next_token = all_views[selected_index].event_token
            next_unreviewed_identity = self._next_unreviewed(active, self._selected_identity)
            core, more = self._evidence(snapshot)

            status_counts = {
                status: sum(str(row.get("status")) == status for row in active)
                for status in ("unreviewed", "accepted", "rejected", "pending")
            }
            project_range = AnalysisRangeView.from_nanoseconds(
                self._window_service.analysis_start_ns,
                self._window_service.analysis_end_ns,
            )
            project = ProjectSummaryView(
                display_name=str(self.project.manifest["display_name"]),
                analysis_range=project_range,
                event_count=len(active),
            )
            history = self._window_service.review_store.history_state()
            view = WorkspaceView(
                project=project,
                review=ReviewProgressView(
                    total=len(active),
                    reviewed=len(active) - status_counts["unreviewed"],
                    unreviewed=status_counts["unreviewed"],
                    accepted=status_counts["accepted"],
                    rejected=status_counts["rejected"],
                    pending=status_counts["pending"],
                ),
                filters=tuple(
                    EventFilterView(value, label, self._filter_count(active, value))
                    for value, label in FILTER_OPTIONS
                ),
                events=all_views,
                selection=EventSelectionView(
                    event=selected_view,
                    index=selected_index,
                    total=len(active),
                    previous_event_token=previous_token,
                    next_event_token=next_token,
                    next_unreviewed_event_token=(
                        None
                        if next_unreviewed_identity is None
                        else self._event_token(next_unreviewed_identity)
                    ),
                    core_evidence=core,
                    more_evidence=more,
                ),
                window=WorkspaceWindowView(
                    viewport=ViewportView(
                        start_min=minutes_text(display.start_ns),
                        end_min=minutes_text(display.end_ns),
                        analysis_start_min=project_range.start_min,
                        analysis_end_min=project_range.end_min,
                    ),
                    trace=tuple(
                        TracePointView(
                            time_min=float(row["scan_start_time_sec"]) / 60.0,
                            intensity=float(row["pc34_760_max_intensity"]),
                        )
                        for row in display.trace.to_dict(orient="records")
                    ),
                    event_overlay=overlay,
                    label_event_tokens=tuple(
                        self._event_token(identity) for identity in label_identities
                    ),
                ),
                history=HistoryView(
                    can_undo=bool(history["can_undo"]),
                    can_redo=bool(history["can_redo"]),
                ),
            )
            return view.to_dict()

    @property
    def _project_identity(self) -> str:
        return str(self.project.manifest["project_id"])

    @staticmethod
    def _apex_point(time_sec: float | None, intensity: float | None) -> ApexPointView | None:
        if time_sec is None or intensity is None:
            return None
        return ApexPointView(float(time_sec) / 60.0, float(intensity))

    @staticmethod
    def _allowed_interval(capability: _AimCapability | _PreviewCapability) -> AllowedIntervalView:
        return AllowedIntervalView(
            float(capability.minimum_sec) / 60.0,
            float(capability.maximum_sec) / 60.0,
        )

    @staticmethod
    def _snap_request_error(error: SnapError) -> WorkspaceRequestError:
        detail = str(error).casefold()
        if "gap" in detail:
            return WorkspaceRequestError(
                "点击位置跨过了采集间隔，请在连续信号附近重试。",
                code="scan_gap",
            )
        if "ambiguous" in detail or "equidistant" in detail:
            return WorkspaceRequestError(
                "附近有多个同样接近的候选峰，请换一个更明确的位置。",
                code="ambiguous_candidate",
            )
        if "outside" in detail or "support" in detail or "range" in detail:
            return WorkspaceRequestError(
                "点击位置不在允许的调整区间内。",
                code="outside_allowed_interval",
            )
        if "overlap" in detail:
            return WorkspaceRequestError(
                "该位置同时属于多个已有事件，未进行修改。",
                code="edit_conflict",
            )
        return WorkspaceRequestError(
            "附近没有满足规则的信号峰，请换一个位置重试。",
            code="candidate_unavailable",
        )

    def _current_active_event(self, identity: str) -> dict[str, Any]:
        row = next(
            (
                item
                for item in self._active_events(self._window_service.all_events())
                if str(item["event_id"]) == identity
            ),
            None,
        )
        if row is None:
            raise WorkspaceRequestError(
                "事件已经变化，请重新选择。",
                code="stale_event",
            )
        return row

    def _validate_edit_capability(
        self,
        capability: _AimCapability | _PreviewCapability,
    ) -> None:
        if capability.project_identity != self._project_identity:
            raise WorkspaceRequestError(
                "编辑步骤已经失效，请重新开始。",
                code="stale_edit",
            )
        if capability.event_identity is None:
            return
        row = self._current_active_event(capability.event_identity)
        if int(row["revision"]) != int(capability.expected_version):
            raise ReviewConflict("event changed after the edit capability was issued")

    @staticmethod
    def _parse_edit_click(
        value: object,
        capability: _AimCapability | _PreviewCapability,
    ) -> float:
        valid_types = (str, int, float)
        if isinstance(value, bool) or not isinstance(value, valid_types):
            raise WorkspaceRequestError(
                "点击位置无效。",
                code="invalid_edit_position",
            )
        try:
            click_ns = minutes_to_ns(value)
        except ValueError as exc:
            raise WorkspaceRequestError(
                "点击位置无效。",
                code="invalid_edit_position",
            ) from exc
        minimum_ns = seconds_to_ns(capability.minimum_sec)
        maximum_ns = seconds_to_ns(capability.maximum_sec)
        if not minimum_ns <= click_ns <= maximum_ns:
            raise WorkspaceRequestError(
                "点击位置不在允许的调整区间内。",
                code="outside_allowed_interval",
            )
        return float(click_ns) / 1_000_000_000.0

    def _snap_edit_candidate(
        self,
        capability: _AimCapability | _PreviewCapability,
        click_time_sec: float,
    ) -> tuple[Any, float]:
        try:
            snapped, offset = self._window_service.review_store._snap(
                click_time_sec=click_time_sec,
                scans=self._window_service.scans,
                minimum_sec=capability.snap_minimum_sec,
                maximum_sec=capability.snap_maximum_sec,
            )
        except SnapError as exc:
            raise self._snap_request_error(exc) from exc
        apex_ns = int(snapped["scan_time_ns"])
        if not (
            self._window_service.analysis_start_ns
            <= apex_ns
            <= self._window_service.analysis_end_ns
        ):
            raise WorkspaceRequestError(
                "候选峰不在项目分析范围内。",
                code="outside_allowed_interval",
            )
        return snapped, float(offset)

    def begin_event_edit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload).difference(
            {"mode", "action_token"}
        ):
            raise WorkspaceRequestError("编辑请求包含不支持的字段。")
        mode_value = payload.get("mode")
        try:
            mode = EventEditMode(mode_value) if isinstance(mode_value, str) else None
        except ValueError as exc:
            raise WorkspaceRequestError("请选择有效的编辑方式。", code="invalid_edit_mode") from exc
        if mode is None:
            raise WorkspaceRequestError("请选择有效的编辑方式。", code="invalid_edit_mode")
        if mode is EventEditMode.ADD and "action_token" in payload:
            raise WorkspaceRequestError("添加事件不接受事件操作凭据。")
        if mode is EventEditMode.ADJUST and "action_token" not in payload:
            raise WorkspaceRequestError("请先选择要调整的事件。", code="stale_action")

        with self._lock:
            self._require_open()
            analysis_minimum = self._window_service.analysis_start_ns / 1_000_000_000.0
            analysis_maximum = self._window_service.analysis_end_ns / 1_000_000_000.0
            identity = None
            expected = None
            before_time = None
            before_intensity = None
            minimum = analysis_minimum
            maximum = analysis_maximum
            snap_minimum = None
            snap_maximum = None
            if mode is EventEditMode.ADJUST:
                action = self._resolve_action_token(payload.get("action_token"))
                if action.event_identity != self._selected_identity:
                    raise WorkspaceRequestError(
                        "事件选择已经变化，请重新选择。",
                        code="stale_action",
                    )
                row = self._current_active_event(action.event_identity)
                if int(row["revision"]) != action.expected_version:
                    raise ReviewConflict("event changed before adjust aim")
                identity = action.event_identity
                expected = action.expected_version
                before_time = float(row["current_apex_time_sec"])
                before_intensity = float(row["current_apex_intensity"])
                if row.get("original_auto_event_id"):
                    snap_minimum = float(row["original_left_sec"])
                    snap_maximum = float(row["original_right_sec"])
                    minimum = max(analysis_minimum, snap_minimum)
                    maximum = min(analysis_maximum, snap_maximum)
            if maximum < minimum:
                raise WorkspaceRequestError(
                    "当前事件没有可用的调整区间。",
                    code="outside_allowed_interval",
                )
            self._invalidate_edits()
            token = secrets.token_urlsafe(24)
            capability = _AimCapability(
                project_identity=self._project_identity,
                mode=mode,
                event_identity=identity,
                expected_version=expected,
                before_time_sec=before_time,
                before_intensity=before_intensity,
                minimum_sec=minimum,
                maximum_sec=maximum,
                snap_minimum_sec=snap_minimum,
                snap_maximum_sec=snap_maximum,
            )
            self._aim_tokens[token] = capability
            return EventEditAimView(
                aim_token=token,
                mode=mode,
                before=self._apex_point(before_time, before_intensity),
                allowed_interval=self._allowed_interval(capability),
            ).to_dict()

    def preview_event_edit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload) != {
            "aim_token",
            "click_time_min",
        }:
            raise WorkspaceRequestError("候选预览请求不完整或包含不支持的字段。")
        token = payload.get("aim_token")
        with self._lock:
            self._require_open()
            capability = self._aim_tokens.get(token) if isinstance(token, str) else None
            if capability is None:
                raise WorkspaceRequestError(
                    "编辑步骤已经失效，请重新开始。",
                    code="stale_edit",
                )
            self._validate_edit_capability(capability)
            click_time_sec = self._parse_edit_click(payload.get("click_time_min"), capability)
            snapped, offset = self._snap_edit_candidate(capability, click_time_sec)
            self._aim_tokens.pop(token, None)
            self._preview_tokens.clear()
            preview_token = secrets.token_urlsafe(24)
            preview = _PreviewCapability(
                project_identity=capability.project_identity,
                mode=capability.mode,
                event_identity=capability.event_identity,
                expected_version=capability.expected_version,
                before_time_sec=capability.before_time_sec,
                before_intensity=capability.before_intensity,
                minimum_sec=capability.minimum_sec,
                maximum_sec=capability.maximum_sec,
                snap_minimum_sec=capability.snap_minimum_sec,
                snap_maximum_sec=capability.snap_maximum_sec,
                click_time_sec=click_time_sec,
                candidate_scan_id=str(snapped["scan_id"]),
                candidate_scan_row=int(snapped["scan_row_index"]),
                candidate_spectrum=int(snapped["spectrum_index"]),
                candidate_time_ns=int(snapped["scan_time_ns"]),
                candidate_time_sec=float(snapped["scan_start_time_sec"]),
                candidate_intensity=float(snapped["pc34_760_max_intensity"]),
                offset_sec=offset,
            )
            self._preview_tokens[preview_token] = preview
            before = self._apex_point(preview.before_time_sec, preview.before_intensity)
            after = ApexPointView(
                preview.candidate_time_sec / 60.0,
                preview.candidate_intensity,
            )
            return EventEditPreviewView(
                preview_token=preview_token,
                mode=preview.mode,
                candidate=EventEditCandidateView(
                    time_min=preview.candidate_time_sec / 60.0,
                    intensity=preview.candidate_intensity,
                    offset_sec=preview.offset_sec,
                ),
                before=before,
                after=after,
                allowed_interval=self._allowed_interval(preview),
            ).to_dict()

    @staticmethod
    def _preview_candidate_matches(
        preview: _PreviewCapability,
        snapped: Any,
        offset: float,
    ) -> bool:
        return (
            str(snapped["scan_id"]) == preview.candidate_scan_id
            and int(snapped["scan_row_index"]) == preview.candidate_scan_row
            and int(snapped["spectrum_index"]) == preview.candidate_spectrum
            and int(snapped["scan_time_ns"]) == preview.candidate_time_ns
            and math.isclose(
                float(snapped["scan_start_time_sec"]),
                preview.candidate_time_sec,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            and math.isclose(
                float(snapped["pc34_760_max_intensity"]),
                preview.candidate_intensity,
                rel_tol=0.0,
                abs_tol=0.0,
            )
            and math.isclose(float(offset), preview.offset_sec, rel_tol=0.0, abs_tol=1e-12)
        )

    def apply_event_edit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload).difference({"preview_token", "note"}):
            raise WorkspaceRequestError("应用候选请求包含不支持的字段。")
        if "preview_token" not in payload:
            raise WorkspaceRequestError("候选预览已经失效，请重新开始。", code="stale_edit")
        reason = _note(payload.get("note"))
        token = payload.get("preview_token")
        with self._lock:
            self._require_open()
            preview = self._preview_tokens.pop(token, None) if isinstance(token, str) else None
            if preview is None:
                raise WorkspaceRequestError(
                    "候选预览已经失效，请重新开始。",
                    code="stale_edit",
                )
            self._validate_edit_capability(preview)
            try:
                snapped, offset = self._snap_edit_candidate(preview, preview.click_time_sec)
            except WorkspaceRequestError as exc:
                raise WorkspaceRequestError(
                    "信号已发生变化，请重新预览。",
                    code="stale_preview",
                ) from exc
            if not self._preview_candidate_matches(preview, snapped, offset):
                raise WorkspaceRequestError(
                    "信号已发生变化，请重新预览。",
                    code="stale_preview",
                )

            outcome = "applied"
            if preview.mode is EventEditMode.ADD:
                try:
                    result = self._window_service.review_store.add_event(
                        click_time_sec=preview.click_time_sec,
                        scans=self._window_service.scans,
                        analysis_start_ns=self._window_service.analysis_start_ns,
                        analysis_end_ns=self._window_service.analysis_end_ns,
                        actor=self._actor,
                        session_id=self._session_id,
                        reason=reason,
                    )
                    selected_identity = str(result["event_id"])
                except ExistingEventNavigation as navigation:
                    selected_identity = navigation.event_id
                    outcome = "navigate_existing"
                except SnapError as exc:
                    raise self._snap_request_error(exc) from exc
            else:
                if preview.event_identity is None or preview.expected_version is None:
                    raise WorkspaceRequestError(
                        "候选预览已经失效，请重新开始。",
                        code="stale_preview",
                    )
                try:
                    result = self._window_service.review_store.adjust_apex(
                        preview.event_identity,
                        click_time_sec=preview.click_time_sec,
                        scans=self._window_service.scans,
                        analysis_start_ns=self._window_service.analysis_start_ns,
                        analysis_end_ns=self._window_service.analysis_end_ns,
                        expected_revision=preview.expected_version,
                        actor=self._actor,
                        session_id=self._session_id,
                        reason=reason,
                    )
                except SnapError as exc:
                    raise self._snap_request_error(exc) from exc
                selected_identity = str(result["event_id"])

            self._invalidate_actions()
            self._invalidate_edits()
            self._selected_identity = selected_identity
            return {
                "ok": True,
                "outcome": outcome,
                "workspace": self.workspace(),
            }

    def cancel_event_edit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload) != {"edit_token"}:
            raise WorkspaceRequestError("取消编辑请求不完整或包含不支持的字段。")
        token = payload.get("edit_token")
        with self._lock:
            self._require_open()
            cancelled = False
            if isinstance(token, str):
                cancelled = self._aim_tokens.pop(token, None) is not None
                cancelled = self._preview_tokens.pop(token, None) is not None or cancelled
            if not cancelled:
                raise WorkspaceRequestError(
                    "编辑步骤已经失效。",
                    code="stale_edit",
                )
            return {"ok": True, "cancelled": True}

    def _artifact(self, role: str) -> Path:
        for record in self.project.manifest["artifacts"]:
            if record["role"] == role:
                return resolve_project_path(self.project.project_dir, record["path"])
        raise ValueError("project export input is incomplete")

    def export_review_results(
        self,
        target: Path,
        *,
        display_name: str,
        include_pending: bool,
        note: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._require_open()
            result = export_human_csv(
                self._window_service.review_store.list_events(),
                target,
                analysis_start_ns=self._window_service.analysis_start_ns,
                analysis_end_ns=self._window_service.analysis_end_ns,
                include_pending=include_pending,
            )
            self._window_service.review_store.record_export(
                actor=self._actor,
                session_id=self._session_id,
                reason=note,
                details={
                    "contract": "human-csv-v1",
                    "file_name": result.path.name,
                    "sha256": result.sha256,
                    "size_bytes": result.size_bytes,
                    "row_count": result.row_count,
                    "statuses": result.statuses,
                    "analysis_start_ns": self._window_service.analysis_start_ns,
                    "analysis_end_ns": self._window_service.analysis_end_ns,
                },
            )
            return ExportSummaryView(
                kind="review_results",
                display_name=display_name,
                row_count=result.row_count,
                message=f"审阅结果已导出，共 {result.row_count:,} 行。",
            ).to_dict()

    def export_audit_package(
        self,
        target: Path,
        *,
        display_name: str,
        note: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._require_open()
            input_manifest = json.loads(
                self._artifact("input_manifest").read_text(encoding="utf-8")
            )
            protocol = json.loads(
                self._artifact("detector_protocol").read_text(encoding="utf-8")
            )
            result = export_machine_contract(
                self._window_service.review_store.list_events(),
                self._window_service.automatic,
                target,
                source_fingerprint=input_manifest["source_fingerprint"],
                detector_version=protocol["detector_version"],
                parameter_hash=protocol["parameter_hash"],
                generation_id=protocol["generation_id"],
                analysis_start_ns=self._window_service.analysis_start_ns,
                analysis_end_ns=self._window_service.analysis_end_ns,
                boundary_rule=self.project.manifest["analysis_range"]["boundary_rule"],
            )
            self._window_service.review_store.record_export(
                actor=self._actor,
                session_id=self._session_id,
                reason=note,
                details={
                    "contract": "ms-event-machine-contract-v1",
                    "directory_name": result.output_dir.name,
                    "event_table_sha256": result.event_table_sha256,
                    "manifest_sha256": result.manifest_sha256,
                    "row_count": result.row_count,
                },
            )
            return ExportSummaryView(
                kind="audit_package",
                display_name=display_name,
                row_count=result.row_count,
                message=f"完整审计数据包已导出，共 {result.row_count:,} 行。",
            ).to_dict()

    def review_decision(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload).difference(
            {"action_token", "decision", "note"}
        ):
            raise WorkspaceRequestError("审阅请求包含不支持的字段。")
        decision = payload.get("decision")
        if not isinstance(decision, str) or decision not in DECISION_STATUSES:
            raise WorkspaceRequestError("请选择有效的审阅结论。", code="invalid_decision")
        reason = _note(payload.get("note"))
        with self._lock:
            self._require_open()
            capability = self._resolve_action_token(payload.get("action_token"))
            self._window_service.review_store.set_status(
                capability.event_identity,
                DECISION_STATUSES[decision],
                expected_revision=capability.expected_version,
                actor=self._actor,
                session_id=self._session_id,
                reason=reason,
            )
            self._invalidate_actions(capability.event_identity)
            self._invalidate_edits()
            active = self._active_events(self._window_service.all_events())
            next_identity = self._next_unreviewed(active, capability.event_identity)
            identities = {str(row["event_id"]) for row in active}
            self._selected_identity = (
                next_identity
                or (
                    capability.event_identity
                    if capability.event_identity in identities
                    else None
                )
            )
            return {
                "ok": True,
                "message": "审阅已保存。",
                "workspace": self.workspace(),
            }

    def restore_automatic_apex(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload).difference({"action_token", "note"}):
            raise WorkspaceRequestError("恢复请求包含不支持的字段。")
        reason = _note(payload.get("note"))
        with self._lock:
            self._require_open()
            capability = self._resolve_action_token(payload.get("action_token"))
            self._window_service.review_store.restore_automatic_apex(
                capability.event_identity,
                expected_revision=capability.expected_version,
                actor=self._actor,
                session_id=self._session_id,
                reason=reason,
            )
            self._invalidate_actions(capability.event_identity)
            self._invalidate_edits()
            self._selected_identity = capability.event_identity
            return {
                "ok": True,
                "message": "已恢复自动峰顶，审阅结论保持不变。",
                "workspace": self.workspace(),
            }

    def undo(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload).difference({"note"}):
            raise WorkspaceRequestError("撤销请求包含不支持的字段。")
        reason = _note(payload.get("note"))
        with self._lock:
            self._require_open()
            restored = self._window_service.review_store.undo(
                actor=self._actor,
                session_id=self._session_id,
                reason=reason,
            )
            self._invalidate_actions()
            self._invalidate_edits()
            self._selected_identity = (
                None if restored is None else str(restored["event_id"])
            )
            return {"ok": True, "message": "已撤销上一步操作。", "workspace": self.workspace()}

    def redo(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload).difference({"note"}):
            raise WorkspaceRequestError("重做请求包含不支持的字段。")
        reason = _note(payload.get("note"))
        with self._lock:
            self._require_open()
            restored = self._window_service.review_store.redo(
                actor=self._actor,
                session_id=self._session_id,
                reason=reason,
            )
            self._invalidate_actions()
            self._invalidate_edits()
            self._selected_identity = str(restored["event_id"])
            return {"ok": True, "message": "已重做上一步操作。", "workspace": self.workspace()}


__all__ = [
    "BrowserWorkspaceService",
    "DECISION_STATUSES",
    "FILTER_OPTIONS",
    "WorkspaceRequestError",
]
