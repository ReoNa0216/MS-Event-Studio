from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from ms_event_studio.export import HUMAN_COLUMNS, export_human_csv


class ExportContractTest(unittest.TestCase):
    def setUp(self):
        self.events = [
            {
                "event_id": "EV_a",
                "current_scan_id": "1",
                "current_apex_time_ns": 60_000_000_000,
                "current_apex_time_sec": 60.0,
                "current_apex_intensity": 100.0,
                "status": "accepted",
                "origin": "automatic",
            },
            {
                "event_id": "EV_b",
                "current_scan_id": "2",
                "current_apex_time_ns": 90_000_000_000,
                "current_apex_time_sec": 90.0,
                "current_apex_intensity": 200.0,
                "status": "pending",
                "origin": "manual_adjusted",
            },
            {
                "event_id": "EV_c",
                "current_scan_id": "3",
                "current_apex_time_ns": 120_000_000_000,
                "current_apex_time_sec": 120.0,
                "current_apex_intensity": 300.0,
                "status": "rejected",
                "origin": "automatic",
            },
            {
                "event_id": "EV_d",
                "current_scan_id": "4",
                "current_apex_time_ns": 150_000_000_000,
                "current_apex_time_sec": 150.0,
                "current_apex_intensity": 400.0,
                "status": "unreviewed",
                "origin": "automatic",
            },
        ]

    def test_human_csv_defaults_to_accepted_and_has_six_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.csv"
            result = export_human_csv(
                self.events,
                path,
                analysis_start_ns=60_000_000_000,
                analysis_end_ns=150_000_000_000,
            )
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(result.row_count, 1)
        self.assertEqual(list(rows[0]), list(HUMAN_COLUMNS))
        self.assertEqual(rows[0]["EventID"], "EV_a")
        self.assertEqual(rows[0]["scan_start_time"], "1")

    def test_pending_requires_explicit_switch_and_range_is_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.csv"
            result = export_human_csv(
                self.events,
                path,
                analysis_start_ns=90_000_000_000,
                analysis_end_ns=90_000_000_000,
                include_pending=True,
            )
        self.assertEqual(result.row_count, 1)
        self.assertEqual(result.statuses, ("accepted", "pending"))

    def test_duplicate_event_id_is_rejected_instead_of_exported_twice(self):
        duplicate = [dict(self.events[0]), dict(self.events[0])]
        duplicate[1]["current_scan_id"] = "different-scan"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "duplicate EventID"):
                export_human_csv(
                    duplicate,
                    Path(tmp) / "events.csv",
                    analysis_start_ns=0,
                    analysis_end_ns=180_000_000_000,
                )


if __name__ == "__main__":
    unittest.main()
