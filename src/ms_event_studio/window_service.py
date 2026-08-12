"""One-snapshot project windows for the desktop application."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .desktop_model import filter_events
from .display import DisplayPyramid, DisplayWindow, WindowRequest, choose_event_labels
from .paths import resolve_project_path
from .project import Project, open_project
from .review import ReviewStore


@dataclass(frozen=True, slots=True)
class ProjectWindowSnapshot:
    trace: pd.DataFrame
    events: tuple[dict[str, Any], ...]
    label_event_ids: tuple[str, ...]
    selected_event: dict[str, Any] | None
    selected_scan: dict[str, Any] | None
    selected_automatic: dict[str, Any] | None
    start_ns: int
    end_ns: int
    margin_start_ns: int
    margin_end_ns: int
    bucket_size: int
    sqlite_snapshot_count: int


def _artifact_path(project: Project, role: str) -> Path:
    for record in project.manifest["artifacts"]:
        if record["role"] == role:
            return resolve_project_path(project.project_dir, record["path"])
    raise ValueError(f"project artifact role is missing: {role}")


class ProjectWindowService:
    """Own one validated project and serve bounded, internally consistent views."""

    def __init__(
        self,
        project: Project,
        scans: pd.DataFrame,
        automatic: pd.DataFrame,
        review_store: ReviewStore,
        pyramid: DisplayPyramid,
    ) -> None:
        self.project = project
        self.scans = scans
        self.automatic = automatic
        self.review_store = review_store
        self.pyramid = pyramid

    @classmethod
    def open(cls, project_dir: str | Path) -> "ProjectWindowService":
        project = open_project(project_dir)
        scans = pd.read_parquet(_artifact_path(project, "scan_summary"))
        automatic = pd.read_parquet(_artifact_path(project, "automatic_events"))
        review_path = resolve_project_path(project.project_dir, project.manifest["review"]["path"])
        store = ReviewStore.open(review_path, project_id=project.manifest["project_id"])
        cache_path = resolve_project_path(project.project_dir, "cache/display_pyramids")
        pyramid = DisplayPyramid.open_or_build(
            scans,
            cache_path,
            source_binding=project.manifest["source"]["source_sha256"],
        )
        return cls(project, scans, automatic, store, pyramid)

    @property
    def analysis_start_ns(self) -> int:
        return int(self.project.manifest["analysis_range"]["start_ns"])

    @property
    def analysis_end_ns(self) -> int:
        return int(self.project.manifest["analysis_range"]["end_ns"])

    def all_events(self) -> list[dict[str, Any]]:
        return self.review_store.list_events()

    def window(
        self,
        request: WindowRequest,
        *,
        status_filter: str = "all",
        selected_event_id: str | None = None,
        maximum_labels: int = 30,
    ) -> ProjectWindowSnapshot:
        # This is the only SQLite read for one window request.  Every event in
        # the response therefore belongs to the same database snapshot.
        all_events = self.review_store.list_events()
        selected = next(
            (dict(row) for row in all_events if str(row["event_id"]) == str(selected_event_id)),
            None,
        )
        filtered = filter_events(all_events, status_filter)
        display = self.pyramid.read_window(request, filtered)
        selected_scan = None
        selected_automatic = None
        if selected is not None:
            row_index = int(selected["current_scan_row_index"])
            matches = self.scans[self.scans["scan_row_index"].astype("int64") == row_index]
            if len(matches) == 1:
                selected_scan = matches.iloc[0].to_dict()
            automatic_id = selected.get("original_auto_event_id") or selected.get("auto_event_id")
            if automatic_id and "auto_event_id" in self.automatic:
                evidence = self.automatic[
                    self.automatic["auto_event_id"].astype(str) == str(automatic_id)
                ]
                if len(evidence) == 1:
                    selected_automatic = evidence.iloc[0].to_dict()
        labels = choose_event_labels(
            display.events,
            maximum_labels=maximum_labels,
            selected_event_id=selected_event_id,
        )
        return ProjectWindowSnapshot(
            trace=display.trace,
            events=display.events,
            label_event_ids=labels,
            selected_event=selected,
            selected_scan=selected_scan,
            selected_automatic=selected_automatic,
            start_ns=display.start_ns,
            end_ns=display.end_ns,
            margin_start_ns=display.margin_start_ns,
            margin_end_ns=display.margin_end_ns,
            bucket_size=display.bucket_size,
            sqlite_snapshot_count=1,
        )

    def close(self) -> None:
        self.review_store.close()

    def __enter__(self) -> "ProjectWindowService":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
