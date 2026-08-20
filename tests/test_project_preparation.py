from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from _fixtures import spectrum_lines, write_ms_file
from ms_event_studio.errors import InputChangedError
from ms_event_studio.project import (
    CreateProjectRequest,
    create_project,
    inspect_project_source,
)
from ms_event_studio.scientific_settings import ProjectScientificSettings


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


class PreparedProjectSourceContractTest(unittest.TestCase):
    def test_inspect_then_create_reuses_the_single_parse(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source = make_source(root / "source.txt")
            prepared = inspect_project_source(source)
            self.assertEqual(prepared.start_ns, 0)
            self.assertEqual(prepared.end_ns, 120_000_000_000)
            with patch(
                "ms_event_studio.project.parse_ms_scan_summary",
                side_effect=AssertionError("source was parsed twice"),
            ):
                project = create_project(
                    CreateProjectRequest(
                        source_path=source,
                        project_dir=root / "project",
                        display_name="Prepared",
                        analysis_start_min="0",
                        analysis_end_min="2",
                    ),
                    prepared_source=prepared,
                )
            self.assertTrue((project.project_dir / "cache").is_dir())
            self.assertEqual(
                project.manifest["scientific_settings"],
                {
                    "primary_marker_mz": 760.5851,
                    "marker_tolerance_ppm": 12.0,
                    "collision_gap_sec": 0.60,
                },
            )

    def test_prepared_source_cannot_be_reused_with_a_different_marker(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source = make_source(root / "source.txt")
            prepared = inspect_project_source(source)
            target = root / "project"
            with self.assertRaisesRegex(ValueError, "different primary marker"):
                create_project(
                    CreateProjectRequest(
                        source_path=source,
                        project_dir=target,
                        display_name="Wrong marker",
                        analysis_start_min="0",
                        analysis_end_min="2",
                        scientific_settings=ProjectScientificSettings(
                            primary_marker_mz=500.1234
                        ),
                    ),
                    prepared_source=prepared,
                )
            self.assertFalse(target.exists())

    def test_change_after_inspection_fails_without_publishing(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source = make_source(root / "source.txt")
            prepared = inspect_project_source(source)
            source.write_text(source.read_text(encoding="ascii") + "\n", encoding="ascii")
            target = root / "project"
            with self.assertRaises(InputChangedError):
                create_project(
                    CreateProjectRequest(
                        source_path=source,
                        project_dir=target,
                        display_name="Changed",
                        analysis_start_min="0",
                        analysis_end_min="2",
                    ),
                    prepared_source=prepared,
                )
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
