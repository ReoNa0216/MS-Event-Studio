from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path

from desktop_bundle.build_desktop import (
    WINDOWS_RUNTIME_CONFIG,
    build_arguments,
    copy_windows_runtime_config,
    locate_executable,
    project_application_version,
    validate_single_renderer_tree,
    validate_smoke_candidate_identity,
    validate_windows_runtime_config,
    write_build_manifest,
)
from desktop_bundle.webview_smoke import validate_webview_smoke_payload
from ms_event_studio import __version__
from ms_event_studio.runtime_smoke import packaged_scientific_smoke


class PackagingContractTest(unittest.TestCase):
    def test_packaged_scientific_runtime_smoke_exercises_core_stack(self):
        result = packaged_scientific_smoke()
        self.assertEqual(result["scan_rows"], 1201)
        self.assertEqual(result["event_rows"], 3)
        self.assertEqual(result["human_rows"], 1)
        self.assertEqual(result["machine_rows"], 3)
        self.assertGreater(result["display_points"], 0)
        self.assertIsInstance(__version__, str)

    def test_windows_webview_runtime_config_is_required_and_valid(self):
        repository = Path(__file__).resolve().parents[1]
        config = repository / "packaging/windows" / WINDOWS_RUNTIME_CONFIG
        validate_windows_runtime_config(config)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            executable = Path(tmp) / "MS-Event-Studio.exe"
            executable.touch()
            copied = copy_windows_runtime_config(repository, executable)
            self.assertEqual(copied, executable.with_name(WINDOWS_RUNTIME_CONFIG))
            self.assertEqual(copied.read_bytes(), config.read_bytes())
            validate_windows_runtime_config(copied)

    def test_windows_build_uses_native_webview_spec(self):
        repository = Path(__file__).resolve().parents[1]
        arguments = build_arguments(repository, platform_name="windows")
        self.assertIn("--clean", arguments)
        self.assertEqual(
            arguments[arguments.index("--distpath") + 1],
            str(repository / "dist/windows"),
        )
        self.assertEqual(arguments[-1], str(repository / "packaging/windows/ms_event_studio.spec"))

        spec = Path(arguments[-1]).read_text(encoding="utf-8")
        for required in (
            'ms_event_studio/web',
            'collect_data_files("webview", subdir="lib")',
            'collect_data_files("webview", subdir="js")',
            'collect_dynamic_libs("webview")',
            '"webview.platforms.edgechromium"',
            '"webview.platforms.winforms"',
            '"ms_event_studio.desktop"',
            '"ms_event_studio.theme"',
            '"_tkinter"',
            '"idlelib"',
            '"tcl"',
            '"tkinter"',
            '"webview.platforms.mshtml"',
            '"webbrowserinterop."',
            '"pywebview-android.jar"',
            '"packaging/windows/runtime-placeholder.txt"',
            "a.binaries = [entry for entry in a.binaries if windows_production_payload(entry)]",
        ):
            self.assertIn(required, spec)
        self.assertIn('name="MS-Event-Studio"', spec)
        self.assertIn('manifest=str(repo_root / "packaging/windows/MS-Event-Studio.manifest")', spec)
        manifest = (repository / "packaging/windows/MS-Event-Studio.manifest").read_text(
            encoding="utf-8"
        )
        self.assertIn(">PerMonitorV2, PerMonitor</dpiAwareness>", manifest)
        self.assertIn(">true/pm</dpiAware>", manifest)

    def test_macos_build_is_arm64_app_with_bundle_identity_and_icon(self):
        repository = Path(__file__).resolve().parents[1]
        arguments = build_arguments(repository, platform_name="macos")
        self.assertEqual(arguments[-1], str(repository / "packaging/macos/ms_event_studio.spec"))
        spec = Path(arguments[-1]).read_text(encoding="utf-8")
        for required in (
            'target_arch="arm64"',
            'bundle_identifier="org.hulab.ms-event-studio"',
            'collect_data_files("webview", subdir="lib")',
            'collect_data_files("webview", subdir="js")',
            '"webview.platforms.cocoa"',
            '"ms_event_studio.desktop"',
            '"ms_event_studio.theme"',
            '"_tkinter"',
            '"idlelib"',
            '"tcl"',
            '"tkinter"',
            '"webview.platforms.win32"',
            '"webview.platforms.winforms"',
            '"pywebview-android.jar"',
            '"/runtimes/win-"',
            "a.binaries = [entry for entry in a.binaries if macos_production_payload(entry)]",
            '"NSHighResolutionCapable": True',
            'name="MS-Event-Studio.app"',
        ):
            self.assertIn(required, spec)
        self.assertGreater(
            spec.index("a.binaries = [entry for entry in a.binaries if macos_production_payload(entry)]"),
            spec.index("a = Analysis("),
        )

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

    def test_pywebview_is_pinned_for_both_native_builds_and_web_assets_are_package_data(self):
        repository = Path(__file__).resolve().parents[1]
        project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(project["project"]["version"], "0.3.0.dev1")
        self.assertEqual(__version__, project["project"]["version"])
        self.assertEqual(project_application_version(repository), __version__)
        self.assertIn("pywebview==6.2.1", project["project"]["optional-dependencies"]["packaging"])
        self.assertEqual(
            project["project"]["scripts"]["ms-event-studio-gui"],
            "ms_event_studio.web_desktop:main",
        )
        package_data = project["tool"]["setuptools"]["package-data"]["ms_event_studio"]
        for pattern in ("web/*.html", "web/*.css", "web/*.js", "web/icons/*.svg"):
            self.assertIn(pattern, package_data)
        for relative in (
            "packaging/windows/requirements-windows.txt",
            "packaging/macos/requirements-macos.txt",
        ):
            self.assertIn(
                "pywebview==6.2.1",
                (repository / relative).read_text(encoding="utf-8"),
            )

    def test_packaged_smoke_contract_requires_webview_api_dom_and_scientific_roundtrip(self):
        payload = {
            "status": "ok",
            "renderer": "pywebview",
            "hidden": True,
            "application_version": "0.3.0.dev1",
            "checks": {
                "page_loaded": True,
                "frontend_ready": True,
                "api_health": True,
                "api_bootstrap": True,
            },
            "scientific": {
                "scan_rows": 1201,
                "event_rows": 3,
                "display_points": 10,
                "human_rows": 1,
                "machine_rows": 3,
            },
        }
        self.assertEqual(validate_webview_smoke_payload(payload), payload)
        for key in ("frontend_ready", "api_health"):
            broken = {**payload, "checks": {**payload["checks"], key: False}}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                validate_webview_smoke_payload(broken)
        with self.assertRaisesRegex(ValueError, "pywebview"):
            validate_webview_smoke_payload({**payload, "renderer": "tk"})

    def test_packaged_entry_has_no_legacy_tk_fallback(self):
        repository = Path(__file__).resolve().parents[1]
        entry = (repository / "desktop_bundle/ms_event_studio_gui.py").read_text(encoding="utf-8")
        self.assertIn("from ms_event_studio.web_desktop import main", entry)
        self.assertNotIn("from ms_event_studio.desktop import main", entry)
        self.assertNotIn("tkinter", entry.casefold())
        production_sources = (
            "desktop_bundle/ms_event_studio_gui.py",
            "src/ms_event_studio/web_desktop.py",
            "src/ms_event_studio/web_app.py",
            "src/ms_event_studio/web_models.py",
            "src/ms_event_studio/web_review_service.py",
            "src/ms_event_studio/runtime_smoke.py",
        )
        for relative in production_sources:
            source = (repository / relative).read_text(encoding="utf-8").casefold()
            with self.subTest(production_source=relative):
                self.assertNotIn("import tkinter", source)
                self.assertNotIn("from tkinter", source)
                self.assertNotIn("from ms_event_studio.desktop import", source)
                self.assertNotIn("from .desktop import", source)
                self.assertNotIn("from .theme import", source)

    def test_smoke_identity_must_match_source_version_and_platform_backend(self):
        repository = Path(__file__).resolve().parents[1]
        base = {
            "application_version": __version__,
            "runtime": {"platform_backend": "edgechromium"},
        }
        validate_smoke_candidate_identity(repository, base, platform_name="windows")
        with self.assertRaisesRegex(RuntimeError, "application_version"):
            validate_smoke_candidate_identity(
                repository,
                {**base, "application_version": "0.2.0.dev3"},
                platform_name="windows",
            )
        with self.assertRaisesRegex(RuntimeError, "wrong native WebView backend"):
            validate_smoke_candidate_identity(
                repository,
                {**base, "runtime": {"platform_backend": "cocoa"}},
                platform_name="windows",
            )
        validate_smoke_candidate_identity(
            repository,
            {
                "application_version": __version__,
                "runtime": {"platform_backend": "cocoa"},
            },
            platform_name="macos",
        )

    def test_final_tree_rejects_tk_tcl_and_second_renderers(self):
        web_assets = [
            {"path": f"MS-Event-Studio/_internal/ms_event_studio/web/{name}"}
            for name in ("index.html", "tokens.css", "app.css", "app.js")
        ]
        windows_runtime = [
            {"path": f"MS-Event-Studio/_internal/webview/lib/{name}"}
            for name in (
                "Microsoft.Web.WebView2.Core.dll",
                "Microsoft.Web.WebView2.WinForms.dll",
                "runtimes/win-x64/native/WebView2Loader.dll",
            )
        ]
        windows_runtime += [
            {"path": f"MS-Event-Studio/_internal/webview/lib/runtimes/{arch}/native/runtime-placeholder.txt"}
            for arch in ("win-arm64", "win-x86")
        ]
        valid_windows = web_assets + windows_runtime
        validate_single_renderer_tree(valid_windows, platform_name="windows")
        validate_single_renderer_tree(web_assets, platform_name="macos")
        for forbidden in (
            "MS-Event-Studio/_internal/_tkinter.pyd",
            "MS-Event-Studio/_internal/tcl8/8.6/init.tcl",
            "MS-Event-Studio/_internal/webview/lib/pywebview-android.jar",
            "MS-Event-Studio/_internal/PySide6/QtWebEngineCore.dll",
            "MS-Event-Studio/_internal/cefpython3/libcef.dll",
            "MS-Event-Studio/_internal/webview/lib/runtimes/win-arm64/native/WebView2Loader.dll",
            "MS-Event-Studio/_internal/webview/lib/runtimes/win-x86/native/WebView2Loader.dll",
        ):
            with self.subTest(forbidden=forbidden), self.assertRaisesRegex(
                RuntimeError, "second renderer|Tk/Tcl"
            ):
                validate_single_renderer_tree(
                    valid_windows + [{"path": forbidden}],
                    platform_name="windows",
                )
        with self.assertRaisesRegex(RuntimeError, "missing production Web assets"):
            validate_single_renderer_tree(windows_runtime, platform_name="windows")
        with self.assertRaisesRegex(RuntimeError, "Edge Chromium runtime"):
            validate_single_renderer_tree(web_assets, platform_name="windows")

    def test_final_manifest_can_be_refreshed_after_macos_signing_smoke(self):
        repository = Path(__file__).resolve().parents[1]
        script = (repository / "desktop_bundle/build_macos.sh").read_text(encoding="utf-8")
        smoke_index = script.index('"$executable" --webview-smoke')
        final_sign_index = script.index('codesign --force --deep --sign - "$app_path"', smoke_index)
        verify_index = script.index('codesign --verify --deep --strict "$app_path"', final_sign_index)
        refresh_index = script.index("--refresh-manifest", verify_index)
        self.assertLess(smoke_index, final_sign_index)
        self.assertLess(final_sign_index, verify_index)
        self.assertLess(verify_index, refresh_index)
        self.assertTrue(callable(write_build_manifest))


if __name__ == "__main__":
    unittest.main()
