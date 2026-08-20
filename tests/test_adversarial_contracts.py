from __future__ import annotations

import sqlite3
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from _fixtures import PRIMARY_MARKER_MZ, detector_scan, spectrum_lines, write_ms_file
from ms_event_studio.errors import (
    CancelledError,
    ExistingEventNavigation,
    MSParseError,
    ProjectValidationError,
    SnapError,
)
from ms_event_studio.parser import parse_ms_scan_summary
from ms_event_studio.project import CreateProjectRequest, create_project, open_project
from ms_event_studio.review import ReviewStore


def small_project_source(path: Path) -> Path:
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


class StrictParserAdversarialTest(unittest.TestCase):
    def test_conflicting_duplicate_field_inside_spectrum_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_ms_file(Path(tmp) / "duplicate-field.txt", [spectrum_lines(0, 1, 0)])
            text = path.read_text(encoding="ascii").replace(
                "  id: scanId=1\n",
                "  id: scanId=1\n  id: scanId=999\n",
            )
            path.write_text(text, encoding="ascii")
            with self.assertRaisesRegex(MSParseError, "duplicate field.*scan_id"):
                parse_ms_scan_summary(path)

    def test_negative_mz_fails_even_when_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_ms_file(
                Path(tmp) / "negative-mz.txt",
                [
                    spectrum_lines(
                        0,
                        1,
                        0,
                        mz_values=[-1.0, PRIMARY_MARKER_MZ, 900.0],
                        intensities=[0.0, 10.0, 0.0],
                    )
                ],
            )
            with self.assertRaisesRegex(MSParseError, "negative m/z"):
                parse_ms_scan_summary(path)


class ReviewAdversarialTest(unittest.TestCase):
    def test_equal_distance_peaks_are_ambiguous(self):
        signal = np.zeros(20)
        signal[[9, 11]] = [500.0, 900.0]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = ReviewStore.create(
                Path(tmp) / "review.sqlite",
                project_id="p",
                generation_id="GEN_" + "1" * 64,
                automatic_events=[],
            )
            with self.assertRaisesRegex(SnapError, "ambiguous"):
                store.add_event(
                    click_time_sec=1.0,
                    scans=detector_scan(signal),
                    analysis_start_ns=0,
                    analysis_end_ns=2_000_000_000,
                    actor="tester",
                    session_id="s1",
                )
            store.close()

    def test_add_inside_existing_automatic_support_returns_navigation(self):
        signal = np.zeros(20)
        signal[3] = 1_000.0
        automatic = [
            {
                "auto_event_id": "AE_" + "1" * 64,
                "generation_id": "GEN_" + "1" * 64,
                "scan_id": "100003",
                "scan_row_index": 3,
                "spectrum_index": 3,
                "scan_time_ns": 300_000_000,
                "apex_time_sec": 0.3,
                "left_time_ns": 200_000_000,
                "right_time_ns": 400_000_000,
                "left_sec": 0.2,
                "right_sec": 0.4,
                "local_scan_interval_sec": 0.1,
                "apex_intensity": 1_000.0,
            }
        ]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = ReviewStore.create(
                Path(tmp) / "review.sqlite",
                project_id="p",
                generation_id="GEN_" + "1" * 64,
                automatic_events=automatic,
            )
            expected = store.list_events()[0]["event_id"]
            with self.assertRaises(ExistingEventNavigation) as caught:
                store.add_event(
                    click_time_sec=0.31,
                    scans=detector_scan(signal),
                    analysis_start_ns=0,
                    analysis_end_ns=2_000_000_000,
                    actor="tester",
                    session_id="s1",
                )
            self.assertEqual(caught.exception.event_id, expected)
            self.assertEqual(len(store.list_events()), 1)
            store.close()
    def test_plateau_uses_lower_midpoint_and_duplicate_scan_is_rejected(self):
        signal = np.zeros(20)
        signal[[9, 10]] = 500.0
        scans = detector_scan(signal)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = ReviewStore.create(
                Path(tmp) / "review.sqlite",
                project_id="p",
                generation_id="GEN_" + "1" * 64,
                automatic_events=[],
            )
            first = store.add_event(
                click_time_sec=0.95,
                scans=scans,
                analysis_start_ns=0,
                analysis_end_ns=2_000_000_000,
                actor="tester",
                session_id="s1",
            )
            self.assertEqual(first["current_scan_row_index"], 9)
            with self.assertRaises(ExistingEventNavigation):
                store.add_event(
                    click_time_sec=0.94,
                    scans=scans,
                    analysis_start_ns=0,
                    analysis_end_ns=2_000_000_000,
                    actor="tester",
                    session_id="s1",
                )
            store.close()

    def test_click_inside_large_gap_cannot_snap_across_it(self):
        signal = np.asarray([0.0, 0.0, 0.0, 10.0, 0.0, 0.0])
        scans = detector_scan(signal)
        scans["scan_start_time_sec"] = [0.0, 0.1, 0.2, 1.0, 1.1, 1.2]
        scans["scan_start_time_min"] = scans["scan_start_time_sec"] / 60.0
        scans["scan_time_ns"] = np.rint(
            scans["scan_start_time_sec"] * 1_000_000_000
        ).astype("int64")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = ReviewStore.create(
                Path(tmp) / "review.sqlite",
                project_id="p",
                generation_id="GEN_" + "1" * 64,
                automatic_events=[],
            )
            with self.assertRaisesRegex(SnapError, "gap"):
                store.add_event(
                    click_time_sec=0.85,
                    scans=scans,
                    analysis_start_ns=0,
                    analysis_end_ns=2_000_000_000,
                    actor="tester",
                    session_id="s1",
                )
            store.close()

    def test_append_only_audit_rejects_update_too(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = Path(tmp) / "review.sqlite"
            store = ReviewStore.create(
                db,
                project_id="p",
                generation_id="GEN_" + "1" * 64,
                automatic_events=[],
            )
            signal = np.zeros(20)
            signal[10] = 50.0
            store.add_event(
                click_time_sec=1.0,
                scans=detector_scan(signal),
                analysis_start_ns=0,
                analysis_end_ns=2_000_000_000,
                actor="tester",
                session_id="s1",
            )
            store.close()
            connection = sqlite3.connect(db)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE audit_events SET reason = 'rewritten'")
            connection.close()


class ProjectAdversarialTest(unittest.TestCase):
    def test_cancellation_cleans_only_staging_and_preserves_empty_target(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source = small_project_source(root / "source.txt")
            target = root / "project"
            target.mkdir()
            with self.assertRaises(CancelledError):
                create_project(
                    CreateProjectRequest(
                        source_path=source,
                        project_dir=target,
                        display_name="Cancelled",
                        analysis_start_min="0",
                        analysis_end_min="2",
                        cancel_check=lambda: True,
                    )
                )
            self.assertEqual(list(target.iterdir()), [])
            self.assertEqual(list(root.glob(".*.ms-event-building-*")), [])

    def test_immutable_artifact_tamper_fails_preflight(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            project = create_project(
                CreateProjectRequest(
                    source_path=small_project_source(root / "source.txt"),
                    project_dir=root / "project",
                    display_name="Tamper",
                    analysis_start_min="0",
                    analysis_end_min="2",
                )
            )
            protocol = project.project_dir / "provenance/detector_protocol.json"
            protocol.write_text(protocol.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaisesRegex(ProjectValidationError, "(size|SHA-256) mismatch"):
                open_project(project.project_dir)

    def test_manifest_cannot_drop_required_artifact_records(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            project = create_project(
                CreateProjectRequest(
                    source_path=small_project_source(root / "source.txt"),
                    project_dir=root / "project",
                    display_name="Incomplete manifest",
                    analysis_start_min="0",
                    analysis_end_min="2",
                )
            )
            manifest_path = project.project_dir / "ms_event_project.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"] = [
                row for row in manifest["artifacts"] if row["role"] != "detector_protocol"
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ProjectValidationError, "required artifact role"):
                open_project(project.project_dir)

    def test_manifest_source_binding_must_match_verified_input_manifest(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            project = create_project(
                CreateProjectRequest(
                    source_path=small_project_source(root / "source.txt"),
                    project_dir=root / "project",
                    display_name="Cross binding",
                    analysis_start_min="0",
                    analysis_end_min="2",
                )
            )
            manifest_path = project.project_dir / "ms_event_project.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["source"]["source_sha256"] = "f" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ProjectValidationError, "source fingerprint binding"):
                open_project(project.project_dir)

    def test_manifest_cannot_mark_scientific_artifact_mutable(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            project = create_project(
                CreateProjectRequest(
                    source_path=small_project_source(root / "source.txt"),
                    project_dir=root / "project",
                    display_name="Mutable bypass",
                    analysis_start_min="0",
                    analysis_end_min="2",
                )
            )
            manifest_path = project.project_dir / "ms_event_project.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"][0]["mutable"] = True
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ProjectValidationError, "cannot be mutable"):
                open_project(project.project_dir)

    def test_malformed_numeric_manifest_field_is_a_typed_validation_failure(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            project = create_project(
                CreateProjectRequest(
                    source_path=small_project_source(root / "source.txt"),
                    project_dir=root / "project",
                    display_name="Malformed numeric field",
                    analysis_start_min="0",
                    analysis_end_min="2",
                )
            )
            manifest_path = project.project_dir / "ms_event_project.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"][0]["size_bytes_at_creation"] = None
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ProjectValidationError, "artifact size_bytes_at_creation"):
                open_project(project.project_dir)


if __name__ == "__main__":
    unittest.main()
