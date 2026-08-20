"""Rebuildable min/max display pyramids and bounded trace windows.

Display caches are deliberately outside the scientific artifact registry.  A
cache can be deleted or rebuilt without changing event identity, provenance, or
review state.  Event apexes are returned as a separate overlay and therefore
can never disappear because a trace level was decimated.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .canonical import json_value
from .errors import ProjectValidationError


DISPLAY_CACHE_SCHEMA = "ms-event-display-pyramid-v1"
DISPLAY_SIGNAL = "primary_marker_max_intensity"
DISPLAY_COLUMNS = (
    "scan_row_index",
    "spectrum_index",
    "scan_id",
    "scan_time_ns",
    "scan_start_time_sec",
    "primary_marker_max_intensity",
    "qc_marker_max_intensity",
    "tic",
)
DEFAULT_BUCKET_SIZES = (4, 16, 64, 256, 1024, 4096)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_scans(scans: pd.DataFrame) -> None:
    missing = sorted(set(DISPLAY_COLUMNS).difference(scans.columns))
    if missing:
        raise ValueError(f"display scan table is missing: {', '.join(missing)}")
    if scans.empty:
        raise ValueError("display scan table is empty")
    time_ns = scans["scan_time_ns"].to_numpy(dtype=np.int64)
    if len(time_ns) > 1 and np.any(np.diff(time_ns) <= 0):
        raise ValueError("display scan time must be strictly increasing")
    signal = scans[DISPLAY_SIGNAL].to_numpy(dtype=float)
    if not np.isfinite(signal).all() or np.any(signal < 0):
        raise ValueError("display signal must be finite and non-negative")


def min_max_envelope(
    scans: pd.DataFrame,
    *,
    bucket_size: int,
    signal_col: str = DISPLAY_SIGNAL,
) -> pd.DataFrame:
    """Keep each consecutive bucket's physical minimum and maximum samples."""

    if isinstance(bucket_size, bool) or int(bucket_size) < 2:
        raise ValueError("display envelope bucket_size must be at least 2")
    if signal_col not in scans:
        raise ValueError(f"display signal column is missing: {signal_col}")
    if scans.empty:
        return scans.copy()
    signal = scans[signal_col].to_numpy(dtype=float)
    if not np.isfinite(signal).all():
        raise ValueError("display envelope signal must be finite")
    chosen: list[int] = []
    size = int(bucket_size)
    for start in range(0, len(scans), size):
        stop = min(len(scans), start + size)
        values = signal[start:stop]
        local_min = start + int(np.argmin(values))
        local_max = start + int(np.argmax(values))
        chosen.extend(sorted({local_min, local_max}))
    return scans.iloc[chosen].sort_values("scan_time_ns", kind="stable").reset_index(drop=True)


@dataclass(frozen=True, slots=True)
class WindowRequest:
    start_ns: int
    end_ns: int
    point_budget: int = 2_000
    margin_fraction: float = 0.05

    def __post_init__(self) -> None:
        if isinstance(self.start_ns, bool) or isinstance(self.end_ns, bool):
            raise TypeError("window boundaries must be integer nanoseconds")
        if int(self.end_ns) < int(self.start_ns):
            raise ValueError("window end precedes start")
        if isinstance(self.point_budget, bool) or int(self.point_budget) < 32:
            raise ValueError("window point budget must be at least 32")
        margin = float(self.margin_fraction)
        if not np.isfinite(margin) or not 0.0 <= margin <= 1.0:
            raise ValueError("window margin_fraction must be between zero and one")


@dataclass(frozen=True, slots=True)
class DisplayWindow:
    trace: pd.DataFrame
    events: tuple[dict[str, Any], ...]
    start_ns: int
    end_ns: int
    margin_start_ns: int
    margin_end_ns: int
    bucket_size: int


def choose_event_labels(
    events: Iterable[dict[str, Any]],
    *,
    maximum_labels: int,
    selected_event_id: str | None = None,
) -> tuple[str, ...]:
    """Choose deterministic, time-spread labels without suppressing event points."""

    limit = int(maximum_labels)
    if limit <= 0:
        return ()
    ordered = sorted(
        (dict(row) for row in events),
        key=lambda row: (int(row["current_apex_time_ns"]), str(row["event_id"])),
    )
    if len(ordered) <= limit:
        return tuple(str(row["event_id"]) for row in ordered)
    positions = np.linspace(0, len(ordered) - 1, num=limit, dtype=int).tolist()
    chosen = {str(ordered[index]["event_id"]) for index in positions}
    selected = str(selected_event_id) if selected_event_id else None
    all_ids = {str(row["event_id"]) for row in ordered}
    if selected and selected in all_ids and selected not in chosen:
        selected_time = next(
            int(row["current_apex_time_ns"])
            for row in ordered
            if str(row["event_id"]) == selected
        )
        removable = min(
            chosen,
            key=lambda identity: (
                abs(
                    next(
                        int(row["current_apex_time_ns"])
                        for row in ordered
                        if str(row["event_id"]) == identity
                    )
                    - selected_time
                ),
                identity,
            ),
        )
        chosen.remove(removable)
        chosen.add(selected)
    return tuple(
        str(row["event_id"])
        for row in ordered
        if str(row["event_id"]) in chosen
    )


class DisplayPyramid:
    """An in-process scan table backed by atomically published cache levels."""

    def __init__(
        self,
        scans: pd.DataFrame,
        cache_dir: Path,
        manifest: dict[str, Any],
    ) -> None:
        self._scans = scans.reset_index(drop=True)
        self.cache_dir = cache_dir.resolve()
        self.manifest = manifest
        self.row_count = len(scans)
        self.source_binding = str(manifest["source_binding"])
        self._levels: dict[int, pd.DataFrame] = {1: self._scans}

    @staticmethod
    def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(json_value(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    @staticmethod
    def _safe_remove(path: Path, parent: Path, allowed_prefix: str) -> None:
        if not path.exists():
            return
        if path.is_symlink():
            raise ProjectValidationError("refusing to remove a symlinked display cache")
        resolved = path.resolve()
        if resolved.parent != parent.resolve() or not resolved.name.startswith(allowed_prefix):
            raise ProjectValidationError("refusing to remove an unrecognized display cache path")
        shutil.rmtree(resolved)

    @classmethod
    def build(
        cls,
        scans: pd.DataFrame,
        cache_dir: str | Path,
        *,
        source_binding: str,
    ) -> "DisplayPyramid":
        _validate_scans(scans)
        raw_target = Path(cache_dir)
        if raw_target.is_symlink():
            raise ProjectValidationError("display cache target cannot be a symlink")
        target = raw_target.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not target.is_dir():
            raise ProjectValidationError("display cache target must be a directory")
        staging = target.parent / f".{target.name}.building-{uuid.uuid4().hex}"
        backup = target.parent / f".{target.name}.replaced-{uuid.uuid4().hex}"
        published = False
        try:
            staging.mkdir(parents=False, exist_ok=False)
            columns = [column for column in DISPLAY_COLUMNS if column in scans.columns]
            source = scans.loc[:, columns].copy().reset_index(drop=True)
            level_records: list[dict[str, Any]] = []
            for bucket_size in DEFAULT_BUCKET_SIZES:
                if bucket_size > max(4, len(source) * 4):
                    break
                frame = min_max_envelope(source, bucket_size=bucket_size)
                relative = f"level_{bucket_size}.parquet"
                path = staging / relative
                frame.to_parquet(path, index=False)
                level_records.append(
                    {
                        "bucket_size": bucket_size,
                        "path": relative,
                        "row_count": len(frame),
                        "size_bytes": path.stat().st_size,
                        "sha256": _sha256(path),
                    }
                )
                if len(frame) <= 2:
                    break
            manifest = {
                "schema": DISPLAY_CACHE_SCHEMA,
                "source_binding": str(source_binding),
                "scan_row_count": len(source),
                "start_ns": int(source["scan_time_ns"].iloc[0]),
                "end_ns": int(source["scan_time_ns"].iloc[-1]),
                "signal_column": DISPLAY_SIGNAL,
                "levels": level_records,
            }
            cls._write_manifest(staging / "manifest.json", manifest)
            if target.exists():
                os.replace(target, backup)
            try:
                os.replace(staging, target)
            except Exception:
                if backup.exists() and not target.exists():
                    os.replace(backup, target)
                raise
            published = True
            if backup.exists():
                cls._safe_remove(backup, target.parent, f".{target.name}.replaced-")
            return cls(source, target, manifest)
        finally:
            if not published and staging.exists():
                cls._safe_remove(staging, target.parent, f".{target.name}.building-")

    @classmethod
    def _load_valid_manifest(
        cls,
        scans: pd.DataFrame,
        cache_dir: Path,
        source_binding: str,
    ) -> dict[str, Any] | None:
        if cache_dir.is_symlink() or not cache_dir.is_dir():
            return None
        manifest_path = cache_dir / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(manifest, dict):
            return None
        if (
            manifest.get("schema") != DISPLAY_CACHE_SCHEMA
            or manifest.get("source_binding") != str(source_binding)
            or manifest.get("scan_row_count") != len(scans)
            or manifest.get("start_ns") != int(scans["scan_time_ns"].iloc[0])
            or manifest.get("end_ns") != int(scans["scan_time_ns"].iloc[-1])
        ):
            return None
        levels = manifest.get("levels")
        if not isinstance(levels, list) or not levels:
            return None
        for record in levels:
            if not isinstance(record, dict):
                return None
            relative = record.get("path")
            if not isinstance(relative, str) or Path(relative).name != relative:
                return None
            path = cache_dir / relative
            if (
                not path.is_file()
                or path.stat().st_size != record.get("size_bytes")
                or _sha256(path) != record.get("sha256")
            ):
                return None
        return manifest

    @classmethod
    def open_or_build(
        cls,
        scans: pd.DataFrame,
        cache_dir: str | Path,
        *,
        source_binding: str,
    ) -> "DisplayPyramid":
        _validate_scans(scans)
        raw_target = Path(cache_dir)
        if raw_target.is_symlink():
            raise ProjectValidationError("display cache target cannot be a symlink")
        target = raw_target.resolve()
        manifest = cls._load_valid_manifest(scans, target, source_binding)
        if manifest is None:
            return cls.build(scans, target, source_binding=source_binding)
        columns = [column for column in DISPLAY_COLUMNS if column in scans.columns]
        return cls(scans.loc[:, columns].copy(), target, manifest)

    def _frame_for_level(self, bucket_size: int) -> pd.DataFrame:
        if bucket_size in self._levels:
            return self._levels[bucket_size]
        record = next(
            row for row in self.manifest["levels"] if int(row["bucket_size"]) == bucket_size
        )
        frame = pd.read_parquet(self.cache_dir / str(record["path"]))
        self._levels[bucket_size] = frame
        return frame

    def _choose_level(self, start_ns: int, end_ns: int, point_budget: int) -> int:
        candidates = [1] + [int(row["bucket_size"]) for row in self.manifest["levels"]]
        selected = candidates[-1]
        for bucket_size in candidates:
            frame = self._frame_for_level(bucket_size)
            count = int(frame["scan_time_ns"].between(start_ns, end_ns, inclusive="both").sum())
            if count <= 2 * point_budget:
                selected = bucket_size
                break
        return selected

    def read_window(
        self,
        request: WindowRequest,
        events: Iterable[dict[str, Any]],
    ) -> DisplayWindow:
        data_start = int(self._scans["scan_time_ns"].iloc[0])
        data_end = int(self._scans["scan_time_ns"].iloc[-1])
        if request.end_ns < data_start or request.start_ns > data_end:
            raise ValueError("display window does not overlap the scan time extent")
        start_ns = max(int(request.start_ns), data_start)
        end_ns = min(int(request.end_ns), data_end)
        width = max(1, end_ns - start_ns)
        margin = int(round(width * float(request.margin_fraction)))
        margin_start = max(data_start, start_ns - margin)
        margin_end = min(data_end, end_ns + margin)
        level = self._choose_level(margin_start, margin_end, int(request.point_budget))
        frame = self._frame_for_level(level)
        mask = frame["scan_time_ns"].between(margin_start, margin_end, inclusive="both")
        trace = frame.loc[mask].reset_index(drop=True).copy()
        visible = tuple(
            sorted(
                (
                    dict(row)
                    for row in events
                    if start_ns <= int(row["current_apex_time_ns"]) <= end_ns
                ),
                key=lambda row: (int(row["current_apex_time_ns"]), str(row["event_id"])),
            )
        )
        return DisplayWindow(
            trace=trace,
            events=visible,
            start_ns=start_ns,
            end_ns=end_ns,
            margin_start_ns=margin_start,
            margin_end_ns=margin_end,
            bucket_size=level,
        )
