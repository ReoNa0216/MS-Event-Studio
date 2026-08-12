from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from ms_event_studio.demo import (
    AUTOMATIC_PEAKS,
    MANUAL_ONLY_PEAKS,
    SCAN_COUNT,
    create_guided_source,
    create_guided_test_assets,
)
from ms_event_studio.detector import detect_events
from ms_event_studio.project import inspect_project_source
from ms_event_studio.timebase import AnalysisRange, NANOSECONDS_PER_MINUTE


REPOSITORY = Path(__file__).resolve().parents[1]


def png_size(path: Path) -> tuple[int, int]:
    payload = path.read_bytes()
    if payload[:8] != b"\x89PNG\r\n\x1a\n" or payload[12:16] != b"IHDR":
        raise AssertionError(f"not a valid PNG header: {path}")
    return struct.unpack(">II", payload[16:24])


class BrandGuidedAndActionsTest(unittest.TestCase):
    def test_packaged_runtime_icons_have_the_committed_size_set(self):
        assets = REPOSITORY / "src/ms_event_studio/assets"
        master = assets / "app_icon_master.png"
        width, height = png_size(master)
        self.assertEqual(width, height)
        self.assertGreaterEqual(width, 1024)
        for size in (32, 64, 128, 256):
            self.assertEqual(png_size(assets / f"app_icon_{size}.png"), (size, size))

    def test_guided_source_is_unique_parseable_and_detects_known_apexes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            first = create_guided_test_assets(root)
            second = create_guided_test_assets(root)
            self.assertNotEqual(first.source_path, second.source_path)
            self.assertFalse(first.project_path.exists())

            prepared = inspect_project_source(first.source_path)
            self.assertEqual(len(prepared.parsed.scans), SCAN_COUNT)
            self.assertEqual(prepared.start_ns, 0)
            self.assertEqual(prepared.end_ns, 2 * NANOSECONDS_PER_MINUTE)
            detection = detect_events(
                prepared.parsed.scans,
                prepared.parsed.fingerprint.sha256,
                AnalysisRange.from_minutes(0, 2),
            )
            apexes = detection.events["spectrum_index"].astype(int).tolist()
            self.assertEqual(apexes, sorted(AUTOMATIC_PEAKS))
            weak_index = next(iter(MANUAL_ONLY_PEAKS))
            self.assertNotIn(weak_index, apexes)
            self.assertEqual(
                float(prepared.parsed.scans.iloc[weak_index]["pc34_760_max_intensity"]),
                MANUAL_ONLY_PEAKS[weak_index],
            )

    def test_guided_source_refuses_overwrite(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            source = Path(tmp) / "guided.txt"
            create_guided_source(source)
            with self.assertRaises(FileExistsError):
                create_guided_source(source)

    def test_github_actions_matches_native_desktop_contract(self):
        workflow = (REPOSITORY / ".github/workflows/release-desktop.yml").read_text(
            encoding="utf-8"
        )
        for required in (
            "workflow_dispatch:",
            "runs-on: macos-14",
            "architecture: arm64",
            "desktop_bundle/build_macos.sh",
            "runs-on: windows-2022",
            "desktop_bundle/build_windows.ps1",
            "actions/upload-artifact@v4",
            "publish-prerelease:",
            "push:\n    tags:",
        ):
            self.assertIn(required, workflow)
        self.assertNotIn('${{ inputs.version }}"', workflow)
        self.assertIn("default: 0.3.0-dev1", workflow)

    def test_native_scripts_validate_dist_before_writing_release_archives(self):
        windows = (REPOSITORY / "desktop_bundle/build_windows.ps1").read_text(
            encoding="utf-8"
        )
        macos = (REPOSITORY / "desktop_bundle/build_macos.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('"dist\\windows"', windows)
        self.assertIn('"build\\venv\\windows"', windows)
        self.assertIn('else { "0.3.0-dev1" }', windows)
        self.assertIn('$ReleaseRoot = Join-Path $RepoRoot "release"', windows)
        self.assertIn('"MS-Event-Studio-$Version-windows-x64.zip"', windows)
        self.assertIn('"build\\release-staging\\windows-"', windows)
        self.assertIn("The staged Windows archive is missing", windows)
        self.assertIn("Move-Item -LiteralPath $StagedArchive -Destination $Archive", windows)
        self.assertNotIn('"release\\windows\\', windows)
        self.assertIn('dist_root="$repo_root/dist/macos"', macos)
        self.assertIn('venv_root="$repo_root/build/venv/macos"', macos)
        self.assertIn('${MS_EVENT_STUDIO_VERSION:-0.3.0-dev1}', macos)
        self.assertIn('release_root="$repo_root/release"', macos)
        self.assertIn('archive="$release_root/MS-Event-Studio-', macos)
        self.assertIn('build/release-staging/macos.', macos)
        self.assertIn('unzip -tq "$staged_archive"', macos)
        self.assertIn('mv "$staged_archive" "$archive"', macos)
        self.assertNotIn('release/macos/', macos)


if __name__ == "__main__":
    unittest.main()
