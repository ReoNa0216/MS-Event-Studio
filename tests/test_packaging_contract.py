from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from desktop_bundle.build_desktop import build_arguments, locate_executable
from ms_event_studio import __version__
from ms_event_studio.desktop import _packaged_scientific_smoke
from ms_event_studio.theme import enable_high_dpi


class PackagingContractTest(unittest.TestCase):
    def test_packaged_scientific_runtime_smoke_exercises_core_stack(self):
        result = _packaged_scientific_smoke()
        self.assertEqual(result["scan_rows"], 1201)
        self.assertEqual(result["event_rows"], 3)
        self.assertEqual(result["human_rows"], 1)
        self.assertEqual(result["machine_rows"], 3)
        self.assertGreater(result["display_points"], 0)
        self.assertEqual(__version__, "0.2.0.dev3")

    def test_native_ui_enables_dpi_awareness_without_dotnet_config(self):
        mode = enable_high_dpi()
        self.assertIn(
            mode,
            {"per-monitor-v2", "per-monitor", "system", "platform-native"},
        )
        repository = Path(__file__).resolve().parents[1]
        self.assertFalse((repository / "desktop_bundle/MS-Event-Studio.exe.config").exists())
        notes = (repository / "desktop_bundle/README.md").read_text(encoding="utf-8")
        self.assertIn("Per-Monitor V2", notes)
        self.assertIn("pywebview/pythonnet/CLR", notes)

    def test_build_is_native_windowed_onedir_and_source_root_is_explicit(self):
        repository = Path(__file__).resolve().parents[1]
        arguments = build_arguments(repository, platform_name="windows")
        self.assertIn("--windowed", arguments)
        self.assertIn("--onedir", arguments)
        self.assertIn("--clean", arguments)
        self.assertIn(str(repository / "src"), arguments)
        self.assertIn(str(repository / "desktop_bundle/ms_event_studio_gui.py"), arguments)
        self.assertEqual(
            arguments[arguments.index("--distpath") + 1],
            str(repository / "dist/windows"),
        )
        self.assertIn("--icon", arguments)
        self.assertIn(str(repository / "build/icons/MS-Event-Studio.ico"), arguments)
        self.assertIn("--add-data", arguments)
        self.assertIn(
            f"{repository / 'src/ms_event_studio/assets'}{os.pathsep}ms_event_studio/assets",
            arguments,
        )

    def test_macos_build_is_arm64_app_with_bundle_identity_and_icon(self):
        repository = Path(__file__).resolve().parents[1]
        arguments = build_arguments(repository, platform_name="macos")
        self.assertIn("--target-architecture", arguments)
        self.assertIn("arm64", arguments)
        self.assertIn("--osx-bundle-identifier", arguments)
        self.assertIn("org.hulab.ms-event-studio", arguments)
        self.assertIn(str(repository / "build/icons/MS-Event-Studio.icns"), arguments)

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

    def test_custom_candidate_root_must_remain_inside_repository(self):
        repository = Path(__file__).resolve().parents[1]
        custom = repository / "dist/windows-side-by-side"
        arguments = build_arguments(
            repository,
            platform_name="windows",
            dist_root=custom,
        )
        self.assertEqual(arguments[arguments.index("--distpath") + 1], str(custom))
        with self.assertRaisesRegex(ValueError, "escapes repository"):
            build_arguments(
                repository,
                platform_name="windows",
                dist_root=repository.parent,
            )


if __name__ == "__main__":
    unittest.main()
