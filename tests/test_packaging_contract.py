from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from desktop_bundle.build_desktop import build_arguments, locate_executable
from ms_event_studio import __version__
from ms_event_studio.desktop import _packaged_scientific_smoke


class PackagingContractTest(unittest.TestCase):
    def test_packaged_scientific_runtime_smoke_exercises_core_stack(self):
        result = _packaged_scientific_smoke()
        self.assertEqual(result["scan_rows"], 1201)
        self.assertEqual(result["event_rows"], 3)
        self.assertEqual(result["human_rows"], 1)
        self.assertEqual(result["machine_rows"], 3)
        self.assertGreater(result["display_points"], 0)
        self.assertEqual(__version__, "0.2.0.dev0")

    def test_build_is_native_windowed_onedir_and_source_root_is_explicit(self):
        repository = Path(__file__).resolve().parents[1]
        arguments = build_arguments(repository, platform_name="windows")
        self.assertIn("--windowed", arguments)
        self.assertIn("--onedir", arguments)
        self.assertIn("--clean", arguments)
        self.assertIn(str(repository / "src"), arguments)
        self.assertIn(str(repository / "desktop_bundle/ms_event_studio_gui.py"), arguments)

    def test_platform_executable_locations_are_not_cross_compiled(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.assertEqual(
                locate_executable(root, "windows"),
                root / "MS-Event-Studio/MS-Event-Studio.exe",
            )
            self.assertEqual(
                locate_executable(root, "macos"),
                root / "MS-Event-Studio.app/Contents/MacOS/MS-Event-Studio",
            )
            with self.assertRaises(ValueError):
                build_arguments(Path(tmp), platform_name="linux")


if __name__ == "__main__":
    unittest.main()
