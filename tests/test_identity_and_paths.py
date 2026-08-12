from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ms_event_studio.errors import PathSecurityError
from ms_event_studio.identity import auto_event_id, generation_id, new_event_id
from ms_event_studio.paths import resolve_project_path
from ms_event_studio.timebase import AnalysisRange, minutes_to_ns


class IdentityAndPathContractTest(unittest.TestCase):
    def test_fixed_point_closed_range(self):
        range_ = AnalysisRange.from_minutes("10", "60")
        self.assertTrue(range_.contains_ns(minutes_to_ns("10")))
        self.assertTrue(range_.contains_ns(minutes_to_ns("60")))
        self.assertFalse(range_.contains_ns(minutes_to_ns("9.999999999")))
        self.assertFalse(range_.contains_ns(minutes_to_ns("60.000000001")))

    def test_generation_and_auto_identity_are_deterministic_and_order_free(self):
        first_generation = generation_id(
            source_sha256="a" * 64,
            parser_version="parser-v1",
            detector_version="detector-v1",
            parameter_hash="b" * 64,
            analysis_range=AnalysisRange.from_minutes("10", "60"),
            boundary_rule="closed_current_apex_v1",
        )
        second_generation = generation_id(
            source_sha256="a" * 64,
            parser_version="parser-v1",
            detector_version="detector-v1",
            parameter_hash="b" * 64,
            analysis_range=AnalysisRange.from_minutes("10", "60"),
            boundary_rule="closed_current_apex_v1",
        )
        self.assertEqual(first_generation, second_generation)
        identity = auto_event_id(
            generation_id=first_generation,
            scan_id="1501139",
            spectrum_index=7,
            scan_row_index=9,
            scan_time_ns=1_501_131_000_000,
        )
        self.assertEqual(
            identity,
            auto_event_id(
                generation_id=first_generation,
                scan_id="1501139",
                spectrum_index=7,
                scan_row_index=9,
                scan_time_ns=1_501_131_000_000,
            ),
        )
        self.assertRegex(new_event_id(), r"^EV_[0-9a-f]{32}$")

    def test_manifest_paths_reject_cross_platform_escape_forms(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            safe = resolve_project_path(root, "data/events.parquet")
            self.assertEqual(safe, (root / "data/events.parquet").resolve())
            for unsafe in (
                "../outside",
                "/absolute/path",
                r"C:\absolute\path",
                r"C:drive-relative",
                r"data\..\outside",
                "data/file.txt:stream",
                r"\\server\share\file",
                "data/CON.txt",
                "data/NUL",
                "data/trailing. ",
            ):
                with self.subTest(unsafe=unsafe):
                    with self.assertRaises(PathSecurityError):
                        resolve_project_path(root, unsafe)

    def test_existing_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            link = root / "data"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation is not available")
            with self.assertRaises(PathSecurityError):
                resolve_project_path(root, "data/events.parquet")


if __name__ == "__main__":
    unittest.main()
