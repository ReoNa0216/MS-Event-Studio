"""Local-only HTTP and session boundary for the Phase 2R WebView shell.

This module deliberately contains no scientific implementation.  It validates
browser inputs, resolves in-memory path capabilities, schedules the existing
project services off the UI thread, and converts results to browser-safe view
models.
"""

from __future__ import annotations

import copy
import hmac
import ipaddress
import json
import logging
import mimetypes
import os
import re
import secrets
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import parse_qs, unquote, urlencode, urlparse

from . import __version__
from .desktop_model import RecentProjects
from .errors import (
    CancelledError,
    InputChangedError,
    MSParseError,
    ProjectValidationError,
    ReviewConflict,
)
from .parser import ParseProgress
from .paths import resolve_project_path
from .project import (
    CreateProjectRequest,
    PreparedProjectSource,
    Project,
    create_project,
    inspect_project_source,
    open_project as open_scientific_project,
)
from .range_change import RangeChangePreview, apply_range_change, preview_range_change
from .review import ReviewStore
from .timebase import minutes_to_ns
from .web_models import (
    AnalysisRangeView,
    BootstrapView,
    JobErrorView,
    JobProgressView,
    JobState,
    JobView,
    PathRole,
    ProjectSummaryView,
    RangeChangePreviewView,
    RangeImpactView,
    RecentProjectView,
    SelectionView,
    SourceInspectionView,
)
from .web_review_service import BrowserWorkspaceService, WorkspaceRequestError


LOGGER = logging.getLogger(__name__)
APP_NAME = "MS Event Studio"
WRITE_TOKEN_HEADER = "X-MS-Event-Token"
MAX_JSON_BYTES = 64 * 1024
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9_-]{16,160}$")
_RECENT_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class WebBoundaryError(ValueError):
    """An intentionally browser-safe request failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_request",
        status: HTTPStatus = HTTPStatus.BAD_REQUEST,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class _Selection:
    token: str
    role: PathRole
    path: Path
    display_name: str


@dataclass(frozen=True, slots=True)
class _Inspection:
    token: str
    source_token: str
    prepared: PreparedProjectSource


@dataclass(frozen=True, slots=True)
class _RangePreviewCapability:
    token: str
    project_path: Path
    preview: RangeChangePreview


@dataclass(slots=True)
class _JobRecord:
    job_id: str
    kind: str
    state: JobState = JobState.QUEUED
    phase: str = "queued"
    fraction: float = 0.0
    bytes_read: int = 0
    total_bytes: int = 0
    parsed_spectra: int = 0
    result: dict[str, Any] | None = None
    error: JobErrorView | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    future: Future[Any] | None = None
    cancel_allowed: bool = True


def default_recent_path() -> Path:
    """Return the per-user recent-project file without importing a GUI host."""

    override = os.environ.get("MS_EVENT_STUDIO_CONFIG_DIR")
    if override:
        root = Path(override)
    elif sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / APP_NAME
    elif sys.platform == "darwin":
        root = Path.home() / "Library/Application Support" / APP_NAME
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ms-event-studio"
    return root / "recent_projects.json"


def _public_error(error: BaseException) -> JobErrorView:
    """Map internal failures without serializing paths or storage terminology."""

    if isinstance(error, WebBoundaryError):
        return JobErrorView(error.code, str(error))
    if isinstance(error, CancelledError):
        return JobErrorView("cancelled", "操作已取消。")
    if isinstance(error, InputChangedError):
        return JobErrorView("source_changed", "源文件在分析后发生了变化，请重新选择并分析。")
    if isinstance(error, MSParseError):
        return JobErrorView("source_invalid", "源文件未通过完整校验，请确认文件完整且格式正确。")
    if isinstance(error, ProjectValidationError):
        return JobErrorView("project_invalid", "项目无法打开或内容不完整，请选择有效的项目目录。")
    if isinstance(error, FileExistsError):
        return JobErrorView("target_not_empty", "保存位置已被占用，请选择不存在或空的项目目录。")
    if isinstance(error, PermissionError):
        return JobErrorView("permission_denied", "没有完成此操作所需的文件访问权限。")
    if isinstance(error, ValueError):
        return JobErrorView("invalid_input", "输入内容无效，请检查项目名称和分析范围。")
    return JobErrorView("operation_failed", "操作未完成，请重试；若问题持续出现，请保留诊断日志。")


def _clean_display_name(value: object) -> str:
    if not isinstance(value, str):
        raise WebBoundaryError("请填写项目名称。", code="invalid_project_name")
    name = value.strip()
    if not name or len(name) > 120 or any(ord(character) < 32 for character in name):
        raise WebBoundaryError(
            "项目名称应为 1–120 个可见字符。",
            code="invalid_project_name",
        )
    return name


def _exact_text(payload: Mapping[str, Any], name: str, *, maximum: int = 200) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise WebBoundaryError("请求内容不完整。", code="invalid_request")
    return value


def _optional_note(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > 2_000:
        raise WebBoundaryError("操作备注不能超过 2000 个字符。", code="invalid_note")
    if any(ord(character) < 32 and character not in "\t\r\n" for character in value):
        raise WebBoundaryError("操作备注包含不支持的字符。", code="invalid_note")
    return value.strip()


def _phase_name(value: object, *, creating: bool = False) -> str:
    if creating:
        return "creating"
    return {
        "parsing": "reading",
        "complete": "validating",
        "prepared": "creating",
    }.get(str(value), "working")


class WebSession:
    """Own browser capabilities, background jobs, and the active project.

    Filesystem paths, prepared parse tables, project identities, and storage
    bindings stay private to this object.  Public methods return JSON-ready
    dictionaries composed only from :mod:`web_models`.
    """

    def __init__(
        self,
        recent_path: str | Path | None = None,
        *,
        max_workers: int = 2,
    ) -> None:
        if isinstance(max_workers, bool) or int(max_workers) < 1:
            raise ValueError("max_workers must be a positive integer")
        self.request_token = secrets.token_urlsafe(32)
        self._recent = RecentProjects(recent_path or default_recent_path())
        self._executor = ThreadPoolExecutor(
            max_workers=int(max_workers),
            thread_name_prefix="ms-event-web",
        )
        self._lock = threading.RLock()
        self._closed = False
        self._selections: dict[str, _Selection] = {}
        self._selection_keys: dict[tuple[PathRole, str], str] = {}
        self._inspections: dict[str, _Inspection] = {}
        self._range_previews: dict[str, _RangePreviewCapability] = {}
        self._jobs: dict[str, _JobRecord] = {}
        self._active_project: Project | None = None
        self._workspace: BrowserWorkspaceService | None = None
        self._project_mutation_pending = False

    def _require_open(self) -> None:
        if self._closed:
            raise WebBoundaryError(
                "应用正在关闭，不能开始新的操作。",
                code="session_closed",
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )

    @staticmethod
    def _resolve_path(role: PathRole, value: str | Path) -> Path:
        try:
            candidate = Path(value).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise WebBoundaryError("所选位置无效，请重新选择。", code="invalid_selection") from exc
        if role is PathRole.SOURCE_FILE and not candidate.is_file():
            raise WebBoundaryError("请选择可读取的 MS 源文件。", code="invalid_source")
        if role is PathRole.PROJECT_OPEN and not candidate.is_dir():
            raise WebBoundaryError("请选择已有的 MS Event Studio 项目目录。", code="invalid_project")
        if role is PathRole.PROJECT_TARGET:
            if candidate.exists() and not candidate.is_dir():
                raise WebBoundaryError("项目保存位置不能是文件。", code="invalid_target")
            parent = candidate if candidate.exists() else candidate.parent
            if not parent.exists() or not parent.is_dir():
                raise WebBoundaryError("项目保存位置的上级目录不存在。", code="invalid_target")
        if role is PathRole.REVIEW_EXPORT_FILE:
            if candidate.exists() and not candidate.is_file():
                raise WebBoundaryError("审阅结果必须保存为 CSV 文件。", code="invalid_target")
            if candidate.suffix.casefold() != ".csv":
                raise WebBoundaryError("审阅结果文件名必须以 .csv 结尾。", code="invalid_target")
            if not candidate.parent.exists() or not candidate.parent.is_dir():
                raise WebBoundaryError("审阅结果的保存目录不存在。", code="invalid_target")
        if role is PathRole.AUDIT_EXPORT_TARGET:
            if candidate.exists() and (
                not candidate.is_dir() or any(candidate.iterdir())
            ):
                raise WebBoundaryError(
                    "完整审计数据包必须保存到不存在或空的文件夹。",
                    code="target_not_empty",
                )
            if not candidate.exists() and (
                not candidate.parent.exists() or not candidate.parent.is_dir()
            ):
                raise WebBoundaryError(
                    "完整审计数据包的上级目录不存在。",
                    code="invalid_target",
                )
        return candidate

    def _register_selection(
        self,
        role: PathRole,
        value: str | Path,
        *,
        display_name: str | None = None,
    ) -> SelectionView:
        path = self._resolve_path(role, value)
        key = (role, os.path.normcase(str(path)))
        with self._lock:
            self._require_open()
            token = self._selection_keys.get(key)
            if token is None:
                token = secrets.token_urlsafe(24)
                self._selection_keys[key] = token
            label = str(display_name or path.name).strip() or (
                "新项目" if role is PathRole.PROJECT_TARGET else "已选择"
            )
            selection = _Selection(token, role, path, label[:120])
            self._selections[token] = selection
        return SelectionView(token, role, selection.display_name)

    def register_path(self, role: str | PathRole, path: str | Path) -> dict[str, Any]:
        """Register one native-dialog result and return no filesystem path."""

        try:
            parsed_role = role if isinstance(role, PathRole) else PathRole.parse(role)
        except ValueError as exc:
            raise WebBoundaryError(str(exc), code="invalid_selection_role") from exc
        return self._register_selection(parsed_role, path).to_dict()

    def _selection(self, token: object, role: PathRole) -> _Selection:
        if not isinstance(token, str) or not _OPAQUE_ID.fullmatch(token):
            raise WebBoundaryError("选择已失效，请重新选择。", code="stale_selection")
        with self._lock:
            selection = self._selections.get(token)
        if selection is None or selection.role is not role:
            raise WebBoundaryError("选择已失效，请重新选择。", code="stale_selection")
        # Native selections are capabilities, not a substitute for backend
        # validation.  Resolve and check their role again at every operation.
        current = self._resolve_path(role, selection.path)
        if current != selection.path:
            raise WebBoundaryError("所选位置已发生变化，请重新选择。", code="stale_selection")
        return selection

    def _consume_selection(self, token: object, role: PathRole) -> _Selection:
        selection = self._selection(token, role)
        key = (role, os.path.normcase(str(selection.path)))
        with self._lock:
            if self._selections.get(selection.token) is not selection:
                raise WebBoundaryError("选择已失效，请重新选择。", code="stale_selection")
            self._selections.pop(selection.token, None)
            if self._selection_keys.get(key) == selection.token:
                self._selection_keys.pop(key, None)
        return selection

    def _require_project_stable(self) -> None:
        with self._lock:
            if self._project_mutation_pending:
                raise WebBoundaryError(
                    "分析范围正在更新，请等待完成后再操作。",
                    code="project_busy",
                    status=HTTPStatus.CONFLICT,
                )

    def _project_summary(self, project: Project) -> ProjectSummaryView:
        analysis = project.manifest["analysis_range"]
        review_path = resolve_project_path(project.project_dir, project.manifest["review"]["path"])
        with ReviewStore.open(review_path, project_id=project.manifest["project_id"]) as store:
            count = sum(
                row.get("generation_state") != "stale" for row in store.list_events()
            )
        return ProjectSummaryView(
            display_name=_clean_display_name(project.manifest.get("display_name")),
            analysis_range=AnalysisRangeView.from_nanoseconds(
                int(analysis["start_ns"]),
                int(analysis["end_ns"]),
            ),
            event_count=count,
        )

    def bootstrap(self) -> dict[str, Any]:
        with self._lock:
            self._require_open()
            active = self._active_project
        recent_rows: list[RecentProjectView] = []
        for row in self._recent.load():
            try:
                display_name = _clean_display_name(row.display_name)
                if not _RECENT_TIMESTAMP.fullmatch(row.opened_at):
                    continue
                selection = self._register_selection(
                    PathRole.PROJECT_OPEN,
                    row.path,
                    display_name=display_name,
                )
            except WebBoundaryError:
                continue
            recent_rows.append(
                RecentProjectView(
                    project_token=selection.selection_token,
                    display_name=display_name,
                    last_opened=row.opened_at,
                )
            )
        summary = None if active is None else self._project_summary(active)
        return BootstrapView(
            application_name=APP_NAME,
            application_version=__version__,
            request_token=self.request_token,
            recent_projects=tuple(recent_rows),
            active_project=summary,
        ).to_dict()

    def open_project(self, project_token: object) -> dict[str, Any]:
        self._require_project_stable()
        selection = self._selection(project_token, PathRole.PROJECT_OPEN)
        try:
            project = open_scientific_project(selection.path)
            summary = self._project_summary(project)
            workspace = BrowserWorkspaceService(project)
        except Exception as exc:
            public = _public_error(exc)
            raise WebBoundaryError(public.message, code=public.code) from exc
        with self._lock:
            self._require_open()
            previous = self._workspace
            self._active_project = project
            self._workspace = workspace
        if previous is not None:
            previous.close()
        try:
            self._recent.remember(project.project_dir, summary.display_name)
        except OSError:
            LOGGER.warning("Could not update recent-project history", exc_info=True)
        return {"ok": True, "project": summary.to_dict()}

    def _job_view_locked(self, record: _JobRecord) -> JobView:
        return JobView(
            job_id=record.job_id,
            state=record.state,
            phase=record.phase,
            progress=JobProgressView(
                fraction=max(0.0, min(1.0, float(record.fraction))),
                bytes_read=max(0, int(record.bytes_read)),
                total_bytes=max(0, int(record.total_bytes)),
                parsed_spectra=max(0, int(record.parsed_spectra)),
            ),
            cancellable=(
                record.cancel_allowed
                and record.state in {JobState.QUEUED, JobState.RUNNING}
            ),
            result=None if record.result is None else copy.deepcopy(record.result),
            error=record.error,
        )

    def _new_job(
        self,
        kind: str,
        runner: Callable[[_JobRecord], dict[str, Any]],
        *,
        cancel_allowed: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            self._require_open()
            identity = secrets.token_urlsafe(24)
            record = _JobRecord(identity, kind, cancel_allowed=cancel_allowed)
            self._jobs[identity] = record
            record.future = self._executor.submit(self._execute_job, record, runner)
            return {"job": self._job_view_locked(record).to_dict()}

    def _execute_job(
        self,
        record: _JobRecord,
        runner: Callable[[_JobRecord], dict[str, Any]],
    ) -> None:
        with self._lock:
            if record.cancel_event.is_set():
                record.state = JobState.CANCELLED
                record.phase = "cancelled"
                return
            record.state = JobState.RUNNING
            record.phase = {
                "source_inspection": "reading",
                "project_creation": "creating",
                "range_preview": "calculating",
                "range_apply": "applying",
                "review_export": "exporting",
                "audit_export": "exporting",
            }.get(record.kind, "working")
        try:
            result = runner(record)
        except CancelledError:
            with self._lock:
                record.state = JobState.CANCELLED
                record.phase = "cancelled"
                record.error = None
            return
        except BaseException as exc:  # worker exceptions are converted before polling
            expected_failures = (
                InputChangedError,
                MSParseError,
                ProjectValidationError,
                FileExistsError,
                PermissionError,
                ValueError,
                WebBoundaryError,
            )
            if isinstance(exc, expected_failures):
                LOGGER.info(
                    "Background web job %s ended with %s",
                    record.kind,
                    type(exc).__name__,
                )
            else:
                LOGGER.exception("Background web job %s failed unexpectedly", record.kind)
            with self._lock:
                record.state = JobState.FAILED
                record.phase = "failed"
                record.error = _public_error(exc)
            return
        with self._lock:
            record.result = copy.deepcopy(result)
            record.error = None
            record.state = JobState.SUCCEEDED
            record.phase = "complete"
            record.fraction = 1.0

    def _update_progress(
        self,
        record: _JobRecord,
        progress: ParseProgress,
        *,
        creating: bool = False,
    ) -> None:
        total = max(0, int(progress.total_bytes))
        read = max(0, min(int(progress.bytes_read), total))
        if creating:
            fraction = 0.1 + 0.25 * (1.0 if total == 0 else read / total)
        else:
            fraction = 1.0 if total == 0 else read / total
        with self._lock:
            if record.state not in {JobState.RUNNING, JobState.CANCELLING}:
                return
            record.phase = _phase_name(progress.phase, creating=creating)
            record.fraction = max(0.0, min(1.0, fraction))
            record.bytes_read = read
            record.total_bytes = total
            record.parsed_spectra = max(0, int(progress.parsed_spectra))

    def start_source_inspection(self, source_token: object) -> dict[str, Any]:
        selection = self._selection(source_token, PathRole.SOURCE_FILE)

        def run(record: _JobRecord) -> dict[str, Any]:
            prepared = inspect_project_source(
                selection.path,
                cancel_check=record.cancel_event.is_set,
                progress_callback=lambda progress: self._update_progress(record, progress),
            )
            inspection_token = secrets.token_urlsafe(24)
            with self._lock:
                self._inspections[inspection_token] = _Inspection(
                    inspection_token,
                    selection.token,
                    prepared,
                )
            return SourceInspectionView(
                inspection_token=inspection_token,
                source_name=selection.path.name,
                available_range=AnalysisRangeView.from_nanoseconds(
                    prepared.start_ns,
                    prepared.end_ns,
                ),
                scan_count=len(prepared.parsed.scans),
                size_bytes=int(prepared.parsed.fingerprint.size_bytes),
            ).to_dict()

        return self._new_job("source_inspection", run)

    def start_project_creation(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_project_stable()
        if not isinstance(payload, Mapping):
            raise WebBoundaryError("请求内容必须是对象。", code="invalid_request")
        allowed = {
            "source_token",
            "inspection_token",
            "target_token",
            "display_name",
            "analysis_start_min",
            "analysis_end_min",
        }
        if set(payload).difference(allowed):
            raise WebBoundaryError("请求包含不支持的字段。", code="invalid_request")
        source_token = _exact_text(payload, "source_token")
        inspection_token = _exact_text(payload, "inspection_token")
        target_token = _exact_text(payload, "target_token")
        name = _clean_display_name(payload.get("display_name"))
        start_text = _exact_text(payload, "analysis_start_min", maximum=64)
        end_text = _exact_text(payload, "analysis_end_min", maximum=64)
        source = self._selection(source_token, PathRole.SOURCE_FILE)
        target = self._selection(target_token, PathRole.PROJECT_TARGET)
        with self._lock:
            inspection = self._inspections.get(inspection_token)
        if inspection is None or inspection.source_token != source.token:
            raise WebBoundaryError("源文件分析结果已失效，请重新分析。", code="stale_inspection")
        try:
            start_ns = minutes_to_ns(start_text)
            end_ns = minutes_to_ns(end_text)
        except ValueError as exc:
            raise WebBoundaryError("请输入有效的分钟范围。", code="invalid_range") from exc
        if not inspection.prepared.start_ns <= start_ns <= end_ns <= inspection.prepared.end_ns:
            raise WebBoundaryError(
                "分析范围必须位于源文件的可用范围内，并包含起点和终点。",
                code="invalid_range",
            )

        def run(record: _JobRecord) -> dict[str, Any]:
            request = CreateProjectRequest(
                source_path=source.path,
                project_dir=target.path,
                display_name=name,
                analysis_start_min=start_text,
                analysis_end_min=end_text,
                cancel_check=record.cancel_event.is_set,
                progress_callback=lambda progress: self._update_progress(
                    record,
                    progress,
                    creating=True,
                ),
            )
            project = create_project(request, prepared_source=inspection.prepared)
            summary = self._project_summary(project)
            workspace = BrowserWorkspaceService(project)
            with self._lock:
                previous = self._workspace
                self._active_project = project
                self._workspace = workspace
            if previous is not None:
                previous.close()
            try:
                self._recent.remember(project.project_dir, summary.display_name)
            except OSError:
                LOGGER.warning("Could not update recent-project history", exc_info=True)
            return {"project": summary.to_dict()}

        return self._new_job("project_creation", run)

    @staticmethod
    def _range_text(value: object) -> str:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise WebBoundaryError("请输入有效的分钟范围。", code="invalid_range")
        text = str(value).strip()
        if not text or len(text) > 64:
            raise WebBoundaryError("请输入有效的分钟范围。", code="invalid_range")
        try:
            minutes_to_ns(text)
        except ValueError as exc:
            raise WebBoundaryError("请输入有效的分钟范围。", code="invalid_range") from exc
        return text

    def start_range_preview(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload) != {"start_min", "end_min"}:
            raise WebBoundaryError(
                "范围预览请求不完整或包含不支持的字段。",
                code="invalid_range",
            )
        self._require_project_stable()
        start_text = self._range_text(payload.get("start_min"))
        end_text = self._range_text(payload.get("end_min"))
        if minutes_to_ns(end_text) < minutes_to_ns(start_text):
            raise WebBoundaryError("范围终点不能早于起点。", code="invalid_range")
        workspace = self._active_workspace()
        project_path = workspace.project.project_dir

        def run(record: _JobRecord) -> dict[str, Any]:
            if record.cancel_event.is_set():
                raise CancelledError("range preview cancelled")
            preview = preview_range_change(project_path, start_text, end_text)
            if record.cancel_event.is_set():
                raise CancelledError("range preview cancelled")
            with self._lock:
                self._require_open()
                active = self._workspace
                if (
                    self._project_mutation_pending
                    or active is None
                    or active.project.project_dir != project_path
                ):
                    raise WebBoundaryError(
                        "项目已经变化，请重新计算范围预览。",
                        code="stale_range_preview",
                        status=HTTPStatus.CONFLICT,
                    )
                token = secrets.token_urlsafe(24)
                self._range_previews[token] = _RangePreviewCapability(
                    token=token,
                    project_path=project_path,
                    preview=preview,
                )
            result = RangeChangePreviewView(
                preview_token=token,
                old_range=AnalysisRangeView.from_nanoseconds(
                    int(preview.old_analysis_range["start_ns"]),
                    int(preview.old_analysis_range["end_ns"]),
                ),
                new_range=AnalysisRangeView.from_nanoseconds(
                    preview.new_analysis_range.start_ns,
                    preview.new_analysis_range.end_ns,
                ),
                impacts=RangeImpactView(
                    reusable_count=preview.mapped_count,
                    moved_out_count=preview.stale_count,
                    needs_reconfirmation_count=len(preview.plan.ambiguous_event_ids),
                    newly_detected_count=preview.new_count,
                    retained_manual_count=len(preview.plan.manual_event_ids),
                ),
            )
            return {"range_preview": result.to_dict()}

        return self._new_job("range_preview", run)

    def cancel_range_preview(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload) != {"preview_token"}:
            raise WebBoundaryError("取消范围预览请求无效。", code="invalid_request")
        token = payload.get("preview_token")
        with self._lock:
            capability = self._range_previews.pop(token, None) if isinstance(token, str) else None
        if capability is None:
            raise WebBoundaryError(
                "范围预览已经失效，请重新计算。",
                code="stale_range_preview",
                status=HTTPStatus.CONFLICT,
            )
        return {"ok": True, "cancelled": True}

    def start_range_apply(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload).difference(
            {"preview_token", "confirmed", "note"}
        ):
            raise WebBoundaryError("应用范围预览请求包含不支持的字段。")
        if set(payload).difference({"note"}) != {"preview_token", "confirmed"}:
            raise WebBoundaryError("应用范围预览请求不完整。")
        if payload.get("confirmed") is not True:
            raise WebBoundaryError(
                "必须明确确认预览内容后才能更新分析范围。",
                code="confirmation_required",
            )
        note = _optional_note(payload.get("note"))
        token = payload.get("preview_token")
        with self._lock:
            self._require_open()
            export_running = any(
                record.kind in {"review_export", "audit_export"}
                and record.state in {JobState.QUEUED, JobState.RUNNING, JobState.CANCELLING}
                for record in self._jobs.values()
            )
            if self._project_mutation_pending or export_running:
                raise WebBoundaryError(
                    "项目正在完成另一项保存操作，请等待后重试。",
                    code="project_busy",
                    status=HTTPStatus.CONFLICT,
                )
            capability = self._range_previews.pop(token, None) if isinstance(token, str) else None
            active = self._workspace
            if (
                capability is None
                or active is None
                or active.project.project_dir != capability.project_path
            ):
                raise WebBoundaryError(
                    "范围预览已经失效，请重新计算。",
                    code="stale_range_preview",
                    status=HTTPStatus.CONFLICT,
                )
            self._project_mutation_pending = True

        def run(_record: _JobRecord) -> dict[str, Any]:
            replacement: BrowserWorkspaceService | None = None
            try:
                try:
                    project = apply_range_change(
                        capability.preview,
                        confirmed=True,
                        actor="desktop-user",
                        session_id="web-range-" + secrets.token_urlsafe(16),
                        reason=note,
                    )
                except ProjectValidationError as exc:
                    if "stale" in str(exc).casefold():
                        raise WebBoundaryError(
                            "范围预览已经失效，请重新计算。",
                            code="stale_range_preview",
                            status=HTTPStatus.CONFLICT,
                        ) from exc
                    raise
                replacement = BrowserWorkspaceService(project)
                with self._lock:
                    self._require_open()
                    current = self._workspace
                    if current is None or current.project.project_dir != capability.project_path:
                        raise WebBoundaryError(
                            "当前项目已经变化。",
                            code="stale_range_preview",
                            status=HTTPStatus.CONFLICT,
                        )
                    self._workspace = replacement
                    self._active_project = project
                    self._range_previews.clear()
                    replacement = None
                current.close()
                workspace_payload = self._active_workspace().workspace()
                return {"ok": True, "workspace": workspace_payload}
            finally:
                if replacement is not None:
                    replacement.close()
                with self._lock:
                    self._project_mutation_pending = False

        try:
            return self._new_job("range_apply", run, cancel_allowed=False)
        except Exception:
            with self._lock:
                self._project_mutation_pending = False
            raise

    def start_review_export(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload).difference(
            {"target_token", "include_pending", "note"}
        ):
            raise WebBoundaryError("审阅结果导出请求包含不支持的字段。")
        if "target_token" not in payload:
            raise WebBoundaryError("请选择审阅结果的保存文件。", code="stale_selection")
        include_pending = payload.get("include_pending", False)
        if not isinstance(include_pending, bool):
            raise WebBoundaryError("待定事件开关无效。", code="invalid_export")
        note = _optional_note(payload.get("note"))
        self._require_project_stable()
        selection = self._consume_selection(
            payload.get("target_token"),
            PathRole.REVIEW_EXPORT_FILE,
        )
        workspace = self._active_workspace()

        def run(_record: _JobRecord) -> dict[str, Any]:
            summary = workspace.export_review_results(
                selection.path,
                display_name=selection.display_name,
                include_pending=include_pending,
                note=note,
            )
            return {"export": summary}

        return self._new_job("review_export", run, cancel_allowed=False)

    def start_audit_export(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload).difference({"target_token", "note"}):
            raise WebBoundaryError("完整审计数据包导出请求包含不支持的字段。")
        if "target_token" not in payload:
            raise WebBoundaryError("请选择完整审计数据包的保存位置。", code="stale_selection")
        note = _optional_note(payload.get("note"))
        self._require_project_stable()
        selection = self._consume_selection(
            payload.get("target_token"),
            PathRole.AUDIT_EXPORT_TARGET,
        )
        workspace = self._active_workspace()

        def run(_record: _JobRecord) -> dict[str, Any]:
            summary = workspace.export_audit_package(
                selection.path,
                display_name=selection.display_name,
                note=note,
            )
            return {"export": summary}

        return self._new_job("audit_export", run, cancel_allowed=False)

    def _active_workspace(self) -> BrowserWorkspaceService:
        with self._lock:
            self._require_open()
            workspace = self._workspace
        if workspace is None:
            raise WebBoundaryError(
                "请先创建或打开一个项目。",
                code="project_not_open",
                status=HTTPStatus.CONFLICT,
            )
        return workspace

    @staticmethod
    def _workspace_failure(error: BaseException) -> WebBoundaryError:
        if isinstance(error, WorkspaceRequestError):
            status = (
                HTTPStatus.CONFLICT
                if error.code
                in {
                    "stale_action",
                    "stale_event",
                    "stale_edit",
                    "stale_preview",
                    "edit_conflict",
                }
                else HTTPStatus.BAD_REQUEST
            )
            return WebBoundaryError(str(error), code=error.code, status=status)
        if isinstance(error, ReviewConflict):
            return WebBoundaryError(
                "项目已在另一个窗口更新，请重新载入后再试。",
                code="review_conflict",
                status=HTTPStatus.CONFLICT,
            )
        if isinstance(error, ValueError):
            return WebBoundaryError(
                "当前事件无法完成该操作，请检查状态后重试。",
                code="invalid_review_action",
            )
        raise error

    def workspace(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        try:
            return self._active_workspace().workspace(payload)
        except (WorkspaceRequestError, ReviewConflict, ValueError) as exc:
            raise self._workspace_failure(exc) from exc

    def review_decision(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_project_stable()
        try:
            return self._active_workspace().review_decision(payload)
        except (WorkspaceRequestError, ReviewConflict, ValueError) as exc:
            raise self._workspace_failure(exc) from exc

    def restore_automatic_apex(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_project_stable()
        try:
            return self._active_workspace().restore_automatic_apex(payload)
        except (WorkspaceRequestError, ReviewConflict, ValueError) as exc:
            raise self._workspace_failure(exc) from exc

    def begin_event_edit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_project_stable()
        try:
            return self._active_workspace().begin_event_edit(payload)
        except (WorkspaceRequestError, ReviewConflict, ValueError) as exc:
            raise self._workspace_failure(exc) from exc

    def preview_event_edit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_project_stable()
        try:
            return self._active_workspace().preview_event_edit(payload)
        except (WorkspaceRequestError, ReviewConflict, ValueError) as exc:
            raise self._workspace_failure(exc) from exc

    def apply_event_edit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_project_stable()
        try:
            return self._active_workspace().apply_event_edit(payload)
        except (WorkspaceRequestError, ReviewConflict, ValueError) as exc:
            raise self._workspace_failure(exc) from exc

    def cancel_event_edit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_project_stable()
        try:
            return self._active_workspace().cancel_event_edit(payload)
        except (WorkspaceRequestError, ReviewConflict, ValueError) as exc:
            raise self._workspace_failure(exc) from exc

    def undo_review(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_project_stable()
        try:
            return self._active_workspace().undo(payload)
        except (WorkspaceRequestError, ReviewConflict, ValueError) as exc:
            raise self._workspace_failure(exc) from exc

    def redo_review(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_project_stable()
        try:
            return self._active_workspace().redo(payload)
        except (WorkspaceRequestError, ReviewConflict, ValueError) as exc:
            raise self._workspace_failure(exc) from exc

    def job(self, job_id: object) -> dict[str, Any]:
        if not isinstance(job_id, str) or not _OPAQUE_ID.fullmatch(job_id):
            raise WebBoundaryError(
                "后台任务不存在。",
                code="job_not_found",
                status=HTTPStatus.NOT_FOUND,
            )
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise WebBoundaryError(
                    "后台任务不存在。",
                    code="job_not_found",
                    status=HTTPStatus.NOT_FOUND,
                )
            return {"job": self._job_view_locked(record).to_dict()}

    def cancel_job(self, job_id: object) -> dict[str, Any]:
        if not isinstance(job_id, str) or not _OPAQUE_ID.fullmatch(job_id):
            raise WebBoundaryError(
                "后台任务不存在。",
                code="job_not_found",
                status=HTTPStatus.NOT_FOUND,
            )
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise WebBoundaryError(
                    "后台任务不存在。",
                    code="job_not_found",
                    status=HTTPStatus.NOT_FOUND,
                )
            if (
                record.cancel_allowed
                and record.state in {JobState.QUEUED, JobState.RUNNING}
            ):
                record.cancel_event.set()
                if record.future is not None and record.future.cancel():
                    record.state = JobState.CANCELLED
                    record.phase = "cancelled"
                else:
                    record.state = JobState.CANCELLING
                    record.phase = "cancelling"
            return {"ok": True, "job": self._job_view_locked(record).to_dict()}

    @property
    def busy(self) -> bool:
        """Whether closing could interrupt a queued or active background job."""

        with self._lock:
            return any(
                record.state in {JobState.QUEUED, JobState.RUNNING, JobState.CANCELLING}
                for record in self._jobs.values()
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for record in self._jobs.values():
                if record.state in {JobState.QUEUED, JobState.RUNNING, JobState.CANCELLING}:
                    record.cancel_event.set()
            self._inspections.clear()
            self._range_previews.clear()
            self._selections.clear()
            self._selection_keys.clear()
            workspace = self._workspace
            self._workspace = None
            self._active_project = None
            self._project_mutation_pending = False
        if workspace is not None:
            workspace.close()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def __enter__(self) -> "WebSession":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


class RequestActivity:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._paths: dict[str, int] = {}

    @contextmanager
    def track(self, path: str) -> Iterator[None]:
        with self._lock:
            self._paths[path] = self._paths.get(path, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                remaining = self._paths.get(path, 1) - 1
                if remaining:
                    self._paths[path] = remaining
                else:
                    self._paths.pop(path, None)

    def active_paths(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._paths))


PathDialog = Callable[..., str | Path | Mapping[str, Any] | None]


class LocalWebServer(ThreadingHTTPServer):
    """Random-port loopback server with a native-main-document capability."""

    allow_reuse_address = False
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        session: WebSession,
        capability_token: str,
        static_root: Path,
        path_dialog: PathDialog | None,
    ) -> None:
        self.session = session
        self.capability_token = capability_token
        self.static_root = static_root.resolve(strict=False)
        self.path_dialog = path_dialog
        self.request_activity = RequestActivity()
        self._serve_thread: threading.Thread | None = None
        self._stopped = False
        super().__init__(server_address, handler_class)

    @property
    def base_url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/"

    @property
    def url(self) -> str:
        return self.base_url

    @property
    def capability_url(self) -> str:
        return f"{self.base_url}?{urlencode({'native_bridge': self.capability_token})}"

    @property
    def webview_url(self) -> str:
        """LMA-compatible name for the native main-document capability URL."""

        return self.capability_url

    @property
    def busy(self) -> bool:
        return bool(self.active_paths) or self.session.busy

    @property
    def active_paths(self) -> tuple[str, ...]:
        return self.request_activity.active_paths()

    def set_path_dialog(self, provider: PathDialog | None) -> None:
        self.path_dialog = provider

    def start(self) -> None:
        if self._stopped:
            raise RuntimeError("local web server has already stopped")
        if self._serve_thread is not None:
            return
        self._serve_thread = threading.Thread(
            target=self.serve_forever,
            name="ms-event-local-http",
            daemon=True,
        )
        self._serve_thread.start()

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        thread = self._serve_thread
        if thread is not None:
            self.shutdown()
            if thread is not threading.current_thread():
                thread.join(timeout=5.0)
        self.server_close()
        self.session.close()

    def __enter__(self) -> "LocalWebServer":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.stop()


class WebRequestHandler(BaseHTTPRequestHandler):
    server: LocalWebServer
    server_version = "MS-Event-Studio"
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.debug("%s - %s", self.address_string(), fmt % args)

    def _expected_origins(self) -> set[str]:
        host, port = self.server.server_address[:2]
        return {f"http://{host}:{port}", f"http://localhost:{port}"}

    def _request_is_same_origin(self) -> bool:
        try:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                return False
        except ValueError:
            return False
        expected = self._expected_origins()
        allowed_hosts = {urlparse(origin).netloc.casefold() for origin in expected}
        host = self.headers.get("Host", "").strip().casefold()
        origin = self.headers.get("Origin", "").strip()
        return host in allowed_hosts and (not origin or origin in expected)

    def _native_bridge_authorized(self, parsed: Any) -> bool:
        if parsed.path != "/":
            return False
        supplied = str(parse_qs(parsed.query).get("native_bridge", [""])[0])
        return bool(supplied) and hmac.compare_digest(supplied, self.server.capability_token)

    def _security_headers(self, *, native_bridge: bool = False) -> None:
        script = "script-src 'self'"
        if native_bridge:
            # pywebview 6.2.1 constructs its declared JS API with new Function.
            # Only the unguessable native main-document URL receives this bit.
            script += " 'unsafe-eval'"
        policy = (
            "default-src 'self'; "
            f"{script}; "
            "style-src 'self'; img-src 'self' data:; font-src 'self'; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'"
        )
        self.send_header("Content-Security-Policy", policy)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

    def _send_bytes(
        self,
        payload: bytes,
        *,
        status: HTTPStatus = HTTPStatus.OK,
        content_type: str,
        native_bridge: bool = False,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self._security_headers(native_bridge=native_bridge)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _send_json(
        self,
        payload: Mapping[str, Any],
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(
            raw,
            status=status,
            content_type="application/json; charset=utf-8",
        )

    def _send_error(self, error: WebBoundaryError) -> None:
        self._send_json(
            {"ok": False, "error": {"code": error.code, "message": str(error)}},
            error.status,
        )

    def _read_json(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding"):
            raise WebBoundaryError("不支持分块请求。", code="invalid_request")
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        if media_type != "application/json":
            raise WebBoundaryError("请求必须使用 JSON。", code="invalid_request")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise WebBoundaryError("请求长度无效。", code="invalid_request") from exc
        if length < 0 or length > MAX_JSON_BYTES:
            raise WebBoundaryError("请求内容过大。", code="request_too_large")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WebBoundaryError("请求 JSON 无效。", code="invalid_request") from exc
        if not isinstance(payload, dict):
            raise WebBoundaryError("请求内容必须是对象。", code="invalid_request")
        return payload

    def _require_write_token(self) -> None:
        supplied = self.headers.get(WRITE_TOKEN_HEADER, "")
        if not supplied or not hmac.compare_digest(supplied, self.server.session.request_token):
            raise WebBoundaryError(
                "操作授权已失效，请刷新页面后重试。",
                code="invalid_request_token",
                status=HTTPStatus.FORBIDDEN,
            )

    def _static_file(self, parsed: Any) -> tuple[Path | None, bool]:
        if parsed.path == "/":
            return self.server.static_root / "index.html", self._native_bridge_authorized(parsed)
        relative = unquote(parsed.path).lstrip("/")
        if not relative or "\x00" in relative or ":" in relative:
            return None, False
        try:
            candidate = (self.server.static_root / relative).resolve(strict=False)
            candidate.relative_to(self.server.static_root)
        except (OSError, RuntimeError, ValueError):
            return None, False
        return candidate, False

    def _serve_static(self, parsed: Any) -> bool:
        path, native_bridge = self._static_file(parsed)
        if path is None:
            return False
        if parsed.path == "/" and not path.is_file():
            fallback = (
                "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
                "<title>MS Event Studio</title></head><body>MS Event Studio</body></html>"
            ).encode("utf-8")
            self._send_bytes(
                fallback,
                content_type="text/html; charset=utf-8",
                native_bridge=native_bridge,
            )
            return True
        if not path.is_file():
            return False
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise WebBoundaryError(
                "页面资源暂时不可用。",
                code="asset_unavailable",
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            ) from exc
        guessed = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if guessed.startswith("text/") or guessed in {"application/javascript", "application/json"}:
            guessed += "; charset=utf-8"
        self._send_bytes(
            payload,
            content_type=guessed,
            native_bridge=native_bridge,
        )
        return True

    def _invoke_path_dialog(self, role: PathRole) -> dict[str, Any]:
        provider = self.server.path_dialog
        if provider is None:
            raise WebBoundaryError(
                "当前环境无法打开本机选择窗口。",
                code="native_dialog_unavailable",
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
        titles = {
            PathRole.SOURCE_FILE: "选择原始 MS 文本导出",
            PathRole.PROJECT_OPEN: "打开 MS Event Studio 项目",
            PathRole.PROJECT_TARGET: "选择新项目的保存位置",
            PathRole.REVIEW_EXPORT_FILE: "导出审阅结果",
            PathRole.AUDIT_EXPORT_TARGET: "导出完整审计数据包",
        }
        try:
            result = provider(role=role.value, title=titles[role])
        except Exception as exc:
            LOGGER.exception("Native path dialog failed")
            raise WebBoundaryError(
                "无法打开本机选择窗口，请重试。",
                code="native_dialog_failed",
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            ) from exc
        if isinstance(result, Mapping):
            if bool(result.get("cancelled")):
                return {"cancelled": True}
            selected = result.get("path")
        else:
            selected = result
        if selected in {None, ""}:
            return {"cancelled": True}
        return self.server.session.register_path(role, selected)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        with self.server.request_activity.track(parsed.path):
            if not self._request_is_same_origin():
                self._send_error(
                    WebBoundaryError(
                        "该请求不是从本机应用发出的，已安全阻止。",
                        code="cross_origin_blocked",
                        status=HTTPStatus.FORBIDDEN,
                    )
                )
                return
            try:
                if parsed.path.startswith("/api/") and parsed.query:
                    raise WebBoundaryError("请求地址无效。", code="invalid_request")
                if parsed.path == "/api/health":
                    self._send_json({"ok": True, "service": APP_NAME})
                    return
                if parsed.path == "/api/bootstrap":
                    self._send_json(self.server.session.bootstrap())
                    return
                if parsed.path == "/api/workspace":
                    self._send_json(self.server.session.workspace())
                    return
                if parsed.path.startswith("/api/jobs/"):
                    identity = parsed.path.removeprefix("/api/jobs/")
                    if "/" in identity:
                        raise WebBoundaryError(
                            "未找到该操作。",
                            code="not_found",
                            status=HTTPStatus.NOT_FOUND,
                        )
                    self._send_json(self.server.session.job(identity))
                    return
                if parsed.path == "/favicon.ico":
                    self.send_response(HTTPStatus.NO_CONTENT)
                    self._security_headers()
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self._serve_static(parsed):
                    return
                raise WebBoundaryError(
                    "未找到请求的页面或操作。",
                    code="not_found",
                    status=HTTPStatus.NOT_FOUND,
                )
            except WebBoundaryError as exc:
                self._send_error(exc)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                LOGGER.debug("Client disconnected during GET %s", parsed.path)
            except Exception:
                LOGGER.exception("Unhandled GET failure for %s", parsed.path)
                self._send_error(
                    WebBoundaryError(
                        "操作未完成，请重试。",
                        code="operation_failed",
                        status=HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                )

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        with self.server.request_activity.track(parsed.path):
            if not self._request_is_same_origin():
                self._send_error(
                    WebBoundaryError(
                        "该请求不是从本机应用发出的，已安全阻止。",
                        code="cross_origin_blocked",
                        status=HTTPStatus.FORBIDDEN,
                    )
                )
                return
            try:
                if parsed.query:
                    raise WebBoundaryError("请求地址无效。", code="invalid_request")
                self._require_write_token()
                payload = self._read_json()
                if parsed.path == "/api/select-path":
                    extra = set(payload).difference({"role"})
                    if extra:
                        raise WebBoundaryError("请求包含不支持的字段。", code="invalid_request")
                    try:
                        role = PathRole.parse(payload.get("role"))
                    except ValueError as exc:
                        raise WebBoundaryError(str(exc), code="invalid_selection_role") from exc
                    self._send_json(self._invoke_path_dialog(role))
                    return
                if parsed.path == "/api/projects/open":
                    if set(payload).difference({"project_token"}):
                        raise WebBoundaryError("请求包含不支持的字段。", code="invalid_request")
                    self._send_json(self.server.session.open_project(payload.get("project_token")))
                    return
                if parsed.path == "/api/source-inspections":
                    if set(payload).difference({"source_token"}):
                        raise WebBoundaryError("请求包含不支持的字段。", code="invalid_request")
                    self._send_json(
                        self.server.session.start_source_inspection(payload.get("source_token")),
                        HTTPStatus.ACCEPTED,
                    )
                    return
                if parsed.path == "/api/projects":
                    self._send_json(
                        self.server.session.start_project_creation(payload),
                        HTTPStatus.ACCEPTED,
                    )
                    return
                if parsed.path == "/api/range-changes/preview":
                    self._send_json(
                        self.server.session.start_range_preview(payload),
                        HTTPStatus.ACCEPTED,
                    )
                    return
                if parsed.path == "/api/range-changes/apply":
                    self._send_json(
                        self.server.session.start_range_apply(payload),
                        HTTPStatus.ACCEPTED,
                    )
                    return
                if parsed.path == "/api/range-changes/cancel":
                    self._send_json(self.server.session.cancel_range_preview(payload))
                    return
                if parsed.path == "/api/exports/review-results":
                    self._send_json(
                        self.server.session.start_review_export(payload),
                        HTTPStatus.ACCEPTED,
                    )
                    return
                if parsed.path == "/api/exports/audit-package":
                    self._send_json(
                        self.server.session.start_audit_export(payload),
                        HTTPStatus.ACCEPTED,
                    )
                    return
                if parsed.path == "/api/workspace/window":
                    self._send_json(self.server.session.workspace(payload))
                    return
                if parsed.path == "/api/review/decision":
                    self._send_json(self.server.session.review_decision(payload))
                    return
                if parsed.path == "/api/review/restore-automatic-apex":
                    self._send_json(self.server.session.restore_automatic_apex(payload))
                    return
                if parsed.path == "/api/event-edits/aim":
                    self._send_json(self.server.session.begin_event_edit(payload))
                    return
                if parsed.path == "/api/event-edits/preview":
                    self._send_json(self.server.session.preview_event_edit(payload))
                    return
                if parsed.path == "/api/event-edits/apply":
                    self._send_json(self.server.session.apply_event_edit(payload))
                    return
                if parsed.path == "/api/event-edits/cancel":
                    self._send_json(self.server.session.cancel_event_edit(payload))
                    return
                if parsed.path == "/api/review/undo":
                    self._send_json(self.server.session.undo_review(payload))
                    return
                if parsed.path == "/api/review/redo":
                    self._send_json(self.server.session.redo_review(payload))
                    return
                if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/cancel"):
                    identity = parsed.path.removeprefix("/api/jobs/").removesuffix("/cancel")
                    if "/" in identity or payload:
                        raise WebBoundaryError("取消请求无效。", code="invalid_request")
                    self._send_json(self.server.session.cancel_job(identity))
                    return
                raise WebBoundaryError(
                    "未找到请求的页面或操作。",
                    code="not_found",
                    status=HTTPStatus.NOT_FOUND,
                )
            except WebBoundaryError as exc:
                self._send_error(exc)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                LOGGER.debug("Client disconnected during POST %s", parsed.path)
            except Exception:
                LOGGER.exception("Unhandled POST failure for %s", parsed.path)
                self._send_error(
                    WebBoundaryError(
                        "操作未完成，请重试。",
                        code="operation_failed",
                        status=HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                )

    def _method_not_allowed(self) -> None:
        parsed = urlparse(self.path)
        with self.server.request_activity.track(parsed.path):
            if not self._request_is_same_origin():
                self._send_error(
                    WebBoundaryError(
                        "该请求不是从本机应用发出的，已安全阻止。",
                        code="cross_origin_blocked",
                        status=HTTPStatus.FORBIDDEN,
                    )
                )
                return
            self._send_error(
                WebBoundaryError(
                    "不支持该请求方式。",
                    code="method_not_allowed",
                    status=HTTPStatus.METHOD_NOT_ALLOWED,
                )
            )

    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_OPTIONS = _method_not_allowed


def create_http_server(
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    session: WebSession | None = None,
    recent_path: str | Path | None = None,
    static_root: str | Path | None = None,
    path_dialog: PathDialog | None = None,
) -> LocalWebServer:
    """Bind an OS-selected loopback port and return the unstarted server."""

    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("the WebView service may bind only to IPv4 loopback")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be an integer between 0 and 65535")
    active_session = session or WebSession(recent_path=recent_path)
    root = (
        Path(static_root)
        if static_root is not None
        else Path(__file__).resolve().parent / "web"
    )
    try:
        return LocalWebServer(
            (host, port),
            WebRequestHandler,
            session=active_session,
            capability_token=secrets.token_urlsafe(32),
            static_root=root,
            path_dialog=path_dialog,
        )
    except Exception:
        if session is None:
            active_session.close()
        raise


__all__ = [
    "APP_NAME",
    "WRITE_TOKEN_HEADER",
    "LocalWebServer",
    "WebBoundaryError",
    "WebSession",
    "create_http_server",
    "default_recent_path",
]
