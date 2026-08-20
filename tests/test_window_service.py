from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from _fixtures import spectrum_lines, write_ms_file
from ms_event_studio.display import WindowRequest
from ms_event_studio.project import CreateProjectRequest, create_project
from ms_event_studio.window_service import ProjectWindowService


def make_source(path: Path) -> Path:
    signal = np.zeros(1201)
    signal[[300, 600, 900]] = [1000.0, 1500.0, 1200.0]
    return write_ms_file(
        path,
        [
            spectrum_lines(
                index,
                index + 1,
                f"{index / 600.0:.12f}",
                intensities=[0.0, float(signal[index]), 10.0, 0.0],
            )
            for index in range(len(signal))
        ],
    )


class WindowServiceContractTest(unittest.TestCase):
    def test_single_snapshot_window_contains_trace_events_and_selected_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            project = create_project(
                CreateProjectRequest(
                    source_path=make_source(root / "source.txt"),
                    project_dir=root / "project",
                    display_name="Window service",
                    analysis_start_min="0",
                    analysis_end_min="2",
                )
            )
            service = ProjectWindowService.open(project.project_dir)
            first_event = service.all_events()[0]
            snapshot = service.window(
                WindowRequest(start_ns=0, end_ns=120_000_000_000, point_budget=200),
                status_filter="all",
                selected_event_id=first_event["event_id"],
            )
            self.assertTrue(len(snapshot.trace))
            self.assertEqual(snapshot.sqlite_snapshot_count, 1)
            self.assertEqual(snapshot.selected_event["event_id"], first_event["event_id"])
            self.assertEqual(snapshot.selected_scan["scan_id"], first_event["current_scan_id"])
            self.assertIn("primary_marker_ppm_error_at_max_intensity", snapshot.selected_scan)
            self.assertTrue((project.project_dir / "cache/display_pyramids/manifest.json").is_file())
            service.close()

    def test_filter_does_not_mutate_database_and_reopen_recovers(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            project = create_project(
                CreateProjectRequest(
                    source_path=make_source(root / "source.txt"),
                    project_dir=root / "project",
                    display_name="Filter recovery",
                    analysis_start_min="0",
                    analysis_end_min="2",
                )
            )
            service = ProjectWindowService.open(project.project_dir)
            before = service.all_events()
            request = WindowRequest(start_ns=0, end_ns=120_000_000_000, point_budget=200)
            self.assertEqual(service.window(request, status_filter="accepted").events, ())
            service.close()
            reopened = ProjectWindowService.open(project.project_dir)
            self.assertEqual(reopened.all_events(), before)
            reopened.close()


if __name__ == "__main__":
    unittest.main()
