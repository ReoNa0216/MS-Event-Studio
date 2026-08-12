"""Versioned PC34/MS760 event detector.

The adaptive threshold model intentionally follows the LMA Studio v0.4.4
behavior.  This version fixes exact 2-minute bin ownership and converts SciPy
fractional support indices through the physical (possibly irregular) time axis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy.signal import find_peaks, peak_prominences, peak_widths

from .canonical import content_sha256
from .identity import auto_event_id, generation_id as make_generation_id
from .parser import PARSER_VERSION
from .timebase import AnalysisRange


DETECTOR_VERSION = "pc34-adaptive-v1.1-physical-width"
BOUNDARY_RULE = "closed_current_apex_v1"
BIN_SIZE_MIN = 2.0
COLLISION_GAP_SEC = 0.60
BROAD_PEAK_WIDTH_SEC = 1.50
LOW_TIC_THRESHOLD = 1e6
LOW_ARRAY_LENGTH_THRESHOLD = 6000
LOW_ARRAY_LENGTH_SEVERE = 1000
PC34_FALLBACK_HEIGHT_FRACTION = 0.10
PC34_FALLBACK_PROMINENCE_FRACTION = 0.10


EVENT_COLUMNS = (
    "auto_event_id",
    "generation_id",
    "source_sha256",
    "detector_version",
    "parameter_hash",
    "event_strategy",
    "primary_signal_col",
    "scan_row_index",
    "spectrum_index",
    "scan_id",
    "scan_time_ns",
    "apex_time_min",
    "apex_time_sec",
    "apex_intensity",
    "peak_prominence",
    "peak_width_sec",
    "left_time_ns",
    "right_time_ns",
    "left_sec",
    "right_sec",
    "local_scan_interval_sec",
    "window_scan_count",
    "pc34_760_apex",
    "qc_782_apex",
    "pc34_760_ppm_error_at_apex",
    "qc_782_ppm_error_at_apex",
    "tic_apex",
    "ratio_760_782_max_pseudo1",
    "array_length_apex",
    "base_peak_mz_apex",
    "low_array_length_lt_6000_window",
    "low_array_length_lt_1000_window",
    "low_tic_lt_1e6_window",
    "calling_height",
    "calling_prominence",
    "calling_min_distance_sec",
    "prev_event_gap_sec",
    "next_event_gap_sec",
    "nearest_event_gap_sec",
    "collision_risk_high",
    "broad_peak_width_gt_1p5_sec",
    "low_quality_scan_window",
)

_INTEGER_EVENT_COLUMNS = {
    "scan_row_index",
    "spectrum_index",
    "scan_time_ns",
    "left_time_ns",
    "right_time_ns",
    "window_scan_count",
    "array_length_apex",
}
_BOOLEAN_EVENT_COLUMNS = {
    "low_array_length_lt_6000_window",
    "low_array_length_lt_1000_window",
    "low_tic_lt_1e6_window",
    "collision_risk_high",
    "broad_peak_width_gt_1p5_sec",
    "low_quality_scan_window",
}
_STRING_EVENT_COLUMNS = {
    "auto_event_id",
    "generation_id",
    "source_sha256",
    "detector_version",
    "parameter_hash",
    "event_strategy",
    "primary_signal_col",
    "scan_id",
}


@dataclass(frozen=True, slots=True)
class DetectionResult:
    events: pd.DataFrame
    parameters: dict[str, Any]
    bin_summary: pd.DataFrame
    quiet_bins: pd.DataFrame
    generation_id: str
    parameter_hash: str


def _typed_events(rows: list[dict[str, Any]] | None = None) -> pd.DataFrame:
    frame = pd.DataFrame(rows or [], columns=EVENT_COLUMNS)
    for column in EVENT_COLUMNS:
        if column in _INTEGER_EVENT_COLUMNS:
            frame[column] = pd.array(frame[column], dtype="Int64")
        elif column in _BOOLEAN_EVENT_COLUMNS:
            frame[column] = pd.array(frame[column], dtype="boolean")
        elif column in _STRING_EVENT_COLUMNS:
            frame[column] = pd.array(frame[column], dtype="string")
        else:
            frame[column] = pd.array(frame[column], dtype="Float64")
    return frame


def _validate_scan(scan: pd.DataFrame, signal_col: str) -> None:
    required = {
        signal_col,
        "scan_row_index",
        "spectrum_index",
        "scan_id",
        "scan_time_ns",
        "scan_start_time_min",
        "scan_start_time_sec",
    }
    missing = sorted(required.difference(scan.columns))
    if missing:
        raise ValueError(f"scan table is missing required columns: {', '.join(missing)}")
    if scan.empty:
        raise ValueError("scan table is empty")
    time_ns = scan["scan_time_ns"].to_numpy(dtype=np.int64)
    if len(time_ns) > 1 and np.any(np.diff(time_ns) <= 0):
        raise ValueError("scan_time_ns must be strictly increasing")
    signal = scan[signal_col].to_numpy(dtype=float)
    if not np.isfinite(signal).all() or np.any(signal < 0):
        raise ValueError(f"{signal_col} must be finite and non-negative")


def _median_scan_step_sec(scan: pd.DataFrame) -> float:
    time_sec = scan["scan_start_time_sec"].to_numpy(dtype=float)
    differences = np.diff(time_sec)
    positive = differences[np.isfinite(differences) & (differences > 0)]
    if not len(positive):
        raise ValueError("at least two strictly increasing scan times are required")
    return float(np.median(positive))


def strict_contiguous_runs(
    frame: pd.DataFrame,
    start_col: str = "start_min",
    end_col: str = "end_min",
) -> list[pd.DataFrame]:
    if frame.empty:
        return []
    runs: list[pd.DataFrame] = []
    current: list[pd.Series] = []
    previous_end: float | None = None
    for _, row in frame.sort_values(start_col).iterrows():
        if previous_end is None or abs(float(row[start_col]) - previous_end) <= 1e-6:
            current.append(row)
        else:
            runs.append(pd.DataFrame(current))
            current = [row]
        previous_end = float(row[end_col])
    if current:
        runs.append(pd.DataFrame(current))
    return runs


def build_bin_summary(
    scan: pd.DataFrame,
    signal_col: str,
    dt_sec: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Assign every scan to exactly one half-open 2-minute bin."""

    _validate_scan(scan, signal_col)
    if not np.isfinite(dt_sec) or dt_sec <= 0:
        raise ValueError("dt_sec must be positive")
    signal = scan[signal_col].to_numpy(dtype=float)
    times_min = scan["scan_start_time_min"].to_numpy(dtype=float)
    localmax_distance_points = max(1, int(round(0.30 / dt_sec)))
    localmax_indices, _ = find_peaks(
        signal,
        height=np.nextafter(0.0, 1.0),
        distance=localmax_distance_points,
    )
    localmax = pd.DataFrame(
        {
            "scan_row_index": localmax_indices.astype(np.int64),
            "time_min": times_min[localmax_indices],
            "height": signal[localmax_indices],
        }
    )

    # Integer ownership avoids the v0.4.4 arange endpoint omission.  An exact
    # t=2.0 minute scan belongs to bin 1 and is neither lost nor duplicated.
    ownership = np.floor(times_min / BIN_SIZE_MIN).astype(np.int64)
    local_ownership = (
        np.floor(localmax["time_min"].to_numpy(dtype=float) / BIN_SIZE_MIN).astype(np.int64)
        if len(localmax)
        else np.asarray([], dtype=np.int64)
    )
    rows: list[dict[str, Any]] = []
    for bin_index in np.unique(ownership):
        scan_mask = ownership == bin_index
        peak_mask = local_ownership == bin_index
        sub = signal[scan_mask]
        peak_values = localmax.loc[peak_mask, "height"] if len(localmax) else pd.Series(dtype=float)
        rows.append(
            {
                "signal_col": signal_col,
                "bin_index": int(bin_index),
                "start_min": float(bin_index * BIN_SIZE_MIN),
                "end_min": float((bin_index + 1) * BIN_SIZE_MIN),
                "scan_count": int(scan_mask.sum()),
                "positive_scan_fraction": float(np.mean(sub > 0)),
                "scan_p95": float(np.quantile(sub, 0.95)),
                "scan_p99": float(np.quantile(sub, 0.99)),
                "scan_max": float(np.max(sub)),
                "localmax_count": int(len(peak_values)),
                "localmax_p95": float(peak_values.quantile(0.95)) if len(peak_values) else np.nan,
                "localmax_p99": float(peak_values.quantile(0.99)) if len(peak_values) else np.nan,
                "localmax_max": float(peak_values.max()) if len(peak_values) else np.nan,
            }
        )
    bins = pd.DataFrame(rows).sort_values("bin_index").reset_index(drop=True)
    return bins, localmax


def select_quiet_platform(bin_summary: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    required = {"scan_count", "localmax_p99", "scan_p99", "positive_scan_fraction", "start_min", "end_min"}
    missing = required.difference(bin_summary.columns)
    if missing:
        raise ValueError(f"bin summary is missing: {', '.join(sorted(missing))}")
    valid = bin_summary[bin_summary["scan_count"] >= 200].copy()
    if valid.empty:
        raise ValueError("insufficient background: no 2-minute bin contains at least 200 scans")
    for column in ("localmax_p99", "scan_p99", "positive_scan_fraction"):
        valid[f"{column}_rank"] = valid[column].rank(pct=True)
    valid["quiet_score"] = valid[
        ["localmax_p99_rank", "scan_p99_rank", "positive_scan_fraction_rank"]
    ].mean(axis=1)
    cutoff = valid["quiet_score"].quantile(0.35)
    candidates = valid[valid["quiet_score"] <= cutoff].copy()
    runs = strict_contiguous_runs(candidates)
    if runs:
        runs.sort(
            key=lambda item: (
                float(item["end_min"].max() - item["start_min"].min()),
                -float(item["quiet_score"].mean()),
            ),
            reverse=True,
        )
        selected = runs[0].copy()
        method = "longest_contiguous_low_signal_platform"
    else:
        selected = valid.nsmallest(max(1, min(len(valid), int(np.ceil(len(valid) * 0.20)))), "quiet_score").copy()
        method = "fallback_lowest_signal_bins"
    selected = selected.sort_values("start_min").reset_index(drop=True)
    selected["selected_as_quiet_platform"] = True
    return selected, method


def estimate_parameters(
    scan: pd.DataFrame,
    signal_col: str,
    bin_summary: pd.DataFrame,
    localmax: pd.DataFrame,
    dt_sec: float,
) -> tuple[dict[str, Any], pd.DataFrame]:
    quiet_bins, quiet_method = select_quiet_platform(bin_summary)
    quiet_scan_parts: list[pd.Series] = []
    quiet_peak_parts: list[pd.Series] = []
    for _, row in quiet_bins.iterrows():
        scan_mask = (
            (scan["scan_start_time_min"] >= row["start_min"])
            & (scan["scan_start_time_min"] < row["end_min"])
        )
        peak_mask = (
            (localmax["time_min"] >= row["start_min"])
            & (localmax["time_min"] < row["end_min"])
        )
        quiet_scan_parts.append(scan.loc[scan_mask, signal_col])
        quiet_peak_parts.append(localmax.loc[peak_mask, "height"])
    quiet_scans = pd.concat(quiet_scan_parts, ignore_index=True)
    quiet_peaks = pd.concat(quiet_peak_parts, ignore_index=True)
    if quiet_scans.empty:
        raise ValueError("insufficient background scans after quiet-platform selection")

    quiet_scan_p90 = float(quiet_scans.quantile(0.90))
    quiet_scan_p99 = float(quiet_scans.quantile(0.99))
    quiet_localmax_p75 = float(quiet_peaks.quantile(0.75)) if len(quiet_peaks) else 0.0
    quiet_localmax_p99 = float(quiet_peaks.quantile(0.99)) if len(quiet_peaks) else 0.0
    quiet_median = float(quiet_scans.median())
    quiet_mad_sigma = float(1.4826 * np.median(np.abs(quiet_scans - quiet_median)))

    body_candidate = np.nan
    positive_noise_candidate = np.nan
    peak_height_model = "default"
    if signal_col == "pc34_760_max_intensity":
        body_candidate = float(max(6.0 * quiet_scan_p90, 4.0 * quiet_localmax_p75))
        height = body_candidate
        peak_height_model = "background_body_multiplier"
        if quiet_mad_sigma > np.finfo(float).eps:
            positive_noise_candidate = float(quiet_scan_p99 + 3.0 * quiet_mad_sigma)
            if (
                np.isfinite(positive_noise_candidate)
                and positive_noise_candidate > 0
                and positive_noise_candidate < height
            ):
                height = positive_noise_candidate
                peak_height_model = "positive_background_tail_cap"
        prominence = float(max(0.8 * quiet_localmax_p75, 3.0 * quiet_mad_sigma))
    else:
        localmax_excess_p99 = max(0.0, quiet_localmax_p99 - quiet_median)
        height = float(max(quiet_scan_p99 + 3.0 * quiet_mad_sigma, quiet_localmax_p99))
        prominence = float(max(0.25 * localmax_excess_p99, 3.0 * quiet_mad_sigma, 0.02))

    signal = scan[signal_col].to_numpy(dtype=float)
    times_sec = scan["scan_start_time_sec"].to_numpy(dtype=float)
    signal_max = float(np.max(signal))
    fallback_reason = ""
    sparse_high_contrast_trace = 2 <= len(quiet_peaks) <= 5
    if (
        signal_col == "pc34_760_max_intensity"
        and signal_max > 0
        and (not np.isfinite(height) or height >= signal_max)
        and sparse_high_contrast_trace
    ):
        height = float(max(np.nextafter(0.0, 1.0), PC34_FALLBACK_HEIGHT_FRACTION * signal_max))
        prominence = float(
            max(
                np.nextafter(0.0, 1.0),
                min(prominence, PC34_FALLBACK_PROMINENCE_FRACTION * height),
            )
        )
        fallback_reason = "quiet_threshold_exceeded_signal_range"
        peak_height_model = "sparse_high_contrast_range_fallback"

    preliminary_distance = 2 if signal_col == "pc34_760_max_intensity" else 3
    preliminary, _ = find_peaks(
        signal,
        height=height,
        prominence=prominence,
        distance=preliminary_distance,
    )
    if len(preliminary) >= 3:
        gap_q10 = float(np.quantile(np.diff(times_sec[preliminary]), 0.10))
        min_distance_sec = (
            float(2.0 * dt_sec)
            if signal_col == "pc34_760_max_intensity"
            else float(np.clip(gap_q10, 6.0 * dt_sec, 15.0 * dt_sec))
        )
    else:
        gap_q10 = np.nan
        min_distance_sec = float((2.0 if signal_col == "pc34_760_max_intensity" else 6.0) * dt_sec)

    parameters: dict[str, Any] = {
        "signal_col": signal_col,
        "quiet_selection_method": quiet_method,
        "quiet_start_min": float(quiet_bins["start_min"].min()),
        "quiet_end_min": float(quiet_bins["end_min"].max()),
        "quiet_bin_count": int(len(quiet_bins)),
        "quiet_scan_p90": quiet_scan_p90,
        "quiet_scan_p99": quiet_scan_p99,
        "quiet_localmax_p75": quiet_localmax_p75,
        "quiet_localmax_p99": quiet_localmax_p99,
        "quiet_median": quiet_median,
        "quiet_mad_sigma": quiet_mad_sigma,
        "peak_height": height,
        "peak_height_model": peak_height_model,
        "peak_height_body_candidate": body_candidate,
        "peak_height_positive_noise_candidate": positive_noise_candidate,
        "peak_prominence": prominence,
        "threshold_fallback_reason": fallback_reason,
        "signal_max": signal_max,
        "preliminary_peak_count": int(len(preliminary)),
        "preliminary_gap_q10_sec": gap_q10,
        "min_distance_sec": min_distance_sec,
        "scan_step_sec": float(dt_sec),
        "bin_size_min": BIN_SIZE_MIN,
    }
    return parameters, quiet_bins


def call_peak_indices(scan: pd.DataFrame, parameters: dict[str, Any]) -> np.ndarray:
    signal_col = str(parameters["signal_col"])
    _validate_scan(scan, signal_col)
    dt_sec = float(parameters["scan_step_sec"])
    distance_points = max(1, int(round(float(parameters["min_distance_sec"]) / dt_sec)))
    peaks, _ = find_peaks(
        scan[signal_col].to_numpy(dtype=float),
        height=float(parameters["peak_height"]),
        prominence=float(parameters["peak_prominence"]),
        distance=distance_points,
    )
    return peaks.astype(np.int64)


def _as_float(row: pd.Series, name: str, default: float = np.nan) -> float:
    value = row[name] if name in row.index else default
    return float(value) if pd.notna(value) else float(default)


def _as_int(row: pd.Series, name: str, default: int = 0) -> int:
    value = row[name] if name in row.index else default
    return int(value) if pd.notna(value) else int(default)


def _local_scan_interval_sec(times_sec: np.ndarray, peak_index: int) -> float:
    start = max(0, int(peak_index) - 10)
    end = min(len(times_sec), int(peak_index) + 11)
    differences = np.diff(times_sec[start:end])
    positive = differences[np.isfinite(differences) & (differences > 0)]
    if not len(positive):
        differences = np.diff(times_sec)
        positive = differences[np.isfinite(differences) & (differences > 0)]
    if not len(positive):
        raise ValueError("cannot estimate local scan interval for event evidence")
    return float(np.median(positive))


def build_event_table(
    scan: pd.DataFrame,
    peaks: np.ndarray,
    parameters: dict[str, Any],
    *,
    generation_id: str,
    parameter_hash: str,
    source_sha256: str,
) -> pd.DataFrame:
    signal_col = str(parameters["signal_col"])
    _validate_scan(scan, signal_col)
    signal = scan[signal_col].to_numpy(dtype=float)
    times_sec = scan["scan_start_time_sec"].to_numpy(dtype=float)
    times_ns = scan["scan_time_ns"].to_numpy(dtype=np.int64)
    peak_array = np.asarray(peaks, dtype=np.int64)
    if len(peak_array):
        if np.any(peak_array < 0) or np.any(peak_array >= len(scan)):
            raise ValueError("peak index is outside scan table")
        prominences = peak_prominences(signal, peak_array)[0]
        widths = peak_widths(signal, peak_array, rel_height=0.5)
        left_ips = widths[2]
        right_ips = widths[3]
    else:
        prominences = left_ips = right_ips = np.asarray([], dtype=float)

    interpolation_axis = np.arange(len(scan), dtype=float)
    rows: list[dict[str, Any]] = []
    for position, peak_index in enumerate(peak_array):
        left_sec = float(np.interp(left_ips[position], interpolation_axis, times_sec))
        right_sec = float(np.interp(right_ips[position], interpolation_axis, times_sec))
        left_ns = int(np.rint(np.interp(left_ips[position], interpolation_axis, times_ns.astype(float))))
        right_ns = int(np.rint(np.interp(right_ips[position], interpolation_axis, times_ns.astype(float))))
        left_index = max(0, int(np.floor(left_ips[position])))
        right_index = min(len(scan) - 1, int(np.ceil(right_ips[position])))
        window = scan.iloc[left_index : right_index + 1]
        apex = scan.iloc[int(peak_index)]
        identity = auto_event_id(
            generation_id=generation_id,
            scan_id=str(apex["scan_id"]),
            spectrum_index=int(apex["spectrum_index"]),
            scan_row_index=int(apex["scan_row_index"]),
            scan_time_ns=int(apex["scan_time_ns"]),
        )
        low_array = (
            bool((window["array_length"] < LOW_ARRAY_LENGTH_THRESHOLD).any())
            if "array_length" in window
            else False
        )
        severe_array = (
            bool((window["array_length"] < LOW_ARRAY_LENGTH_SEVERE).any())
            if "array_length" in window
            else False
        )
        low_tic = bool((window["tic"] < LOW_TIC_THRESHOLD).any()) if "tic" in window else False
        rows.append(
            {
                "auto_event_id": identity,
                "generation_id": generation_id,
                "source_sha256": source_sha256,
                "detector_version": DETECTOR_VERSION,
                "parameter_hash": parameter_hash,
                "event_strategy": "pc34_primary",
                "primary_signal_col": signal_col,
                "scan_row_index": int(apex["scan_row_index"]),
                "spectrum_index": int(apex["spectrum_index"]),
                "scan_id": str(apex["scan_id"]),
                "scan_time_ns": int(apex["scan_time_ns"]),
                "apex_time_min": float(apex["scan_start_time_min"]),
                "apex_time_sec": float(apex["scan_start_time_sec"]),
                "apex_intensity": float(apex[signal_col]),
                "peak_prominence": float(prominences[position]),
                "peak_width_sec": right_sec - left_sec,
                "left_time_ns": left_ns,
                "right_time_ns": right_ns,
                "left_sec": left_sec,
                "right_sec": right_sec,
                "local_scan_interval_sec": _local_scan_interval_sec(
                    times_sec, int(peak_index)
                ),
                "window_scan_count": int(len(window)),
                "pc34_760_apex": _as_float(apex, "pc34_760_max_intensity", 0.0),
                "qc_782_apex": _as_float(apex, "qc_782_max_intensity", 0.0),
                "pc34_760_ppm_error_at_apex": _as_float(apex, "pc34_760_ppm_error_at_max_intensity"),
                "qc_782_ppm_error_at_apex": _as_float(apex, "qc_782_ppm_error_at_max_intensity"),
                "tic_apex": _as_float(apex, "tic", 0.0),
                "ratio_760_782_max_pseudo1": _as_float(apex, "ratio_760_782_max_pseudo1", 0.0),
                "array_length_apex": _as_int(apex, "array_length", 0),
                "base_peak_mz_apex": _as_float(apex, "base_peak_mz"),
                "low_array_length_lt_6000_window": low_array,
                "low_array_length_lt_1000_window": severe_array,
                "low_tic_lt_1e6_window": low_tic,
                "calling_height": float(parameters["peak_height"]),
                "calling_prominence": float(parameters["peak_prominence"]),
                "calling_min_distance_sec": float(parameters["min_distance_sec"]),
                "prev_event_gap_sec": np.nan,
                "next_event_gap_sec": np.nan,
                "nearest_event_gap_sec": np.nan,
                "collision_risk_high": False,
                "broad_peak_width_gt_1p5_sec": (right_sec - left_sec) > BROAD_PEAK_WIDTH_SEC,
                "low_quality_scan_window": False,
            }
        )
    if not rows:
        return _typed_events()

    for index, row in enumerate(rows):
        if index:
            row["prev_event_gap_sec"] = row["apex_time_sec"] - rows[index - 1]["apex_time_sec"]
        if index + 1 < len(rows):
            row["next_event_gap_sec"] = rows[index + 1]["apex_time_sec"] - row["apex_time_sec"]
        finite_gaps = [
            gap
            for gap in (row["prev_event_gap_sec"], row["next_event_gap_sec"])
            if np.isfinite(gap)
        ]
        row["nearest_event_gap_sec"] = min(finite_gaps) if finite_gaps else np.nan
        row["collision_risk_high"] = bool(
            finite_gaps and row["nearest_event_gap_sec"] < COLLISION_GAP_SEC
        )
        row["low_quality_scan_window"] = bool(
            row["low_array_length_lt_6000_window"]
            or row["low_array_length_lt_1000_window"]
            or row["low_tic_lt_1e6_window"]
            or row["broad_peak_width_gt_1p5_sec"]
        )
    return _typed_events(rows)


def detect_events(
    scan: pd.DataFrame,
    source_sha256: str,
    analysis_range: AnalysisRange,
) -> DetectionResult:
    signal_col = "pc34_760_max_intensity"
    _validate_scan(scan, signal_col)
    if len(source_sha256) != 64 or any(character not in "0123456789abcdef" for character in source_sha256.lower()):
        raise ValueError("source_sha256 must contain 64 hexadecimal characters")
    dt_sec = _median_scan_step_sec(scan)
    bins, localmax = build_bin_summary(scan, signal_col, dt_sec)
    parameters, quiet_bins = estimate_parameters(scan, signal_col, bins, localmax, dt_sec)
    parameter_hash = content_sha256(
        {
            "detector_version": DETECTOR_VERSION,
            "parameters": parameters,
            "constants": {
                "bin_size_min": BIN_SIZE_MIN,
                "collision_gap_sec": COLLISION_GAP_SEC,
                "broad_peak_width_sec": BROAD_PEAK_WIDTH_SEC,
                "fallback_height_fraction": PC34_FALLBACK_HEIGHT_FRACTION,
                "fallback_prominence_fraction": PC34_FALLBACK_PROMINENCE_FRACTION,
            },
        }
    )
    generation = make_generation_id(
        source_sha256=source_sha256.lower(),
        parser_version=PARSER_VERSION,
        detector_version=DETECTOR_VERSION,
        parameter_hash=parameter_hash,
        analysis_range=analysis_range,
        boundary_rule=BOUNDARY_RULE,
    )
    peaks = call_peak_indices(scan, parameters)
    full_events = build_event_table(
        scan,
        peaks,
        parameters,
        generation_id=generation,
        parameter_hash=parameter_hash,
        source_sha256=source_sha256.lower(),
    )
    if full_events.empty:
        events = full_events
    else:
        owned = (
            (full_events["scan_time_ns"] >= analysis_range.start_ns)
            & (full_events["scan_time_ns"] <= analysis_range.end_ns)
        )
        events = full_events.loc[owned].reset_index(drop=True)
        events = _typed_events(events.to_dict(orient="records"))
    parameters = dict(parameters)
    parameters.update(
        {
            "parser_version": PARSER_VERSION,
            "detector_version": DETECTOR_VERSION,
            "parameter_hash": parameter_hash,
            "generation_id": generation,
            "analysis_start_ns": analysis_range.start_ns,
            "analysis_end_ns": analysis_range.end_ns,
            "boundary_rule": BOUNDARY_RULE,
            "detection_scope": "full_trace_then_closed_apex_crop",
        }
    )
    return DetectionResult(
        events=events,
        parameters=parameters,
        bin_summary=bins,
        quiet_bins=quiet_bins,
        generation_id=generation,
        parameter_hash=parameter_hash,
    )
