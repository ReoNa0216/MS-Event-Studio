from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from _fixtures import spectrum_lines, write_ms_file
from ms_event_studio.cli import main
from ms_event_studio.export import export_machine_contract
from ms_event_studio.paths import resolve_project_path
from ms_event_studio.project import CreateProjectRequest, create_project
from ms_event_studio.review import ReviewStore


class MachineContractTest(unittest.TestCase):
    def test_contract_retains_rejected_and_immutable_automatic_evidence(self):
        automatic = [
            {
                "auto_event_id": "AE_" + "1" * 64,
                "generation_id": "GEN_" + "2" * 64,
                "source_sha256": "3" * 64,
                "detector_version": "detector-v1",
                "parameter_hash": "4" * 64,
                "scan_id": "7",
                "scan_row_index": 6,
                "spectrum_index": 5,
                "scan_time_ns": 60_000_000_000,
                "apex_time_sec": 60.0,
                "apex_intensity": 1000.0,
                "left_sec": 59.9,
                "right_sec": 60.1,
                "peak_width_sec": 0.2,
            }
        ]
        reviews = [
            {
                "event_id": "EV_a",
                "auto_event_id": automatic[0]["auto_event_id"],
                "original_auto_event_id": automatic[0]["auto_event_id"],
                "generation_id": automatic[0]["generation_id"],
                "original_left_sec": 59.9,
                "original_right_sec": 60.1,
                "current_scan_id": "7",
                "current_scan_row_index": 6,
                "current_spectrum_index": 5,
                "current_apex_time_ns": 60_000_000_000,
                "current_apex_time_sec": 60.0,
                "current_apex_intensity": 1000.0,
                "status": "rejected",
                "origin": "automatic",
                "revision": 1,
                "snap_offset_sec": 0.0,
            }
        ]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            output = Path(tmp) / "machine"
            result = export_machine_contract(
                reviews,
                automatic,
                output,
                source_fingerprint={"sha256": "3" * 64, "size_bytes": 123},
                detector_version="detector-v1",
                parameter_hash="4" * 64,
                generation_id="GEN_" + "2" * 64,
                analysis_start_ns=0,
                analysis_end_ns=120_000_000_000,
            )
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            table = pd.read_parquet(result.event_table_path)
            digest = hashlib.sha256(result.event_table_path.read_bytes()).hexdigest()
            checksum_lines = result.checksum_path.read_text(encoding="ascii").splitlines()
            serialized = json.dumps(manifest)
        self.assertEqual(len(table), 1)
        self.assertEqual(table.iloc[0]["status"], "rejected")
        self.assertEqual(table.iloc[0]["left_sec"], 59.9)
        self.assertEqual(manifest["event_table"]["sha256"], digest)
        self.assertEqual(manifest["status_counts"], {"rejected": 1})
        self.assertEqual(
            checksum_lines,
            [
                f"{result.event_table_sha256}  events.parquet",
                f"{result.manifest_sha256}  manifest.json",
            ],
        )
        self.assertNotIn(str(output.resolve()), serialized)


class CLIContractTest(unittest.TestCase):
    def test_verify_and_human_export_append_audit(self):
        signal = np.zeros(1201)
        signal[[300, 600, 900]] = [1000.0, 1500.0, 1200.0]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source = write_ms_file(
                root / "source.txt",
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
            project = create_project(
                CreateProjectRequest(
                    source_path=source,
                    project_dir=root / "project",
                    display_name="CLI project",
                    analysis_start_min="0",
                    analysis_end_min="2",
                )
            )
            review_path = resolve_project_path(project.project_dir, project.manifest["review"]["path"])
            store = ReviewStore.open(review_path, project_id=project.manifest["project_id"])
            first = store.list_events()[0]
            store.set_status(
                first["event_id"],
                "accepted",
                expected_revision=0,
                actor="tester",
                session_id="setup",
            )
            store.close()

            output = root / "accepted.csv"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "export",
                        "--project",
                        str(project.project_dir),
                        "--output",
                        str(output),
                        "--actor",
                        "tester",
                        "--session",
                        "cli-test",
                    ]
                )
            self.assertEqual(code, 0)
            with output.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)

            store = ReviewStore.open(review_path, project_id=project.manifest["project_id"])
            self.assertEqual(store.audit_events()[-1]["action"], "export")
            store.close()

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["verify", "--project", str(project.project_dir)])
            self.assertEqual(code, 0)
            verified = json.loads(stdout.getvalue())
            self.assertEqual(verified["project_id"], project.manifest["project_id"])


if __name__ == "__main__":
    unittest.main()
