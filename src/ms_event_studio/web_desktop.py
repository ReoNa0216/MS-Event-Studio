"""Single-renderer pywebview desktop host for MS Event Studio.

All project and scientific operations remain behind the loopback HTTP API. The
native host owns only the window lifecycle, a narrow file-dialog adapter,
runtime checks, logging, and the single-instance guard.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import sys
import threading
import time
from typing import Any
from urllib.request import Request, urlopen
import uuid

from . import __version__
from .runtime_smoke import packaged_scientific_smoke
from .web_app import create_http_server


APP_DISPLAY_NAME = "MS Event Studio"
LOGGER = logging.getLogger(__name__)
WINDOWS_ALREADY_EXISTS = 183
WEBVIEW2_CLIENT_IDS = (
    "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",
    "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}",
    "{65C35B14-6C2D-412B-AC46-7148CC9D6497}",
)
_DPI_AWARENESS: str | None = None
WINDOW_MINIMUM_CSS_SIZE = (960, 640)
_DPI_WINDOW_HANDLERS: dict[str, Any] = {}
_DPI_SYNC_TIMERS: dict[str, list[Any]] = {}


def enable_per_monitor_dpi_awareness() -> str:
    """Enable Per-Monitor v2 before pywebview creates any native objects.

    The packaged executable also declares PerMonitorV2 in its embedded
    manifest.  This runtime call keeps source launches correct and gives older
    Windows versions a documented fallback without silently becoming
    DPI-unaware.
    """

    global _DPI_AWARENESS
    if _DPI_AWARENESS is not None:
        return _DPI_AWARENESS
    if sys.platform != "win32":
        _DPI_AWARENESS = "platform-native"
        return _DPI_AWARENESS

    try:
        user32 = ctypes.windll.user32
        setter = user32.SetProcessDpiAwarenessContext
        setter.argtypes = [ctypes.c_void_p]
        setter.restype = ctypes.c_bool
        if setter(ctypes.c_void_p(-4)):  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            _DPI_AWARENESS = "per-monitor-v2"
            return _DPI_AWARENESS

        # A manifest may have established PMv2 before Python starts.  Query the
        # thread context so E_ACCESSDENIED is not mistaken for a failed setup.
        get_context = user32.GetThreadDpiAwarenessContext
        get_context.restype = ctypes.c_void_p
        contexts_equal = user32.AreDpiAwarenessContextsEqual
        contexts_equal.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        contexts_equal.restype = ctypes.c_bool
        current = get_context()
        if contexts_equal(current, ctypes.c_void_p(-4)):
            _DPI_AWARENESS = "per-monitor-v2"
            return _DPI_AWARENESS
        if contexts_equal(current, ctypes.c_void_p(-3)):
            _DPI_AWARENESS = "per-monitor"
            return _DPI_AWARENESS
    except (AttributeError, OSError):
        pass

    try:
        # PROCESS_PER_MONITOR_DPI_AWARE, available since Windows 8.1.
        result = int(ctypes.windll.shcore.SetProcessDpiAwareness(2))
        if result == 0:
            _DPI_AWARENESS = "per-monitor"
            return _DPI_AWARENESS
    except (AttributeError, OSError):
        pass

    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            _DPI_AWARENESS = "system"
            return _DPI_AWARENESS
    except (AttributeError, OSError):
        pass
    _DPI_AWARENESS = "unavailable"
    return _DPI_AWARENESS


def minimum_window_size_for_dpi(dpi: int) -> tuple[int, int]:
    """Convert the WebView's CSS-pixel minimum into native pixels."""

    scale = max(96, int(dpi)) / 96.0
    return tuple(int(round(value * scale)) for value in WINDOW_MINIMUM_CSS_SIZE)


def install_per_monitor_minimum(window: Any) -> None:
    """Keep pywebview's WinForms minimum synchronized across monitors.

    pywebview 6.2.1 calculates ``MinimumSize`` only on the startup monitor.
    Bind on the WinForms UI thread, update the native minimum for each new DPI,
    and accept Windows' suggested rectangle so the logical size remains stable.
    """

    if sys.platform != "win32":
        return
    native = getattr(window, "native", None)
    if native is None:
        return

    from System import Action
    from System.Drawing import Size

    user32 = ctypes.windll.user32
    user32.GetDpiForWindow.argtypes = [ctypes.c_void_p]
    user32.GetDpiForWindow.restype = ctypes.c_uint

    def current_dpi() -> int:
        handle = int(native.Handle.ToInt64())
        return int(user32.GetDpiForWindow(ctypes.c_void_p(handle))) or 96

    def apply_minimum(dpi: int) -> None:
        width, height = minimum_window_size_for_dpi(dpi)
        native.MinimumSize = Size(width, height)

    def on_dpi_changed(_sender: Any, event: Any) -> None:
        dpi = int(getattr(event, "DeviceDpiNew", 0)) or current_dpi()
        suggested = getattr(event, "SuggestedRectangle", None)
        if suggested is not None:
            native.Bounds = suggested
        # Let WinForms finish its own autoscale work, then make our CSS-pixel
        # minimum authoritative. Some pythonnet/WinForms hosts never raise this
        # event, so the pywebview moved event below is the primary fallback.
        native.BeginInvoke(Action(lambda: apply_minimum(dpi)))

    def bind() -> None:
        apply_minimum(current_dpi())
        native.DpiChanged += on_dpi_changed
        key = str(getattr(window, "uid", id(window)))
        _DPI_WINDOW_HANDLERS[key] = on_dpi_changed

    native.Invoke(Action(bind))


def synchronize_per_monitor_minimum(window: Any) -> None:
    """Synchronize the WinForms minimum using the window's actual monitor DPI."""

    if sys.platform != "win32":
        return
    native = getattr(window, "native", None)
    if native is None:
        return

    from System import Action
    from System.Drawing import Size

    def synchronize() -> None:
        handle = int(native.Handle.ToInt64())
        dpi = int(ctypes.windll.user32.GetDpiForWindow(ctypes.c_void_p(handle))) or 96
        width, height = minimum_window_size_for_dpi(dpi)
        if native.MinimumSize.Width != width or native.MinimumSize.Height != height:
            native.MinimumSize = Size(width, height)

    # pywebview dispatches ``window.events.moved`` on a Python worker thread,
    # so a synchronous UI-thread hop is safe and removes a resize race from
    # native DPI QA and ordinary drag-to-monitor use.
    native.Invoke(Action(synchronize))


def schedule_per_monitor_minimum_sync(window: Any) -> None:
    """Recheck monitor DPI after native move/autoscale messages have settled.

    pywebview can dispatch ``moved`` before every WinForms/Windows DPI
    transition has completed. The immediate synchronization handles ordinary
    moves; two short daemon retries close that cross-monitor timing window
    without blocking the UI thread.
    """

    synchronize_per_monitor_minimum(window)
    if sys.platform != "win32":
        return
    key = str(getattr(window, "uid", id(window)))
    for timer in _DPI_SYNC_TIMERS.pop(key, []):
        timer.cancel()
    pending: list[Any] = []

    def retry() -> None:
        try:
            synchronize_per_monitor_minimum(window)
        except Exception:
            LOGGER.debug("Deferred DPI minimum synchronization skipped", exc_info=True)

    for delay in (0.1, 0.35):
        timer = threading.Timer(delay, retry)
        timer.daemon = True
        timer.start()
        pending.append(timer)
    _DPI_SYNC_TIMERS[key] = pending


def user_state_dir() -> Path:
    override = os.environ.get("MS_EVENT_STUDIO_CONFIG_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return base / APP_DISPLAY_NAME


def recent_projects_path() -> Path:
    return user_state_dir() / "recent_projects.json"


def configure_logging() -> Path:
    log_dir = user_state_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "ms-event-studio.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(handler, RotatingFileHandler) for handler in root.handlers):
        handler = RotatingFileHandler(
            log_path,
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(handler)
    return log_path


def show_native_message(
    message: str,
    *,
    title: str = APP_DISPLAY_NAME,
    error: bool = False,
) -> None:
    if sys.platform == "win32":
        icon = 0x10 if error else 0x30
        ctypes.windll.user32.MessageBoxW(None, message, title, icon)
        return
    print(f"{title}: {message}", file=sys.stderr)


def webview2_runtime_version() -> str | None:
    if sys.platform != "win32":
        return "platform-webview"
    import winreg

    roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    views = (
        0,
        getattr(winreg, "KEY_WOW64_32KEY", 0),
        getattr(winreg, "KEY_WOW64_64KEY", 0),
    )
    prefixes = (
        r"SOFTWARE\Microsoft\EdgeUpdate\Clients",
        r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients",
    )
    for client_id in WEBVIEW2_CLIENT_IDS:
        for root in roots:
            for prefix in prefixes:
                for view in views:
                    try:
                        with winreg.OpenKey(
                            root,
                            f"{prefix}\\{client_id}",
                            0,
                            winreg.KEY_READ | view,
                        ) as key:
                            version = str(winreg.QueryValueEx(key, "pv")[0]).strip()
                            if version and version != "0.0.0.0":
                                return version
                    except OSError:
                        continue
    return None


class SingleInstanceGuard:
    def __init__(self, name: str = "MSEventStudio.Desktop") -> None:
        self.name = name
        self._handle: Any = None
        self._lock_file: Any = None

    def acquire(self) -> bool:
        if sys.platform == "win32":
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = (
                wintypes.LPVOID,
                wintypes.BOOL,
                wintypes.LPCWSTR,
            )
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.CreateMutexW(None, False, f"Local\\{self.name}")
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            if ctypes.get_last_error() == WINDOWS_ALREADY_EXISTS:
                kernel32.CloseHandle(handle)
                return False
            self._handle = (kernel32, handle)
            return True

        import fcntl

        lock_dir = user_state_dir()
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_id = uuid.uuid5(uuid.NAMESPACE_URL, self.name).hex
        lock_file = (lock_dir / f"instance-{lock_id}.lock").open("a+b")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            lock_file.close()
            return False
        self._lock_file = lock_file
        return True

    def release(self) -> None:
        if self._handle is not None:
            kernel32, handle = self._handle
            kernel32.CloseHandle(handle)
            self._handle = None
        if self._lock_file is not None:
            import fcntl

            try:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                self._lock_file.close()
                self._lock_file = None


class WebViewPathDialog:
    """Native dialog provider injected into the loopback API.

    The HTTP session turns the selected path into an opaque, short-lived token
    before returning anything to JavaScript.
    """

    def __init__(self, window: Any, webview_module: Any) -> None:
        self._window = window
        self._webview = webview_module

    def __call__(
        self,
        *,
        role: str,
        title: str = "",
        initial_dir: str = "",
        **_unused: Any,
    ) -> dict[str, Any]:
        if role not in {
            "source_file",
            "project_open",
            "project_target",
            "review_export_file",
            "audit_export_target",
        }:
            raise ValueError("不支持的路径选择用途")
        initial = Path(initial_dir).expanduser() if str(initial_dir).strip() else Path.home()
        if initial.is_file():
            initial = initial.parent
        directory = str(initial) if initial.is_dir() else ""
        if role == "source_file":
            dialog_type = self._webview.FileDialog.OPEN
            selected = self._window.create_file_dialog(
                dialog_type,
                directory=directory,
                allow_multiple=False,
                file_types=("MS 文本导出 (*.txt)", "所有文件 (*.*)"),
            )
        elif role == "review_export_file":
            dialog_type = self._webview.FileDialog.SAVE
            selected = self._window.create_file_dialog(
                dialog_type,
                directory=directory,
                allow_multiple=False,
                save_filename="MS_Event_Studio_审阅结果.csv",
                file_types=("CSV 文件 (*.csv)",),
            )
        else:
            dialog_type = self._webview.FileDialog.FOLDER
            selected = self._window.create_file_dialog(
                dialog_type,
                directory=directory,
                allow_multiple=False,
            )
        first = selected[0] if selected else None
        path = "" if first in {None, ""} else str(Path(first).expanduser().resolve())
        return {"path": path, "cancelled": not bool(path)}


def _json_get(url: str) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"loopback API returned HTTP {response.status}")
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("loopback API did not return a JSON object")
    return payload


def check_desktop_runtime(*, webview_module: Any | None = None) -> dict[str, Any]:
    if webview_module is None:
        import webview as webview_module
    if sys.platform == "win32":
        runtime = webview2_runtime_version()
        if runtime is None:
            raise RuntimeError("未检测到 Microsoft Edge WebView2 Runtime。")
        import webview.platforms.edgechromium  # noqa: F401
        import webview.platforms.winforms  # noqa: F401
        return {"platform_backend": "edgechromium", "webview2_version": runtime}
    if sys.platform == "darwin":
        import webview.platforms.cocoa  # noqa: F401
        return {"platform_backend": "cocoa", "webview2_version": None}
    raise RuntimeError("桌面候选目前只支持 Windows 与 macOS")


def check_webview_runtime(*, webview_module: Any | None = None) -> dict[str, Any]:
    """Load the real page in a hidden native WebView and call its core API."""

    dpi_awareness = enable_per_monitor_dpi_awareness()
    if webview_module is None:
        import webview as webview_module

    runtime = check_desktop_runtime(webview_module=webview_module)

    server = create_http_server(recent_path=recent_projects_path())
    loaded = threading.Event()
    failures: list[BaseException] = []
    result: dict[str, Any] = {}
    window: Any | None = None
    server.start()
    try:
        webview_module.settings["ALLOW_DOWNLOADS"] = False
        webview_module.settings["SHOW_DEFAULT_MENUS"] = False
        window = webview_module.create_window(
            f"{APP_DISPLAY_NAME} · Smoke",
            server.capability_url,
            width=960,
            height=640,
            min_size=(960, 640),
            resizable=True,
            text_select=True,
            zoomable=True,
            hidden=True,
            background_color="#f6f7f9",
        )
        if window is None:
            raise RuntimeError("无法创建隐藏 WebView 探针")
        server.set_path_dialog(WebViewPathDialog(window, webview_module))
        window.events.loaded += lambda *_args: loaded.set()

        def exercise() -> None:
            try:
                if not loaded.wait(timeout=30):
                    raise RuntimeError("隐藏 WebView 未能完成首页加载")
                health = _json_get(f"{server.url.rstrip('/')}/api/health")
                bootstrap = _json_get(f"{server.url.rstrip('/')}/api/bootstrap")
                deadline = time.monotonic() + 30
                frontend: dict[str, Any] | None = None
                while time.monotonic() < deadline:
                    raw = window.evaluate_js(
                        """
                        (() => {
                          const hook = window.__MS_EVENT_STUDIO__;
                          if (!hook || typeof hook.getState !== 'function') {
                            return null;
                          }
                          const state = hook.getState();
                          if (!state || state.ready !== true) return null;
                          return JSON.stringify({ready: true, state});
                        })();
                        """
                    )
                    if raw:
                        frontend = json.loads(raw) if isinstance(raw, str) else raw
                        if isinstance(frontend, dict) and frontend.get("ready") is True:
                            break
                    time.sleep(0.05)
                if not frontend or frontend.get("ready") is not True:
                    raise RuntimeError("首页未暴露已就绪的 WebView 状态探针")
                result.update(
                    {
                        "status": "ok",
                        "renderer": "pywebview",
                        "hidden": True,
                        "page_loaded": True,
                        "api_health": health.get("status") == "ok" or health.get("ok") is True,
                        "api_bootstrap": bool(bootstrap),
                        "frontend_ready": True,
                        "frontend_state": frontend.get("state"),
                        "runtime": {**runtime, "dpi_awareness": dpi_awareness},
                    }
                )
                if not result["api_health"]:
                    raise RuntimeError("loopback health check did not report ready")
            except BaseException as exc:
                failures.append(exc)
            finally:
                try:
                    window.destroy()
                except Exception as exc:
                    if not failures:
                        failures.append(exc)

        gui = "edgechromium" if sys.platform == "win32" else None
        webview_module.start(func=exercise, gui=gui, debug=False, private_mode=True)
        if failures:
            raise RuntimeError(f"隐藏 WebView smoke 失败：{failures[0]}") from failures[0]
        if not result:
            raise RuntimeError("隐藏 WebView smoke 没有返回结果")
        return result
    finally:
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass
        server.stop()


def run_desktop(
    args: argparse.Namespace,
    *,
    webview_module: Any | None = None,
) -> None:
    enable_per_monitor_dpi_awareness()
    runtime = check_desktop_runtime(webview_module=webview_module)
    LOGGER.info("Desktop WebView runtime: %s", runtime)
    if webview_module is None:
        import webview as webview_module

    server = create_http_server(recent_path=recent_projects_path())
    webview_module.settings["ALLOW_DOWNLOADS"] = False
    webview_module.settings["SHOW_DEFAULT_MENUS"] = False
    window = webview_module.create_window(
        APP_DISPLAY_NAME,
        server.capability_url,
        width=1280,
        height=800,
        min_size=(960, 640),
        resizable=True,
        text_select=True,
        zoomable=True,
        background_color="#f6f7f9",
    )
    if window is None:
        server.stop()
        raise RuntimeError("无法创建 MS Event Studio 应用窗口")
    server.set_path_dialog(WebViewPathDialog(window, webview_module))
    def initialize_native_dpi_hooks(*_args: Any) -> None:
        install_per_monitor_minimum(window)
        synchronize_per_monitor_minimum(window)

    window.events.shown += initialize_native_dpi_hooks
    window.events.moved += lambda *_args: schedule_per_monitor_minimum_sync(window)

    def block_unsafe_close() -> bool | None:
        if not server.busy:
            return None
        show_native_message("当前正在分析或创建项目，请等待完成或先取消任务。")
        return False

    window.events.closing += block_unsafe_close
    server.start()
    try:
        gui = "edgechromium" if sys.platform == "win32" else None
        webview_module.start(
            gui=gui,
            debug=bool(args.debug),
            private_mode=True,
        )
    finally:
        server.stop()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"运行 {APP_DISPLAY_NAME} 桌面应用。")
    parser.add_argument("--debug", action="store_true", help="启用开发工具，仅用于本地调试。")
    parser.add_argument("--check-runtime", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--webview-smoke", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-report", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _write_smoke_report(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main(argv: list[str] | None = None) -> int:
    # This must run before pywebview imports WinForms or creates a native
    # window.  Otherwise pywebview's legacy fallback locks the process to the
    # DPI of the monitor on which it started.
    enable_per_monitor_dpi_awareness()
    log_path = configure_logging()
    guard = SingleInstanceGuard()
    args = parse_args(argv)
    try:
        if args.check_runtime:
            print(json.dumps(check_desktop_runtime(), ensure_ascii=False, sort_keys=True))
            return 0
        if args.webview_smoke or args.smoke_test:
            payload = check_webview_runtime()
            payload["application_version"] = __version__
            payload["scientific"] = packaged_scientific_smoke()
            _write_smoke_report(args.smoke_report, payload)
            return 0
        if not guard.acquire():
            show_native_message("MS Event Studio 已经在运行。请切换到现有窗口。")
            return 2
        run_desktop(args)
        return 0
    except Exception as exc:
        LOGGER.exception("Desktop startup failed")
        _write_smoke_report(
            args.smoke_report,
            {
                "status": "error",
                "application_version": __version__,
                "error_type": type(exc).__name__,
                "message": str(exc),
            },
        )
        if not (args.webview_smoke or args.smoke_test or args.check_runtime):
            show_native_message(
                f"MS Event Studio 无法启动：\n{exc}\n\n诊断日志：{log_path}",
                error=True,
            )
        else:
            print(f"{APP_DISPLAY_NAME} runtime check failed: {exc}", file=sys.stderr)
        return 1
    finally:
        guard.release()


if __name__ == "__main__":
    raise SystemExit(main())
