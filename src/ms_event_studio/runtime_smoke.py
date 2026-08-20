"""Runtime probe shared by source and frozen WebView candidates.

The probe deliberately exercises binary-backed scientific dependencies without
opening a user project or touching a raw MS source file.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any


def packaged_scientific_smoke() -> dict[str, Any]:
    """Run the Phase 2 scientific/Parquet/SQLite/export packaged round trip."""

    # Keep binary scientific imports out of ordinary desktop module import so
    # the welcome screen is not delayed by a smoke-only dependency probe.
    import numpy as np
    import pandas as pd

    from .detector import detect_events
    from .display import DisplayPyramid, WindowRequest
    from .export import export_human_csv, export_machine_contract
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
            "primary_marker_max_intensity": signal,
            "qc_marker_max_intensity": np.full(count, 10.0),
            "tic": np.full(count, 1e7),
            "array_length": np.full(count, 7000, dtype=np.int64),
            "base_peak_mz": np.full(count, 760.5851),
            "primary_marker_ppm_error_at_max_intensity": np.zeros(count),
            "qc_marker_ppm_error_at_max_intensity": np.zeros(count),
            "primary_qc_max_ratio_pseudo1": (signal + 1.0) / 11.0,
        }
    )
    analysis = AnalysisRange(0, 120_000_000_000)
    detected = detect_events(scans, "f" * 64, analysis)
    if len(detected.events) != 3:
        raise RuntimeError(
            f"packaged detector smoke expected 3 events, found {len(detected.events)}"
        )

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        scan_path = root / "scans.parquet"
        scans.to_parquet(scan_path, index=False)
        round_trip = pd.read_parquet(scan_path)
        pyramid = DisplayPyramid.build(
            round_trip,
            root / "display",
            source_binding="f" * 64,
        )
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
        try:
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
        finally:
            store.close()

    return {
        "scan_rows": len(round_trip),
        "event_rows": len(detected.events),
        "display_points": len(window.trace),
        "accepted_event_id_prefix": str(accepted["event_id"])[:3],
        "human_rows": human.row_count,
        "machine_rows": machine.row_count,
    }


# Transitional name retained for callers of the scientific regression probe.
# The WebView entry imports this module directly and never pulls in another UI.
_packaged_scientific_smoke = packaged_scientific_smoke
