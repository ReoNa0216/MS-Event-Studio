#!/usr/bin/env python3
"""Read-only Phase 1 regression against the four frozen real MS assets.

This script performs the expensive single-pass parse and full SHA-256, then
compares the new result with the existing LMA scan/event parquet tables without
writing into any source or user-project directory.  Only an irreversible JSON
summary is written under this repository's ignored ``tests/real_output`` path.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ms_event_studio import __version__
from ms_event_studio.canonical import canonical_json, json_value
from ms_event_studio.detector import DETECTOR_VERSION, detect_events
from ms_event_studio.parser import (
    PARSER_VERSION,
    ParseProgress,
    ParseResult,
    ParseSummary,
    SourceFingerprint,
    parse_ms_scan_summary,
)
from ms_event_studio.timebase import AnalysisRange


PROJECTS = ("Lin-_LSK", "Lin-_MPP", "Lin-_CLP", "Lin-_LK")
EDGE_BYTES = 1_048_576
EXPECTED_V044_EVENTS = {
    "Lin-_LSK": 1807,
    "Lin-_MPP": 1414,
    "Lin-_CLP": 1818,
    "Lin-_LK": 1056,
}
PPM_12_TRANSITION_NS = int(
    datetime.fromisoformat("2026-08-11T16:55:03+08:00").timestamp() * 1_000_000_000
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _edge_hashes(path: Path) -> tuple[str, str]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        head = handle.read(EDGE_BYTES)
        if size > EDGE_BYTES:
            handle.seek(size - EDGE_BYTES)
            tail = handle.read(EDGE_BYTES)
        else:
            tail = head
    return hashlib.sha256(head).hexdigest(), hashlib.sha256(tail).hexdigest()


def _tree_snapshot(root: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        stat = path.stat()
        records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    return {
        "file_count": len(records),
        "snapshot_sha256": hashlib.sha256(canonical_json(records).encode("utf-8")).hexdigest(),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _progress(project: str):
    last_reported = -1

    def callback(progress: ParseProgress) -> None:
        nonlocal last_reported
        percent = int(progress.fraction * 100)
        if progress.phase == "complete" or percent >= last_reported + 5:
            last_reported = percent
            print(
                json.dumps(
                    {
                        "project": project,
                        "phase": progress.phase,
                        "percent": percent,
                        "bytes_read": progress.bytes_read,
                        "total_bytes": progress.total_bytes,
                        "parsed_spectra": progress.parsed_spectra,
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
                flush=True,
            )

    return callback


def _manifest_paths(workspace: Path, project_name: str) -> tuple[dict, Path, Path, Path]:
    project_dir = workspace / project_name
    manifest = json.loads((project_dir / "lifms_project.json").read_text(encoding="utf-8"))
    raw = Path(manifest["raw_inputs"]["ms"]["path"]).resolve()
    scan = (project_dir / manifest["intermediate_tables"]["ms_scan_summary"]["path"]).resolve()
    events = (project_dir / manifest["intermediate_tables"]["ms_events"]["path"]).resolve()
    return manifest, raw, scan, events


def _cache_paths(output_path: Path, project_name: str) -> tuple[Path, Path]:
    root = output_path.parent / "cache"
    return root / f"{project_name}.scan.parquet", root / f"{project_name}.parse.json"


def _load_cached_parse(
    source: Path,
    output_path: Path,
    project_name: str,
) -> ParseResult | None:
    scan_path, metadata_path = _cache_paths(output_path, project_name)
    if not scan_path.is_file() or not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        stat = source.stat()
        if (
            metadata["parser_version"] != PARSER_VERSION
            or int(metadata["source_size_bytes"]) != stat.st_size
            or int(metadata["source_mtime_ns"]) != stat.st_mtime_ns
            or _sha256(scan_path) != metadata["scan_parquet_sha256"]
        ):
            return None
        scans = pd.read_parquet(scan_path)
        return ParseResult(
            scans=scans,
            fingerprint=SourceFingerprint(**metadata["fingerprint"]),
            summary=ParseSummary(**metadata["summary"]),
        )
    except (KeyError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _store_cached_parse(
    source: Path,
    output_path: Path,
    project_name: str,
    parsed: ParseResult,
) -> None:
    scan_path, metadata_path = _cache_paths(output_path, project_name)
    scan_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_scan = scan_path.with_name(f".{scan_path.name}.writing-{os.getpid()}")
    parsed.scans.to_parquet(temporary_scan, index=False)
    os.replace(temporary_scan, scan_path)
    stat = source.stat()
    metadata = {
        "schema": "ms-event-real-parse-cache-v1",
        "parser_version": PARSER_VERSION,
        "source_size_bytes": stat.st_size,
        "source_mtime_ns": stat.st_mtime_ns,
        "fingerprint": asdict(parsed.fingerprint),
        "summary": asdict(parsed.summary),
        "scan_parquet_sha256": _sha256(scan_path),
    }
    temporary_metadata = metadata_path.with_name(
        f".{metadata_path.name}.writing-{os.getpid()}"
    )
    temporary_metadata.write_text(
        json.dumps(json_value(metadata), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary_metadata, metadata_path)


def _parse_with_cache(
    source: Path,
    output_path: Path,
    project_name: str,
) -> tuple[ParseResult, bool, float]:
    cached = _load_cached_parse(source, output_path, project_name)
    if cached is not None:
        return cached, True, 0.0
    started = time.monotonic()
    parsed = parse_ms_scan_summary(
        source,
        progress_callback=_progress(project_name),
        progress_interval_bytes=256 * 1024 * 1024,
    )
    elapsed = time.monotonic() - started
    _store_cached_parse(source, output_path, project_name, parsed)
    return parsed, False, elapsed


def _prepare_existing_scan(frame: pd.DataFrame) -> pd.DataFrame:
    scan = frame.copy()
    if "scan_time_ns" not in scan:
        scan["scan_time_ns"] = np.rint(
            scan["scan_start_time_sec"].to_numpy(dtype=float) * 1_000_000_000
        ).astype("int64")
    scan["scan_id"] = scan["scan_id"].astype(str)
    return scan


def _scan_projection_comparison(parsed: pd.DataFrame, existing: pd.DataFrame) -> dict[str, Any]:
    parsed_by_id = parsed.assign(scan_id=parsed["scan_id"].astype(str)).set_index("scan_id")
    existing_by_id = existing.assign(scan_id=existing["scan_id"].astype(str)).set_index("scan_id")
    missing = existing_by_id.index.difference(parsed_by_id.index)
    added = parsed_by_id.index.difference(existing_by_id.index)
    shared = existing_by_id.index.intersection(parsed_by_id.index, sort=False)
    exact_columns = [
        name
        for name in (
            "spectrum_index",
            "array_length",
            "pc34_760_n_mz",
            "qc_782_n_mz",
        )
        if name in existing_by_id and name in parsed_by_id
    ]
    numeric_columns = [
        name
        for name in (
            "scan_start_time_min",
            "scan_start_time_sec",
            "base_peak_mz",
            "base_peak_intensity",
            "tic",
            "pc34_760_max_intensity",
            "pc34_760_sum_intensity",
            "pc34_760_ppm_error_at_max_intensity",
            "qc_782_max_intensity",
            "qc_782_sum_intensity",
            "qc_782_ppm_error_at_max_intensity",
        )
        if name in existing_by_id and name in parsed_by_id
    ]
    exact_mismatches: dict[str, int] = {}
    for column in exact_columns:
        left = existing_by_id.loc[shared, column].to_numpy()
        right = parsed_by_id.loc[shared, column].to_numpy()
        exact_mismatches[column] = int(np.count_nonzero(left != right))
    maximum_absolute_delta: dict[str, float | None] = {}
    for column in numeric_columns:
        left = existing_by_id.loc[shared, column].to_numpy(dtype=float)
        right = parsed_by_id.loc[shared, column].to_numpy(dtype=float)
        finite = np.isfinite(left) & np.isfinite(right)
        both_nan = np.isnan(left) & np.isnan(right)
        incompatible = ~(finite | both_nan)
        if np.any(incompatible):
            maximum_absolute_delta[column] = None
        elif np.any(finite):
            maximum_absolute_delta[column] = float(np.max(np.abs(left[finite] - right[finite])))
        else:
            maximum_absolute_delta[column] = 0.0
    return {
        "shared_scan_ids": int(len(shared)),
        "existing_scan_ids_missing_from_new": int(len(missing)),
        "new_scan_ids_not_in_existing": int(len(added)),
        "new_only_scan_ids_sha256": hashlib.sha256(
            "\n".join(sorted(map(str, added))).encode("utf-8")
        ).hexdigest(),
        "exact_mismatch_counts": exact_mismatches,
        "maximum_absolute_delta": maximum_absolute_delta,
    }


def _projection_matches_with_tolerance(comparison: dict[str, Any]) -> bool:
    if any(value != 0 for value in comparison["exact_mismatch_counts"].values()):
        return False
    for column, value in comparison["maximum_absolute_delta"].items():
        if value is None:
            return False
        tolerance = 1e-9 if column.startswith("scan_start_time") else 1e-10
        if float(value) > tolerance:
            return False
    return True


def _physical_event_keys(events: pd.DataFrame) -> list[tuple[str, int]]:
    return sorted(
        zip(
            events["scan_id"].astype(str),
            events["scan_time_ns"].astype("int64").astype(int),
        )
    )


def _event_comparison(
    parsed_events: pd.DataFrame,
    reference_events: pd.DataFrame,
    legacy_events: pd.DataFrame,
    one_scan_tolerance_sec: float,
) -> dict[str, Any]:
    parsed_keys = _physical_event_keys(parsed_events)
    reference_keys = _physical_event_keys(reference_events)
    parsed_set = set(parsed_keys)
    reference_set = set(reference_keys)
    legacy_scan_ids = set(legacy_events["scan_id"].astype(str))
    parsed_scan_ids = set(parsed_events["scan_id"].astype(str))
    legacy_time_column = "time_sec" if "time_sec" in legacy_events else "apex_time_sec"
    parsed_times = parsed_events["apex_time_sec"].astype(float).to_numpy()
    parsed_ids = parsed_events["scan_id"].astype(str).to_numpy()
    nearest_deltas: list[float] = []
    moved_within_one_scan: list[dict[str, Any]] = []
    for _, legacy in legacy_events.iterrows():
        legacy_time = float(legacy[legacy_time_column])
        distances = np.abs(parsed_times - legacy_time)
        nearest_index = int(np.argmin(distances))
        delta = float(distances[nearest_index])
        nearest_deltas.append(delta)
        legacy_scan_id = str(legacy["scan_id"])
        if legacy_scan_id not in parsed_scan_ids and delta <= one_scan_tolerance_sec:
            moved_within_one_scan.append(
                {
                    "legacy_scan_id": legacy_scan_id,
                    "new_scan_id": str(parsed_ids[nearest_index]),
                    "legacy_time_sec": legacy_time,
                    "new_time_sec": float(parsed_times[nearest_index]),
                    "absolute_delta_sec": delta,
                }
            )
    payload = canonical_json(parsed_keys).encode("utf-8")
    return {
        "parsed_event_rows": len(parsed_events),
        "reference_event_rows_from_existing_scan": len(reference_events),
        "physical_event_vector_exact": parsed_keys == reference_keys,
        "missing_from_parsed": len(reference_set - parsed_set),
        "added_by_parsed": len(parsed_set - reference_set),
        "legacy_scan_id_recall": len(legacy_scan_ids & parsed_scan_ids),
        "legacy_scan_id_total": len(legacy_scan_ids),
        "legacy_recalled_within_one_scan": int(
            sum(delta <= one_scan_tolerance_sec for delta in nearest_deltas)
        ),
        "legacy_one_scan_tolerance_sec": float(one_scan_tolerance_sec),
        "legacy_max_nearest_apex_delta_sec": max(nearest_deltas, default=0.0),
        "legacy_moved_within_one_scan": moved_within_one_scan,
        "physical_event_set_sha256": hashlib.sha256(payload).hexdigest(),
    }


def _lma_reference_detection(workspace: Path, scan: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    lma_root = str((workspace / "lma-studio").resolve())
    if lma_root not in sys.path:
        sys.path.insert(0, lma_root)
    module = importlib.import_module("scripts.v3.run_v3_02_ms_event_calling")
    time_sec = scan["scan_start_time_sec"].to_numpy(dtype=float)
    dt_sec = float(np.median(np.diff(time_sec)))
    bins, localmax = module.build_bin_summary(scan, "pc34_760_max_intensity", dt_sec)
    parameters, _ = module.estimate_parameters(
        scan,
        "pc34_760_max_intensity",
        bins,
        localmax,
        dt_sec,
    )
    peaks = module.call_peak_indices(
        scan,
        "pc34_760_max_intensity",
        parameters["peak_height"],
        parameters["peak_prominence"],
        parameters["min_distance_sec"],
        dt_sec,
    )
    rows = scan.iloc[np.asarray(peaks, dtype=int)][
        ["scan_id", "scan_time_ns"]
    ].reset_index(drop=True)
    rows["scan_id"] = rows["scan_id"].astype(str)
    return rows, parameters


def run_one(workspace: Path, output_path: Path, project_name: str) -> dict[str, Any]:
    manifest, raw_path, scan_path, event_path = _manifest_paths(workspace, project_name)
    user_project_dir = workspace / project_name
    project_tree_before = _tree_snapshot(user_project_dir)
    raw_record = manifest["raw_inputs"]["ms"]
    stat_before = raw_path.stat()
    current_head_before, current_tail_before = _edge_hashes(raw_path)
    parsed, cache_used, parse_elapsed = _parse_with_cache(
        raw_path, output_path, project_name
    )
    stat_after = raw_path.stat()
    current_head_after, current_tail_after = _edge_hashes(raw_path)

    existing_scan = _prepare_existing_scan(pd.read_parquet(scan_path))
    legacy_events = pd.read_parquet(event_path)
    full_range = AnalysisRange(
        int(parsed.scans["scan_time_ns"].iloc[0]),
        int(parsed.scans["scan_time_ns"].iloc[-1]),
    )
    detected = detect_events(parsed.scans, parsed.fingerprint.sha256, full_range)
    existing_range = AnalysisRange(
        int(existing_scan["scan_time_ns"].iloc[0]),
        int(existing_scan["scan_time_ns"].iloc[-1]),
    )
    stale_intermediate_detection = detect_events(
        existing_scan, parsed.fingerprint.sha256, existing_range
    )
    lma_reference_events, lma_reference_parameters = _lma_reference_detection(
        workspace, parsed.scans
    )
    scan_comparison = _scan_projection_comparison(parsed.scans, existing_scan)
    event_comparison = _event_comparison(
        detected.events,
        lma_reference_events,
        legacy_events,
        one_scan_tolerance_sec=float(detected.parameters["scan_step_sec"]) + 1e-9,
    )

    manifest_fingerprint_matches = (
        int(raw_record["size_bytes"]) == parsed.fingerprint.size_bytes
        and raw_record["head_sha256_1mb"] == parsed.fingerprint.head_sha256
        and raw_record["tail_sha256_1mb"] == parsed.fingerprint.tail_sha256
    )
    source_unchanged = (
        stat_before.st_size == stat_after.st_size
        and stat_before.st_mtime_ns == stat_after.st_mtime_ns
        and current_head_before == current_head_after == parsed.fingerprint.head_sha256
        and current_tail_before == current_tail_after == parsed.fingerprint.tail_sha256
    )
    project_tree_after = _tree_snapshot(user_project_dir)
    project_tree_unchanged = project_tree_before == project_tree_after
    existing_scan_predates_12ppm = scan_path.stat().st_mtime_ns < PPM_12_TRANSITION_NS
    projection_exact = _projection_matches_with_tolerance(scan_comparison)
    detector_parameter_delta = {
        key: float(detected.parameters[key]) - float(lma_reference_parameters[key])
        for key in ("peak_height", "peak_prominence", "min_distance_sec")
    }
    gates = {
        "source_unchanged": source_unchanged,
        "user_project_tree_unchanged": project_tree_unchanged,
        "manifest_edge_fingerprint_matches": manifest_fingerprint_matches,
        "metadata_count_equals_parsed": (
            parsed.summary.metadata_spectrum_count == parsed.summary.parsed_spectrum_count
        ),
        "all_existing_scan_ids_retained": scan_comparison["existing_scan_ids_missing_from_new"] == 0,
        "existing_projection_exact_or_documented_pre_12ppm": (
            projection_exact or existing_scan_predates_12ppm
        ),
        "detector_matches_current_lma_source": event_comparison["physical_event_vector_exact"],
        "detector_parameters_match_current_lma_source": all(
            abs(value) <= 1e-10 for value in detector_parameter_delta.values()
        ),
        "legacy_events_recalled_within_one_scan": (
            event_comparison["legacy_recalled_within_one_scan"]
            == event_comparison["legacy_scan_id_total"]
        ),
    }
    return {
        "project": project_name,
        "source_file_name": raw_path.name,
        "source_size_bytes": parsed.fingerprint.size_bytes,
        "source_full_sha256": parsed.fingerprint.sha256,
        "source_head_sha256_1mb": parsed.fingerprint.head_sha256,
        "source_tail_sha256_1mb": parsed.fingerprint.tail_sha256,
        "source_stat_before": {
            "size_bytes": stat_before.st_size,
            "mtime_ns": stat_before.st_mtime_ns,
        },
        "source_stat_after": {
            "size_bytes": stat_after.st_size,
            "mtime_ns": stat_after.st_mtime_ns,
        },
        "user_project_tree_before": project_tree_before,
        "user_project_tree_after": project_tree_after,
        "parse_elapsed_sec": parse_elapsed,
        "parse_cache_used": cache_used,
        "parse_summary": asdict(parsed.summary),
        "existing_scan_rows": len(existing_scan),
        "new_scan_rows": len(parsed.scans),
        "scan_comparison": scan_comparison,
        "existing_scan_provenance": {
            "mtime_ns": scan_path.stat().st_mtime_ns,
            "predates_12ppm_transition": existing_scan_predates_12ppm,
            "projection_matches_current_12ppm_parser": projection_exact,
            "phase0_mixed_baseline_event_rows": EXPECTED_V044_EVENTS[project_name],
            "phase0_mixed_baseline_delta_vs_raw_reparse": (
                len(detected.events) - EXPECTED_V044_EVENTS[project_name]
            ),
            "detector_rows_when_reusing_existing_scan": len(stale_intermediate_detection.events),
        },
        "event_comparison": event_comparison,
        "detector_parameter_delta_vs_current_lma_source": detector_parameter_delta,
        "detector_parameters": {
            key: detected.parameters[key]
            for key in (
                "peak_height",
                "peak_prominence",
                "min_distance_sec",
                "peak_height_model",
                "threshold_fallback_reason",
                "scan_step_sec",
            )
        },
        "gates": gates,
        "passed": all(gates.values()),
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.writing-{os.getpid()}")
    temporary.write_text(
        json.dumps(json_value(report), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "tests/real_output/phase1_real_regression.json",
    )
    parser.add_argument("--project", action="append", choices=PROJECTS)
    args = parser.parse_args(argv)

    chosen = tuple(args.project or PROJECTS)
    report: dict[str, Any] = {
        "schema": "ms-event-studio-real-regression-v1",
        "started_at": _now(),
        "application_version": __version__,
        "parser_version": PARSER_VERSION,
        "detector_version": DETECTOR_VERSION,
        "projects": [],
    }
    exit_code = 0
    for project_name in chosen:
        print(f"Starting read-only full regression: {project_name}", file=sys.stderr, flush=True)
        try:
            result = run_one(args.workspace.resolve(), args.output.resolve(), project_name)
        except Exception as exc:  # preserve an auditable partial report
            result = {
                "project": project_name,
                "passed": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            exit_code = 1
        report["projects"].append(result)
        _write_report(args.output.resolve(), report)
        print(
            json.dumps({"project": project_name, "passed": result["passed"]}, sort_keys=True),
            file=sys.stderr,
            flush=True,
        )
        if not result["passed"]:
            exit_code = 1
    report["completed_at"] = _now()
    report["passed"] = exit_code == 0
    report["summary_sha256"] = hashlib.sha256(
        canonical_json(report["projects"]).encode("utf-8")
    ).hexdigest()
    _write_report(args.output.resolve(), report)
    print(json.dumps(json_value(report), ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
