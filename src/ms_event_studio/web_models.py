"""Browser-safe view models for the Phase 2R WebView boundary.

The scientific and persistence layers intentionally use stable identities,
integer nanoseconds, revisions, manifests, and filesystem paths.  None of
those implementation details belongs in the normal browser model.  This
module is the one-way adapter: every public model contains user-facing values
or an in-memory opaque capability only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping

from .timebase import NANOSECONDS_PER_MINUTE


class PathRole(str, Enum):
    """Native path selections accepted by the narrow desktop bridge."""

    SOURCE_FILE = "source_file"
    PROJECT_OPEN = "project_open"
    PROJECT_TARGET = "project_target"
    REVIEW_EXPORT_FILE = "review_export_file"
    AUDIT_EXPORT_PARENT = "audit_export_parent"

    @classmethod
    def parse(cls, value: object) -> "PathRole":
        if not isinstance(value, str):
            raise ValueError("路径选择类型无效。")
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError("路径选择类型无效。") from exc


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    FAILED = "failed"


def minutes_text(time_ns: int) -> str:
    """Format an integer-nanosecond boundary without binary-float drift."""

    value = Decimal(int(time_ns)) / Decimal(NANOSECONDS_PER_MINUTE)
    result = format(value.quantize(Decimal("0.000001")), "f").rstrip("0").rstrip(".")
    return result or "0"


@dataclass(frozen=True, slots=True)
class AnalysisRangeView:
    start_min: str
    end_min: str

    @classmethod
    def from_nanoseconds(cls, start_ns: int, end_ns: int) -> "AnalysisRangeView":
        return cls(minutes_text(start_ns), minutes_text(end_ns))

    def to_dict(self) -> dict[str, str]:
        return {"start_min": self.start_min, "end_min": self.end_min}


@dataclass(frozen=True, slots=True)
class SelectionView:
    selection_token: str
    role: PathRole
    display_name: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "cancelled": False,
            "selection_token": self.selection_token,
            "role": self.role.value,
            "display_name": self.display_name,
        }


@dataclass(frozen=True, slots=True)
class RecentProjectView:
    project_token: str
    display_name: str
    last_opened: str

    def to_dict(self) -> dict[str, str]:
        return {
            "project_token": self.project_token,
            "display_name": self.display_name,
            "last_opened": self.last_opened,
        }


@dataclass(frozen=True, slots=True)
class ProjectSummaryView:
    display_name: str
    analysis_range: AnalysisRangeView
    event_count: int
    primary_marker_mz: float
    collision_gap_sec: float

    def __post_init__(self) -> None:
        if isinstance(self.event_count, bool) or int(self.event_count) < 0:
            raise ValueError("event_count must be a non-negative integer")
        _finite_number(self.primary_marker_mz, "primary_marker_mz", non_negative=True)
        _finite_number(self.collision_gap_sec, "collision_gap_sec", non_negative=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "display_name": self.display_name,
            "analysis_range": self.analysis_range.to_dict(),
            "event_count": int(self.event_count),
            "primary_marker_mz": float(self.primary_marker_mz),
            "collision_gap_sec": float(self.collision_gap_sec),
        }


@dataclass(frozen=True, slots=True)
class SourceInspectionView:
    inspection_token: str
    source_name: str
    available_range: AnalysisRangeView
    scan_count: int
    size_bytes: int

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or int(value) < 0
            for value in (self.scan_count, self.size_bytes)
        ):
            raise ValueError("inspection counts must be non-negative integers")

    def to_dict(self) -> dict[str, Any]:
        return {
            "inspection_token": self.inspection_token,
            "source_name": self.source_name,
            # Kept as a deliberate UI alias so the create form can label the
            # selected source without ever receiving its filesystem path.
            "display_name": self.source_name,
            "available_range": self.available_range.to_dict(),
            "scan_count": int(self.scan_count),
            "size_bytes": int(self.size_bytes),
        }


@dataclass(frozen=True, slots=True)
class JobProgressView:
    fraction: float
    bytes_read: int
    total_bytes: int
    parsed_spectra: int

    def __post_init__(self) -> None:
        values = (self.bytes_read, self.total_bytes, self.parsed_spectra)
        if any(isinstance(value, bool) or int(value) < 0 for value in values):
            raise ValueError("job progress counts must be non-negative integers")
        numeric = float(self.fraction)
        if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
            raise ValueError("job progress fraction must be between zero and one")

    def to_dict(self) -> dict[str, int | float]:
        return {
            "fraction": float(self.fraction),
            "bytes_read": int(self.bytes_read),
            "total_bytes": int(self.total_bytes),
            "parsed_spectra": int(self.parsed_spectra),
        }


@dataclass(frozen=True, slots=True)
class JobErrorView:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class JobView:
    job_id: str
    state: JobState
    phase: str
    progress: JobProgressView
    cancellable: bool
    result: Mapping[str, Any] | None = None
    error: JobErrorView | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "job_id": self.job_id,
            "state": self.state.value,
            "phase": self.phase,
            "cancellable": bool(self.cancellable),
            "progress": self.progress.to_dict(),
        }
        if self.result is not None:
            payload["result"] = dict(self.result)
        if self.error is not None:
            payload["error"] = self.error.to_dict()
        return payload


@dataclass(frozen=True, slots=True)
class BootstrapView:
    application_name: str
    application_version: str
    request_token: str
    recent_projects: tuple[RecentProjectView, ...]
    active_project: ProjectSummaryView | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "app": {
                "name": self.application_name,
                "version": self.application_version,
                "language": "zh-CN",
            },
            "view": "project" if self.active_project is not None else "welcome",
            "request_token": self.request_token,
            "recent_projects": [row.to_dict() for row in self.recent_projects],
            "active_project": (
                None if self.active_project is None else self.active_project.to_dict()
            ),
        }


def _finite_number(value: object, field_name: str, *, non_negative: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric) or (non_negative and numeric < 0):
        raise ValueError(f"{field_name} must be a finite number")
    return numeric


class ReviewStatus(str, Enum):
    UNREVIEWED = "unreviewed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PENDING = "pending"


class EventOrigin(str, Enum):
    AUTOMATIC = "automatic"
    MANUAL_ADDED = "manual_added"
    MANUAL_ADJUSTED = "manual_adjusted"


@dataclass(frozen=True, slots=True)
class MarkerView:
    shape: str
    color: str
    code: str
    dash: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "shape": self.shape,
            "color": self.color,
            "code": self.code,
            "dash": list(self.dash),
        }


@dataclass(frozen=True, slots=True)
class WorkspaceEventView:
    event_token: str
    action_token: str
    sequence: int
    apex_time_min: float
    apex_time_sec: float
    apex_intensity: float
    status: ReviewStatus
    status_label: str
    origin: EventOrigin
    origin_label: str
    marker: MarkerView
    apex_modified: bool
    can_restore_automatic_apex: bool

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or int(self.sequence) < 1:
            raise ValueError("sequence must be a positive integer")
        _finite_number(self.apex_time_min, "apex_time_min")
        _finite_number(self.apex_time_sec, "apex_time_sec")
        _finite_number(self.apex_intensity, "apex_intensity", non_negative=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_token": self.event_token,
            "action_token": self.action_token,
            "sequence": int(self.sequence),
            "apex_time_min": float(self.apex_time_min),
            "apex_time_sec": float(self.apex_time_sec),
            "apex_intensity": float(self.apex_intensity),
            "status": self.status.value,
            "status_label": self.status_label,
            "origin": self.origin.value,
            "origin_label": self.origin_label,
            "marker": self.marker.to_dict(),
            "apex_modified": bool(self.apex_modified),
            "can_restore_automatic_apex": bool(self.can_restore_automatic_apex),
        }


@dataclass(frozen=True, slots=True)
class QualityConclusionView:
    level: str
    label: str
    notes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.level not in {"ok", "attention"}:
            raise ValueError("unsupported quality level")

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "label": self.label, "notes": list(self.notes)}


@dataclass(frozen=True, slots=True)
class CoreEvidenceView:
    primary_marker_intensity: float
    measured_mz: float | None
    mass_error_ppm: float | None
    quality: QualityConclusionView

    def __post_init__(self) -> None:
        _finite_number(
            self.primary_marker_intensity,
            "primary_marker_intensity",
            non_negative=True,
        )
        if self.measured_mz is not None:
            _finite_number(self.measured_mz, "measured_mz")
        if self.mass_error_ppm is not None:
            _finite_number(self.mass_error_ppm, "mass_error_ppm")

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_marker_intensity": float(self.primary_marker_intensity),
            "measured_mz": None if self.measured_mz is None else float(self.measured_mz),
            "mass_error_ppm": (
                None if self.mass_error_ppm is None else float(self.mass_error_ppm)
            ),
            "quality": self.quality.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class AdjustmentRangeView:
    start_sec: float
    end_sec: float

    def __post_init__(self) -> None:
        start = _finite_number(self.start_sec, "start_sec")
        end = _finite_number(self.end_sec, "end_sec")
        if end < start:
            raise ValueError("adjustment range end precedes start")

    def to_dict(self) -> dict[str, float]:
        return {"start_sec": float(self.start_sec), "end_sec": float(self.end_sec)}


@dataclass(frozen=True, slots=True)
class MoreEvidenceView:
    scan_number: str
    ms782_intensity: float | None
    tic: float | None
    prominence: float | None
    physical_width_sec: float | None
    adjustment_range: AdjustmentRangeView | None
    adjustment_offset_sec: float | None

    def __post_init__(self) -> None:
        if not self.scan_number:
            raise ValueError("scan_number must not be empty")
        for field_name in (
            "ms782_intensity",
            "tic",
            "prominence",
            "physical_width_sec",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _finite_number(value, field_name, non_negative=True)
        if self.adjustment_offset_sec is not None:
            _finite_number(self.adjustment_offset_sec, "adjustment_offset_sec")

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_number": self.scan_number,
            "ms782_intensity": (
                None if self.ms782_intensity is None else float(self.ms782_intensity)
            ),
            "tic": None if self.tic is None else float(self.tic),
            "prominence": None if self.prominence is None else float(self.prominence),
            "physical_width_sec": (
                None if self.physical_width_sec is None else float(self.physical_width_sec)
            ),
            "adjustment_range": (
                None if self.adjustment_range is None else self.adjustment_range.to_dict()
            ),
            "adjustment_offset_sec": (
                None
                if self.adjustment_offset_sec is None
                else float(self.adjustment_offset_sec)
            ),
        }


@dataclass(frozen=True, slots=True)
class EventSelectionView:
    event: WorkspaceEventView | None
    index: int
    total: int
    previous_event_token: str | None
    next_event_token: str | None
    next_unreviewed_event_token: str | None
    core_evidence: CoreEvidenceView | None
    more_evidence: MoreEvidenceView | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": None if self.event is None else self.event.to_dict(),
            "index": int(self.index),
            "total": int(self.total),
            "previous_event_token": self.previous_event_token,
            "next_event_token": self.next_event_token,
            "next_unreviewed_event_token": self.next_unreviewed_event_token,
            "core_evidence": (
                None if self.core_evidence is None else self.core_evidence.to_dict()
            ),
            "more_evidence": (
                None if self.more_evidence is None else self.more_evidence.to_dict()
            ),
        }


@dataclass(frozen=True, slots=True)
class ReviewProgressView:
    total: int
    reviewed: int
    unreviewed: int
    accepted: int
    rejected: int
    pending: int

    def to_dict(self) -> dict[str, int]:
        return {
            "total": int(self.total),
            "reviewed": int(self.reviewed),
            "unreviewed": int(self.unreviewed),
            "accepted": int(self.accepted),
            "rejected": int(self.rejected),
            "pending": int(self.pending),
        }


@dataclass(frozen=True, slots=True)
class EventFilterView:
    value: str
    label: str
    count: int

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "label": self.label, "count": int(self.count)}


@dataclass(frozen=True, slots=True)
class TracePointView:
    time_min: float
    intensity: float

    def __post_init__(self) -> None:
        _finite_number(self.time_min, "time_min")
        _finite_number(self.intensity, "intensity", non_negative=True)

    def to_dict(self) -> dict[str, float]:
        return {"time_min": float(self.time_min), "intensity": float(self.intensity)}


class EventEditMode(str, Enum):
    ADD = "add"
    ADJUST = "adjust"


@dataclass(frozen=True, slots=True)
class ApexPointView:
    time_min: float
    intensity: float

    def __post_init__(self) -> None:
        _finite_number(self.time_min, "time_min")
        _finite_number(self.intensity, "intensity", non_negative=True)

    def to_dict(self) -> dict[str, float]:
        return {"time_min": float(self.time_min), "intensity": float(self.intensity)}


@dataclass(frozen=True, slots=True)
class AllowedIntervalView:
    start_min: float
    end_min: float

    def __post_init__(self) -> None:
        start = _finite_number(self.start_min, "start_min")
        end = _finite_number(self.end_min, "end_min")
        if end < start:
            raise ValueError("allowed interval end precedes start")

    def to_dict(self) -> dict[str, float]:
        return {"start_min": float(self.start_min), "end_min": float(self.end_min)}


@dataclass(frozen=True, slots=True)
class EventEditAimView:
    aim_token: str
    mode: EventEditMode
    before: ApexPointView | None
    allowed_interval: AllowedIntervalView

    def to_dict(self) -> dict[str, Any]:
        return {
            "aim_token": self.aim_token,
            "mode": self.mode.value,
            "before": None if self.before is None else self.before.to_dict(),
            "allowed_interval": self.allowed_interval.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class EventEditCandidateView:
    time_min: float
    intensity: float
    offset_sec: float

    def __post_init__(self) -> None:
        _finite_number(self.time_min, "time_min")
        _finite_number(self.intensity, "intensity", non_negative=True)
        _finite_number(self.offset_sec, "offset_sec")

    def to_dict(self) -> dict[str, float]:
        return {
            "time_min": float(self.time_min),
            "intensity": float(self.intensity),
            "offset_sec": float(self.offset_sec),
        }


@dataclass(frozen=True, slots=True)
class EventEditPreviewView:
    preview_token: str
    mode: EventEditMode
    candidate: EventEditCandidateView
    before: ApexPointView | None
    after: ApexPointView
    allowed_interval: AllowedIntervalView

    def to_dict(self) -> dict[str, Any]:
        return {
            "preview_token": self.preview_token,
            "mode": self.mode.value,
            "candidate": self.candidate.to_dict(),
            "change": {
                "before": None if self.before is None else self.before.to_dict(),
                "after": self.after.to_dict(),
            },
            "allowed_interval": self.allowed_interval.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class RangeImpactView:
    reusable_count: int
    moved_out_count: int
    needs_reconfirmation_count: int
    newly_detected_count: int
    retained_manual_count: int

    def __post_init__(self) -> None:
        for name in (
            "reusable_count",
            "moved_out_count",
            "needs_reconfirmation_count",
            "newly_detected_count",
            "retained_manual_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or int(value) < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    def to_dict(self) -> dict[str, int]:
        return {
            "reusable_count": int(self.reusable_count),
            "moved_out_count": int(self.moved_out_count),
            "needs_reconfirmation_count": int(self.needs_reconfirmation_count),
            "newly_detected_count": int(self.newly_detected_count),
            "retained_manual_count": int(self.retained_manual_count),
        }


@dataclass(frozen=True, slots=True)
class RangeChangePreviewView:
    preview_token: str
    old_range: AnalysisRangeView
    new_range: AnalysisRangeView
    impacts: RangeImpactView

    def to_dict(self) -> dict[str, Any]:
        return {
            "preview_token": self.preview_token,
            "old_range": self.old_range.to_dict(),
            "new_range": self.new_range.to_dict(),
            "impacts": self.impacts.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ExportSummaryView:
    kind: str
    display_name: str
    row_count: int
    message: str

    def __post_init__(self) -> None:
        if self.kind not in {"review_results", "audit_package"}:
            raise ValueError("unsupported export kind")
        if not self.display_name or not self.message:
            raise ValueError("export display fields must not be empty")
        if isinstance(self.row_count, bool) or int(self.row_count) < 0:
            raise ValueError("row_count must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "display_name": self.display_name,
            "row_count": int(self.row_count),
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class ViewportView:
    start_min: str
    end_min: str
    analysis_start_min: str
    analysis_end_min: str

    def to_dict(self) -> dict[str, str]:
        return {
            "start_min": self.start_min,
            "end_min": self.end_min,
            "analysis_start_min": self.analysis_start_min,
            "analysis_end_min": self.analysis_end_min,
        }


@dataclass(frozen=True, slots=True)
class BulkReviewSummaryView:
    eligible_count: int
    collision_count: int

    def __post_init__(self) -> None:
        for name in ("eligible_count", "collision_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or int(value) < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    def to_dict(self) -> dict[str, int]:
        return {
            "eligible_count": int(self.eligible_count),
            "collision_count": int(self.collision_count),
        }


@dataclass(frozen=True, slots=True)
class WorkspaceWindowView:
    viewport: ViewportView
    trace: tuple[TracePointView, ...]
    event_overlay: tuple[WorkspaceEventView, ...]
    label_event_tokens: tuple[str, ...]
    bulk_review: BulkReviewSummaryView

    def to_dict(self) -> dict[str, Any]:
        return {
            "viewport": self.viewport.to_dict(),
            "trace": [point.to_dict() for point in self.trace],
            "event_overlay": [event.to_dict() for event in self.event_overlay],
            "label_event_tokens": list(self.label_event_tokens),
            "bulk_review": self.bulk_review.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class HistoryView:
    can_undo: bool
    can_redo: bool

    def to_dict(self) -> dict[str, bool]:
        return {"can_undo": bool(self.can_undo), "can_redo": bool(self.can_redo)}


@dataclass(frozen=True, slots=True)
class WorkspaceView:
    project: ProjectSummaryView
    review: ReviewProgressView
    filters: tuple[EventFilterView, ...]
    events: tuple[WorkspaceEventView, ...]
    selection: EventSelectionView
    window: WorkspaceWindowView
    history: HistoryView

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project.to_dict(),
            "review": self.review.to_dict(),
            "filters": [item.to_dict() for item in self.filters],
            "events": [item.to_dict() for item in self.events],
            "selection": self.selection.to_dict(),
            "window": self.window.to_dict(),
            "history": self.history.to_dict(),
        }
