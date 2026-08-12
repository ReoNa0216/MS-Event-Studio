from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from _fixtures import detector_scan
from ms_event_studio.display import (
    DisplayPyramid,
    WindowRequest,
    choose_event_labels,
    min_max_envelope,
)


class DisplayPyramidContractTest(unittest.TestCase):
    def test_min_max_envelope_keeps_narrow_extrema_and_time_order(self):
        signal = np.full(64, 10.0)
        signal[17] = 5000.0
        signal[18] = 0.0
        scans = detector_scan(signal)
        envelope = min_max_envelope(scans, bucket_size=16)
        self.assertIn(17, envelope["scan_row_index"].astype(int).tolist())
        self.assertIn(18, envelope["scan_row_index"].astype(int).tolist())
        self.assertTrue(envelope["scan_time_ns"].astype("int64").is_monotonic_increasing)
        self.assertFalse(envelope["scan_row_index"].duplicated().any())

    def test_window_level_reduces_trace_but_never_drops_event_overlay(self):
        signal = np.zeros(8192)
        signal[[100, 4000, 8000]] = [100.0, 500.0, 300.0]
        scans = detector_scan(signal)
        events = [
            {
                "event_id": "EV_mid",
                "current_apex_time_ns": int(scans.iloc[4000]["scan_time_ns"]),
                "current_apex_time_sec": float(scans.iloc[4000]["scan_start_time_sec"]),
                "status": "accepted",
                "origin": "automatic",
            }
        ]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            pyramid = DisplayPyramid.build(
                scans,
                Path(tmp) / "display_pyramids",
                source_binding="a" * 64,
            )
            request = WindowRequest(
                start_ns=0,
                end_ns=int(scans.iloc[-1]["scan_time_ns"]),
                point_budget=300,
                margin_fraction=0.0,
            )
            window = pyramid.read_window(request, events)
            self.assertLessEqual(len(window.trace), 600)
            self.assertGreater(window.bucket_size, 1)
            self.assertEqual([row["event_id"] for row in window.events], ["EV_mid"])
            self.assertIn(4000, window.trace["scan_row_index"].astype(int).tolist())

    def test_corrupt_cache_is_rebuilt_and_staging_is_cleaned(self):
        scans = detector_scan(np.arange(128, dtype=float))
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cache = Path(tmp) / "display_pyramids"
            first = DisplayPyramid.build(scans, cache, source_binding="b" * 64)
            (cache / "manifest.json").write_text("not-json", encoding="utf-8")
            rebuilt = DisplayPyramid.open_or_build(
                scans,
                cache,
                source_binding="b" * 64,
            )
            self.assertEqual(first.row_count, rebuilt.row_count)
            manifest = json.loads((cache / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["source_binding"], "b" * 64)
            self.assertEqual(list(Path(tmp).glob(".display_pyramids.building-*")), [])

    def test_window_request_and_labels_are_deterministic(self):
        with self.assertRaises(ValueError):
            WindowRequest(start_ns=10, end_ns=9)
        events = [
            {
                "event_id": f"EV_{index:03d}",
                "current_apex_time_ns": index * 10,
                "status": "accepted",
                "origin": "automatic",
            }
            for index in range(100)
        ]
        first = choose_event_labels(events, maximum_labels=8, selected_event_id="EV_055")
        second = choose_event_labels(list(reversed(events)), maximum_labels=8, selected_event_id="EV_055")
        self.assertEqual(first, second)
        self.assertIn("EV_055", first)
        self.assertLessEqual(len(first), 8)


if __name__ == "__main__":
    unittest.main()
