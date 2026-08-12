from __future__ import annotations

import unittest

import numpy as np

from _fixtures import detector_scan
from ms_event_studio.detector import (
    EVENT_COLUMNS,
    build_bin_summary,
    build_event_table,
    call_peak_indices,
    detect_events,
    estimate_parameters,
)
from ms_event_studio.timebase import AnalysisRange


class DetectorContractTest(unittest.TestCase):
    def test_seeded_weak_peak_recall_and_false_discovery_gate(self):
        rng = np.random.default_rng(123)
        signal = np.clip(rng.normal(100.0, 10.0, 12_000), 0.0, None)
        weak_4sigma, weak_6sigma, weak_8sigma = 2_000, 4_000, 6_000
        signal[[weak_4sigma, weak_6sigma, weak_8sigma]] = [140.0, 160.0, 180.0]
        result = detect_events(
            detector_scan(signal),
            source_sha256="e" * 64,
            analysis_range=AnalysisRange.from_minutes("0", "20"),
        )
        called = result.events["scan_row_index"].astype(int).tolist()
        self.assertEqual(called, [weak_6sigma, weak_8sigma])
        self.assertEqual(len(set(called) - {weak_4sigma, weak_6sigma, weak_8sigma}), 0)

    def test_adjacent_events_are_retained_and_flagged_as_collision(self):
        signal = np.zeros(2_401)
        truth = [600, 603]
        signal[truth] = [1_000.0, 1_200.0]
        result = detect_events(
            detector_scan(signal),
            source_sha256="f" * 64,
            analysis_range=AnalysisRange.from_minutes("0", "4"),
        )
        self.assertEqual(result.events["scan_row_index"].astype(int).tolist(), truth)
        self.assertEqual(result.events["collision_risk_high"].tolist(), [True, True])

    def test_wide_peak_is_quality_evidence_not_identity_suppression(self):
        signal = np.zeros(101)
        signal[30:71] = np.concatenate(
            [np.linspace(0.0, 1_000.0, 21), np.linspace(950.0, 0.0, 20)]
        )
        scan = detector_scan(signal, dt_sec=0.1)
        params = {
            "signal_col": "pc34_760_max_intensity",
            "scan_step_sec": 0.1,
            "peak_height": 100.0,
            "peak_prominence": 100.0,
            "min_distance_sec": 0.2,
        }
        events = build_event_table(
            scan,
            np.asarray([50]),
            params,
            generation_id="GEN_" + "1" * 64,
            parameter_hash="2" * 64,
            source_sha256="3" * 64,
        )
        self.assertEqual(len(events), 1)
        self.assertGreater(float(events.iloc[0]["peak_width_sec"]), 1.5)
        self.assertTrue(bool(events.iloc[0]["broad_peak_width_gt_1p5_sec"]))
        self.assertTrue(bool(events.iloc[0]["low_quality_scan_window"]))

    def test_repeated_detection_is_byte_identity_deterministic_in_logical_projection(self):
        signal = np.zeros(2_401)
        signal[[500, 1_000, 1_500]] = [900.0, 1_100.0, 1_000.0]
        scan = detector_scan(signal)
        range_ = AnalysisRange.from_minutes("0", "4")
        first = detect_events(scan, "9" * 64, range_)
        second = detect_events(scan.copy(), "9" * 64, range_)
        self.assertEqual(first.parameter_hash, second.parameter_hash)
        self.assertEqual(first.generation_id, second.generation_id)
        self.assertEqual(
            first.events.to_json(orient="table", index=False),
            second.events.to_json(orient="table", index=False),
        )

    def test_empty_event_result_has_strong_complete_schema(self):
        scan = detector_scan(np.zeros(1201))
        result = detect_events(
            scan,
            source_sha256="a" * 64,
            analysis_range=AnalysisRange.from_minutes("0", "2"),
        )
        self.assertEqual(len(result.events), 0)
        self.assertEqual(result.events.columns.tolist(), list(EVENT_COLUMNS))
        self.assertEqual(str(result.events["scan_row_index"].dtype), "Int64")
        self.assertEqual(str(result.events["collision_risk_high"].dtype), "boolean")

    def test_exact_two_minute_endpoint_is_accounted_once(self):
        scan = detector_scan(np.zeros(1201), dt_sec=0.1)
        bins, _ = build_bin_summary(scan, "pc34_760_max_intensity", 0.1)
        self.assertEqual(int(bins["scan_count"].sum()), len(scan))

    def test_zero_inflated_fallback_matches_v044_golden(self):
        signal = np.zeros(600, dtype=float)
        peaks = np.asarray([100, 300, 500])
        signal[peaks] = [1000.0, 2000.0, 3000.0]
        scan = detector_scan(signal)
        localmax = scan.iloc[peaks][["scan_row_index", "scan_start_time_min"]].copy()
        localmax.columns = ["scan_row_index", "time_min"]
        localmax["height"] = signal[peaks]
        bins = __import__("pandas").DataFrame(
            [
                {
                    "bin_index": 0,
                    "start_min": 0.0,
                    "end_min": 1.0,
                    "scan_count": 600,
                    "localmax_p99": 2980.0,
                    "scan_p99": 2500.0,
                    "positive_scan_fraction": 0.005,
                }
            ]
        )
        params, _ = estimate_parameters(
            scan, "pc34_760_max_intensity", bins, localmax, 0.1
        )
        called = call_peak_indices(scan, params)
        self.assertEqual(called.tolist(), peaks.tolist())
        self.assertEqual(params["peak_height"], 300.0)
        self.assertEqual(params["peak_prominence"], 30.0)
        self.assertEqual(params["peak_height_model"], "sparse_high_contrast_range_fallback")

    def test_event_free_positive_noise_has_no_false_calls(self):
        rng = np.random.default_rng(760)
        scan = detector_scan(rng.uniform(0.0, 250.0, 12_000))
        result = detect_events(
            scan,
            source_sha256="b" * 64,
            analysis_range=AnalysisRange.from_minutes("0", "20"),
        )
        self.assertEqual(len(result.events), 0)
        self.assertEqual(result.parameters["threshold_fallback_reason"], "")

    def test_positive_background_tail_cap_preserves_all_truth(self):
        rng = np.random.default_rng(20260812)
        signal = np.clip(rng.normal(100.0, 100.0, 12_000), 0.0, 500.0)
        truth = np.arange(500, 11_500, 1000)
        signal[truth] = 1000.0
        result = detect_events(
            detector_scan(signal),
            source_sha256="c" * 64,
            analysis_range=AnalysisRange.from_minutes("0", "20"),
        )
        self.assertEqual(result.events["scan_row_index"].astype(int).tolist(), truth.tolist())
        self.assertEqual(result.parameters["peak_height_model"], "positive_background_tail_cap")

    def test_irregular_time_width_is_physical_support_width(self):
        scan = detector_scan(np.asarray([0.0, 1.0, 4.0, 1.0, 0.0]))
        scan["scan_start_time_sec"] = [0.0, 0.1, 0.2, 1.0, 1.1]
        scan["scan_start_time_min"] = scan["scan_start_time_sec"] / 60.0
        scan["scan_time_ns"] = np.rint(
            scan["scan_start_time_sec"] * 1_000_000_000
        ).astype("int64")
        params = {
            "signal_col": "pc34_760_max_intensity",
            "scan_step_sec": 0.1,
            "peak_height": 1.0,
            "peak_prominence": 1.0,
            "min_distance_sec": 0.2,
        }
        events = build_event_table(
            scan,
            np.asarray([2]),
            params,
            generation_id="GEN_" + "1" * 64,
            parameter_hash="2" * 64,
            source_sha256="3" * 64,
        )
        row = events.iloc[0]
        self.assertAlmostEqual(
            float(row["peak_width_sec"]),
            float(row["right_sec"] - row["left_sec"]),
            places=12,
        )

    def test_range_is_closed_and_labels_cannot_change_detection(self):
        signal = np.zeros(2401)
        # Keep two local maxima in the selected quiet bin so this deliberately
        # sparse synthetic trace exercises the frozen v0.4.4 range fallback.
        # The 30 s peak is also an explicit out-of-range exclusion control.
        signal[[300, 600, 1200]] = [100.0, 1000.0, 1200.0]
        scan = detector_scan(signal)
        range_ = AnalysisRange.from_minutes("1", "2")
        first = detect_events(scan, "d" * 64, range_)
        contaminated = scan.copy()
        contaminated["Type"] = ["secret"] * len(scan)
        contaminated["CellNumber"] = np.arange(len(scan))
        second = detect_events(contaminated, "d" * 64, range_)
        self.assertEqual(first.events["scan_time_ns"].tolist(), [60_000_000_000, 120_000_000_000])
        self.assertEqual(first.events["auto_event_id"].tolist(), second.events["auto_event_id"].tolist())


if __name__ == "__main__":
    unittest.main()
