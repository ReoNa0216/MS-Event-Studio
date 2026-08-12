from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from _fixtures import spectrum_lines, write_ms_file
from ms_event_studio.errors import MSParseError
from ms_event_studio.project import CreateProjectRequest, create_project, open_project


class ProjectAtomicityTest(unittest.TestCase):
    def make_source(self, path: Path) -> Path:
        signal = np.zeros(1201)
        signal[[300, 600, 900]] = [1000.0, 1500.0, 1200.0]
        spectra = [
            spectrum_lines(
                index,
                index + 1,
                f"{index / 600.0:.12f}",
                intensities=[0.0, float(signal[index]), 10.0, 0.0],
            )
            for index in range(len(signal))
        ]
        return write_ms_file(path, spectra)

    def test_create_publishes_complete_portable_project(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source = self.make_source(root / "source.txt")
            source_before = hashlib.sha256(source.read_bytes()).hexdigest()
            target = root / "project"
            created = create_project(
                CreateProjectRequest(
                    source_path=source,
                    project_dir=target,
                    display_name="Synthetic project",
                    analysis_start_min="0.05",
                    analysis_end_min="0.15",
                )
            )
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_before)
            for relative in (
                "ms_event_project.json",
                "README.md",
                "data/ms_scan_summary.parquet",
                "data/automatic_events.parquet",
                "annotations/review.sqlite",
                "provenance/input_manifest.json",
                "provenance/detector_protocol.json",
                "provenance/processing.log",
            ):
                self.assertTrue((target / relative).exists(), relative)
            self.assertTrue((target / "annotations/exports").is_dir())
            self.assertEqual(created.project_dir, target.resolve())
            reopened = open_project(target)
            self.assertEqual(reopened.manifest["project_id"], created.manifest["project_id"])
            serialized = json.dumps(reopened.manifest)
            self.assertNotIn(str(source.resolve()), serialized)

    def test_parse_failure_never_publishes_and_restores_empty_target(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source = write_ms_file(
                root / "bad.txt",
                [spectrum_lines(0, 1, 0)],
                declared_count=2,
            )
            target = root / "project"
            target.mkdir()
            with self.assertRaises(MSParseError):
                create_project(
                    CreateProjectRequest(
                        source_path=source,
                        project_dir=target,
                        display_name="Bad project",
                        analysis_start_min="0",
                        analysis_end_min="1",
                    )
                )
            self.assertTrue(target.is_dir())
            self.assertEqual(list(target.iterdir()), [])
            self.assertEqual(list(root.glob(".*.ms-event-building-*")), [])


if __name__ == "__main__":
    unittest.main()
