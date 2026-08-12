"""Repeatable Phase 2 interaction benchmark using an ignored real parse cache."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from ms_event_studio.canonical import json_value
from ms_event_studio.detector import DETECTOR_VERSION, detect_events
from ms_event_studio.display import DisplayPyramid, WindowRequest, choose_event_labels
from ms_event_studio.export import export_human_csv, export_machine_contract
from ms_event_studio.review import ReviewStore
from ms_event_studio.timebase import AnalysisRange, NANOSECONDS_PER_MINUTE


TARGET_P95_MS = 250.0


def _measure(function: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    result = function()
    return result, (time.perf_counter() - started) * 1_000


def _p95(values: list[float]) -> float:
    return float(np.percentile(np.asarray(values, dtype=float), 95))


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.writing-{os.getpid()}")
    temporary.write_text(
        json.dumps(json_value(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def run(cache_root: Path) -> dict[str, Any]:
    scan_path = cache_root / "Lin-_LSK.scan.parquet"
    metadata_path = cache_root / "Lin-_LSK.parse.json"
    if not scan_path.is_file() or not metadata_path.is_file():
        raise FileNotFoundError(
            "Phase 1 Lin-_LSK parse cache is required; run scripts/run_real_regression.py first"
        )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    scans, scan_load_ms = _measure(lambda: pd.read_parquet(scan_path))
    analysis = AnalysisRange(
        int(scans["scan_time_ns"].iloc[0]),
        int(scans["scan_time_ns"].iloc[-1]),
    )
    detection, detector_ms = _measure(
        lambda: detect_events(scans, metadata["fingerprint"]["sha256"], analysis)
    )

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        pyramid, pyramid_build_ms = _measure(
            lambda: DisplayPyramid.build(
                scans,
                root / "display_pyramids",
                source_binding=metadata["fingerprint"]["sha256"],
            )
        )
        store, review_create_ms = _measure(
            lambda: ReviewStore.create(
                root / "review.sqlite",
                project_id="phase2-benchmark",
                generation_id=detection.generation_id,
                automatic_events=detection.events.to_dict(orient="records"),
            )
        )
        states = store.list_events()
        extent_start = analysis.start_ns
        extent_end = analysis.end_ns
        one_minute_timings: list[float] = []
        ten_minute_timings: list[float] = []
        for width_ns, timings in (
            (NANOSECONDS_PER_MINUTE, one_minute_timings),
            (10 * NANOSECONDS_PER_MINUTE, ten_minute_timings),
        ):
            maximum_start = max(extent_start, extent_end - width_ns)
            starts = np.linspace(extent_start, maximum_start, num=100, dtype=np.int64)
            for start in starts:
                request = WindowRequest(
                    start_ns=int(start),
                    end_ns=min(extent_end, int(start) + width_ns),
                    point_budget=1_800,
                )

                def window_read():
                    review_snapshot = store.list_events()
                    window = pyramid.read_window(request, review_snapshot)
                    choose_event_labels(window.events, maximum_labels=24)
                    return window

                _, elapsed = _measure(window_read)
                timings.append(elapsed)

        review_timings: list[float] = []
        for row in states[:100]:
            _, elapsed = _measure(
                lambda row=row: store.set_status(
                    row["event_id"],
                    "accepted",
                    expected_revision=int(row["revision"]),
                    actor="benchmark",
                    session_id="phase2-performance",
                )
            )
            review_timings.append(elapsed)
        reviewed = store.list_events()
        human_result, human_export_ms = _measure(
            lambda: export_human_csv(
                reviewed,
                root / "accepted.csv",
                analysis_start_ns=analysis.start_ns,
                analysis_end_ns=analysis.end_ns,
            )
        )
        machine_result, machine_export_ms = _measure(
            lambda: export_machine_contract(
                reviewed,
                detection.events,
                root / "machine",
                source_fingerprint=metadata["fingerprint"],
                detector_version=DETECTOR_VERSION,
                parameter_hash=detection.parameter_hash,
                generation_id=detection.generation_id,
                analysis_start_ns=analysis.start_ns,
                analysis_end_ns=analysis.end_ns,
            )
        )
        store.close()

    interaction_gates = {
        "window_1min_p95_under_250ms": _p95(one_minute_timings) < TARGET_P95_MS,
        "window_10min_p95_under_250ms": _p95(ten_minute_timings) < TARGET_P95_MS,
        "review_p95_under_250ms": _p95(review_timings) < TARGET_P95_MS,
    }
    return {
        "schema": "ms-event-studio-phase2-performance-v1",
        "dataset": "Lin-_LSK real parse cache",
        "source_size_bytes": metadata["fingerprint"]["size_bytes"],
        "source_sha256": metadata["fingerprint"]["sha256"],
        "full_parse_elapsed_sec_from_phase1": metadata["summary"]["elapsed_sec"],
        "scan_rows": len(scans),
        "automatic_events": len(detection.events),
        "measurements_ms": {
            "scan_parquet_load": scan_load_ms,
            "detector_full_trace": detector_ms,
            "display_pyramid_initial_build": pyramid_build_ms,
            "review_database_initial_create": review_create_ms,
            "window_1min_p50": float(np.percentile(one_minute_timings, 50)),
            "window_1min_p95": _p95(one_minute_timings),
            "window_10min_p50": float(np.percentile(ten_minute_timings, 50)),
            "window_10min_p95": _p95(ten_minute_timings),
            "review_single_first": review_timings[0],
            "review_100_p50": float(np.percentile(review_timings, 50)),
            "review_100_p95": _p95(review_timings),
            "review_100_total": float(sum(review_timings)),
            "human_export": human_export_ms,
            "machine_export": machine_export_ms,
        },
        "export_rows": {
            "human": human_result.row_count,
            "machine": machine_result.row_count,
        },
        "target_interaction_p95_ms": TARGET_P95_MS,
        "gates": interaction_gates,
        "passed": all(interaction_gates.values()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    repository = Path(__file__).resolve().parents[1]
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=repository / "tests/real_output/cache",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repository / "tests/real_output/phase2_performance.json",
    )
    args = parser.parse_args(argv)
    report = run(args.cache_root.resolve())
    _atomic_json(args.output.resolve(), report)
    print(json.dumps(json_value(report), ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
