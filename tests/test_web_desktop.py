from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ms_event_studio.web_desktop as desktop


class _Event:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self) -> None:
        for handler in tuple(self.handlers):
            handler()


class _Events:
    def __init__(self) -> None:
        self.loaded = _Event()
        self.closing = _Event()
        self.shown = _Event()
        self.moved = _Event()


class _Window:
    def __init__(self, selected=None) -> None:
        self.events = _Events()
        self.selected = selected
        self.destroy_count = 0
        self.dialog_calls = []

    def create_file_dialog(self, kind, **kwargs):
        self.dialog_calls.append((kind, kwargs))
        return self.selected

    def evaluate_js(self, _code):
        return json.dumps(
            {"ready": True, "state": {"view": "welcome", "fixture": None}},
            ensure_ascii=False,
        )

    def destroy(self):
        self.destroy_count += 1


class _FileDialog:
    OPEN = "open"
    FOLDER = "folder"
    SAVE = "save"


class _WebView:
    FileDialog = _FileDialog

    def __init__(self, selected=None) -> None:
        self.settings = {}
        self.window = _Window(selected)
        self.create_calls = []
        self.start_calls = []

    def create_window(self, *args, **kwargs):
        self.create_calls.append((args, kwargs))
        return self.window

    def start(self, *, func=None, **kwargs):
        self.start_calls.append(kwargs)
        self.window.events.shown.fire()
        self.window.events.loaded.fire()
        if func is not None:
            func()


class _Server:
    capability_url = "http://127.0.0.1:43210/?native_bridge=opaque"
    url = "http://127.0.0.1:43210/"

    def __init__(self) -> None:
        self.start_count = 0
        self.stop_count = 0
        self.path_dialog = None
        self.busy = False

    def set_path_dialog(self, provider) -> None:
        self.path_dialog = provider

    def start(self) -> None:
        self.start_count += 1

    def stop(self) -> None:
        self.stop_count += 1


class WebViewPathDialogTest(unittest.TestCase):
    def test_dialog_supports_narrow_native_path_roles(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            selected = str(Path(tmp) / "source.txt")
            webview = _WebView(selected=(selected,))
            provider = desktop.WebViewPathDialog(webview.window, webview)

            result = provider(role="source_file", title="选择")

            self.assertFalse(result["cancelled"])
            self.assertEqual(Path(result["path"]), Path(selected).resolve())
            self.assertEqual(webview.window.dialog_calls[0][0], _FileDialog.OPEN)

            export = provider(role="review_export_file", title="导出审阅结果")
            self.assertFalse(export["cancelled"])
            kind, options = webview.window.dialog_calls[1]
            self.assertEqual(kind, _FileDialog.SAVE)
            self.assertEqual(options["save_filename"], "MS_Event_Studio_审阅结果.csv")
            self.assertEqual(options["file_types"], ("CSV 文件 (*.csv)",))

            audit = provider(role="audit_export_target", title="导出完整审计数据包")
            self.assertFalse(audit["cancelled"])
            self.assertEqual(webview.window.dialog_calls[2][0], _FileDialog.FOLDER)
            with self.assertRaises(ValueError):
                provider(role="export_file")

    def test_cancel_never_invents_a_path(self):
        webview = _WebView(selected=None)
        provider = desktop.WebViewPathDialog(webview.window, webview)

        self.assertEqual(
            provider(role="project_open"),
            {"path": "", "cancelled": True},
        )


class WebDesktopLifecycleTest(unittest.TestCase):
    def test_native_minimum_tracks_css_size_at_each_monitor_dpi(self):
        self.assertEqual(desktop.minimum_window_size_for_dpi(96), (960, 640))
        self.assertEqual(desktop.minimum_window_size_for_dpi(120), (1200, 800))
        self.assertEqual(desktop.minimum_window_size_for_dpi(144), (1440, 960))
        self.assertEqual(desktop.minimum_window_size_for_dpi(192), (1920, 1280))

    def test_main_enables_per_monitor_dpi_before_runtime_or_window_setup(self):
        calls = []
        with mock.patch.object(
            desktop,
            "enable_per_monitor_dpi_awareness",
            side_effect=lambda: calls.append("dpi") or "per-monitor-v2",
        ), mock.patch.object(
            desktop,
            "configure_logging",
            side_effect=lambda: calls.append("logging") or Path("test.log"),
        ), mock.patch.object(
            desktop,
            "check_desktop_runtime",
            side_effect=lambda: calls.append("runtime") or {"platform_backend": "test"},
        ):
            self.assertEqual(desktop.main(["--check-runtime"]), 0)

        self.assertEqual(calls, ["dpi", "logging", "runtime"])

    def test_production_window_uses_only_the_capability_webview(self):
        server = _Server()
        webview = _WebView()
        args = desktop.parse_args([])
        with mock.patch.object(desktop, "create_http_server", return_value=server), mock.patch.object(
            desktop,
            "check_desktop_runtime",
            return_value={"platform_backend": "test"},
        ), mock.patch.object(
            desktop, "install_per_monitor_minimum"
        ) as install_minimum, mock.patch.object(
            desktop, "synchronize_per_monitor_minimum"
        ) as synchronize_minimum:
            desktop.run_desktop(args, webview_module=webview)
            webview.window.events.moved.fire()
            self.assertEqual(synchronize_minimum.call_count, 2)

        self.assertEqual(len(webview.create_calls), 1)
        call_args, call_kwargs = webview.create_calls[0]
        self.assertEqual(call_args[1], server.capability_url)
        self.assertNotIn("hidden", call_kwargs)
        self.assertNotIn("js_api", call_kwargs)
        self.assertEqual(call_kwargs["min_size"], (960, 640))
        self.assertEqual(server.start_count, 1)
        self.assertEqual(server.stop_count, 1)
        self.assertIsInstance(server.path_dialog, desktop.WebViewPathDialog)
        install_minimum.assert_called_once_with(webview.window)

    def test_hidden_smoke_loads_page_and_both_core_get_endpoints(self):
        webview = _WebView()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(
            desktop.os.environ,
            {"MS_EVENT_STUDIO_CONFIG_DIR": tmp},
        ), mock.patch.object(
            desktop,
            "check_desktop_runtime",
            return_value={"platform_backend": "test"},
        ):
            result = desktop.check_webview_runtime(webview_module=webview)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["renderer"], "pywebview")
        self.assertTrue(result["hidden"])
        self.assertTrue(result["page_loaded"])
        self.assertTrue(result["api_health"])
        self.assertTrue(result["api_bootstrap"])
        self.assertTrue(result["frontend_ready"])
        self.assertEqual(result["runtime"]["platform_backend"], "test")
        self.assertTrue(webview.create_calls[0][1]["hidden"])
        self.assertEqual(webview.start_calls[0]["private_mode"], True)

        source = Path(desktop.__file__).read_text(encoding="utf-8")
        self.assertIn("state.ready !== true", source)
        self.assertNotIn("hook.ready !== true", source)

    def test_entrypoint_and_cli_use_only_the_webview_renderer(self):
        repository = Path(__file__).resolve().parents[1]
        entry = (repository / "desktop_bundle/ms_event_studio_gui.py").read_text(
            encoding="utf-8"
        )
        project = (repository / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("from ms_event_studio.web_desktop import main", entry)
        self.assertIn('ms-event-studio-gui = "ms_event_studio.web_desktop:main"', project)
        self.assertNotIn("from ms_event_studio.desktop import main", entry)
        source_root = repository / "src/ms_event_studio"
        self.assertFalse((source_root / "desktop.py").exists())
        self.assertFalse((source_root / "theme.py").exists())

        forbidden_imports: list[str] = []
        for path in source_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: tuple[str, ...] = ()
                if isinstance(node, ast.Import):
                    names = tuple(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = (node.module,)
                if any(
                    name in {"tkinter", "ttk"}
                    or name.startswith(("tkinter.", "ttk."))
                    for name in names
                ):
                    forbidden_imports.append(str(path.relative_to(repository)))
        self.assertEqual(forbidden_imports, [])


if __name__ == "__main__":
    unittest.main()
