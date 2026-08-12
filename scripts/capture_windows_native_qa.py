"""Capture packaged MS Event Studio states on real Windows DPI monitors.

This is a native WebView/Win32 evidence gate, not a browser-emulation helper.
It launches the frozen executable, connects to that process' Edge WebView2
debug endpoint on loopback, moves the real top-level window between physical
monitors, verifies ``GetDpiForWindow``, and captures physical desktop pixels.

Only monitors whose effective DPI exactly matches a requested scale are used.
Missing scales fail by default; the caller must never substitute Playwright
viewport/device-scale emulation for Windows native DPI evidence.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable
import urllib.request
import xml.etree.ElementTree as ET


REPOSITORY = Path(__file__).resolve().parents[1]
APP_NAME = "MS-Event-Studio"
WINDOW_TITLE = "MS Event Studio"
DEFAULT_STATES = ("welcome", "review-unreviewed-auto", "range-preview")
ALLOWED_STATES = frozenset(
    {
        "welcome",
        "review-unreviewed-auto",
        "range-preview",
        "long-chinese-copy",
    }
)
REQUIRED_STATE_ACTIONS = {
    "welcome": ("#welcomeCreate", "#welcomeOpen"),
    "review-unreviewed-auto": (
        "[data-qa='next-event']",
        "[data-qa='review-accept']",
        "[data-qa='evidence-toggle']",
    ),
    "range-preview": ("[data-qa='range-cancel']", "[data-qa='range-apply']"),
    # The long-copy fixture intentionally holds a native modal open.  Background
    # workbench controls are correctly inert and therefore must not be treated
    # as keyboard targets here.
    "long-chinese-copy": ("[data-qa='export-cancel']",),
}
# pywebview's ``min_size`` is expressed in logical outer-window pixels.  The
# native gate deliberately requests that exact boundary on every monitor so a
# stale startup-monitor ``MinimumSize`` cannot be hidden by a roomy evidence
# window.  Browser viewport dimensions are recorded separately; Win32 chrome
# means they are expected to be smaller than this outer rectangle.
MINIMUM_WINDOW_LOGICAL = (960, 640)
REQUIRED_SMOKE_CHECKS = ("page_loaded", "frontend_ready", "api_health", "api_bootstrap")
PROCESS_PER_MONITOR_DPI_AWARE_V2 = ctypes.c_void_p(-4)
MONITORINFOF_PRIMARY = 1
MDT_EFFECTIVE_DPI = 0
SW_RESTORE = 9
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
WM_CLOSE = 0x0010


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_manifest(root: Path) -> tuple[list[dict[str, Any]], str]:
    """Recompute the build tree using the canonical manifest algorithm."""

    rows: list[dict[str, Any]] = []
    aggregate = hashlib.sha256()
    paths = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and path.name != "build_manifest.json"
        ),
        key=lambda path: path.as_posix(),
    )
    for path in paths:
        relative = path.relative_to(root).as_posix()
        digest = _sha256(path)
        row = {"path": relative, "size_bytes": path.stat().st_size, "sha256": digest}
        rows.append(row)
        aggregate.update(f"{relative}\0{row['size_bytes']}\0{digest}\n".encode("utf-8"))
    return rows, aggregate.hexdigest()


def _assert(condition: Any, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _set_native_dpi_awareness() -> None:
    if sys.platform != "win32":
        raise RuntimeError("native Windows screenshots must run on Windows")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    setter = user32.SetProcessDpiAwarenessContext
    setter.argtypes = [ctypes.c_void_p]
    setter.restype = wintypes.BOOL
    if not setter(PROCESS_PER_MONITOR_DPI_AWARE_V2):
        error = ctypes.get_last_error()
        # ERROR_ACCESS_DENIED means an embedding host already selected a DPI
        # context.  Refuse it: virtualized coordinates would taint evidence.
        raise OSError(error, "could not select Per-Monitor-V2 DPI awareness")


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MonitorInfoEx(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _Rect),
        ("rcWork", _Rect),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


def _rect_list(rect: Any) -> list[int]:
    return [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]


def _enumerate_monitors() -> list[dict[str, Any]]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shcore = ctypes.WinDLL("shcore", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(_Rect),
        wintypes.LPARAM,
    )
    rows: list[dict[str, Any]] = []

    def visit(monitor: int, _dc: int, _rect: Any, _data: int) -> bool:
        info = _MonitorInfoEx()
        info.cbSize = ctypes.sizeof(info)
        _assert(bool(user32.GetMonitorInfoW(monitor, ctypes.byref(info))), "GetMonitorInfoW failed")
        dpi_x = wintypes.UINT()
        dpi_y = wintypes.UINT()
        result = shcore.GetDpiForMonitor(
            monitor,
            MDT_EFFECTIVE_DPI,
            ctypes.byref(dpi_x),
            ctypes.byref(dpi_y),
        )
        _assert(result == 0, f"GetDpiForMonitor failed for {info.szDevice}: HRESULT {result}")
        _assert(dpi_x.value == dpi_y.value, f"non-square monitor DPI for {info.szDevice}")
        rows.append(
            {
                "device": info.szDevice,
                "primary": bool(info.dwFlags & MONITORINFOF_PRIMARY),
                "physical_bounds": _rect_list(info.rcMonitor),
                "physical_work_area": _rect_list(info.rcWork),
                "dpi_x": int(dpi_x.value),
                "dpi_y": int(dpi_y.value),
                "scale_percent": round(dpi_x.value / 96 * 100),
            }
        )
        return True

    callback = callback_type(visit)
    _assert(bool(user32.EnumDisplayMonitors(0, None, callback, 0)), "EnumDisplayMonitors failed")
    return sorted(rows, key=lambda row: (not row["primary"], row["device"]))


def _validate_candidate(candidate: Path) -> dict[str, Any]:
    # The canonical build root contains build_manifest.json/smoke_test.json and
    # the onedir bundle below it.  Also accept the executable's immediate
    # directory as a convenience, but always hash the canonical manifest root.
    candidate = candidate.resolve()
    manifest_root = candidate
    if not (manifest_root / "build_manifest.json").is_file():
        manifest_root = candidate.parent
    manifest_path = manifest_root / "build_manifest.json"
    smoke_path = manifest_root / "smoke_test.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("candidate manifest/smoke evidence is missing or invalid") from exc
    _assert(manifest.get("schema") == "ms-event-studio-desktop-build-v2", "unexpected manifest schema")
    _assert(manifest.get("platform") == "windows", "candidate is not a Windows build")
    _assert(manifest.get("renderer") == "pywebview", "candidate renderer is not pywebview")
    _assert(manifest.get("smoke_test_exit_code") == 0, "candidate smoke exit code is not zero")
    _assert(smoke.get("status") == "ok", "candidate hidden smoke did not pass")
    _assert(smoke.get("renderer") == "pywebview" and smoke.get("hidden") is True, "smoke did not use hidden pywebview")
    _assert(smoke.get("runtime", {}).get("platform_backend") == "edgechromium", "smoke did not use EdgeChromium")
    for name in REQUIRED_SMOKE_CHECKS:
        _assert(smoke.get(name) is True, f"smoke check is missing: {name}")
    executable = manifest_root / str(manifest.get("executable", ""))
    _assert(executable.is_file(), "manifest executable is missing")
    _assert(_sha256(executable) == manifest.get("executable_sha256"), "executable SHA-256 drifted from manifest")
    config = executable.with_name(f"{APP_NAME}.exe.config")
    _assert(config.is_file(), "Windows runtime config is missing beside executable")
    root = ET.parse(config).getroot()
    policy = root.findall("./runtime/loadFromRemoteSources")
    _assert(len(policy) == 1 and policy[0].attrib.get("enabled", "").casefold() == "true", "invalid loadFromRemoteSources policy")
    embedded_manifest = _read_embedded_manifest(executable)
    _assert(
        "PerMonitorV2" in embedded_manifest and "true/pm" in embedded_manifest,
        "Windows executable does not embed PerMonitorV2 DPI awareness",
    )
    files = manifest.get("files")
    _assert(isinstance(files, list), "manifest files list is missing")
    actual_files, actual_tree_sha = _tree_manifest(manifest_root)
    _assert(actual_files == files, "candidate files/sizes/hashes drifted from build manifest")
    _assert(len(actual_files) == manifest.get("file_count"), "candidate file count drifted from manifest")
    _assert(
        sum(int(row["size_bytes"]) for row in actual_files) == manifest.get("bundle_bytes"),
        "candidate bundle byte count drifted from manifest",
    )
    _assert(actual_tree_sha == manifest.get("tree_sha256"), "candidate tree SHA-256 drifted from manifest")
    paths = [str(row.get("path", "")).replace("\\", "/").casefold() for row in files]
    _assert(any("/_internal/ms_event_studio/web/app.js" in f"/{path}" for path in paths), "packaged app.js is absent")
    _assert(any("/_internal/webview/lib/microsoft.web.webview2.winforms.dll" in f"/{path}" for path in paths), "WebView2 WinForms runtime is absent")
    forbidden = ("tkinter", "/_tkinter", "/tcl/", "tk86", "webview/platforms/qt", "webview/platforms/cef")
    bad = sorted({path for path in paths if any(marker in f"/{path}" for marker in forbidden)})
    _assert(not bad, f"candidate contains a second/legacy renderer: {bad[:5]}")
    packaged_root = executable.parent / "_internal/ms_event_studio/web"
    source_root = REPOSITORY / "src/ms_event_studio/web"
    source_assets = sorted(path for path in source_root.rglob("*") if path.is_file())
    _assert(source_assets, "source Web asset tree is empty")
    for source in source_assets:
        packaged = packaged_root / source.relative_to(source_root)
        _assert(packaged.is_file(), f"packaged Web asset is missing: {source.relative_to(source_root)}")
        _assert(
            _sha256(packaged) == _sha256(source),
            f"packaged Web asset is stale: {source.relative_to(source_root)}",
        )
    return {
        "root": manifest_root,
        "manifest_path": manifest_path,
        "smoke_path": smoke_path,
        "manifest": manifest,
        "smoke": smoke,
        "executable": executable,
        "config": config,
        "config_sha256": _sha256(config),
        "embedded_manifest": embedded_manifest,
        "recomputed_tree_sha256": actual_tree_sha,
        "source_web_asset_count": len(source_assets),
    }


def _read_embedded_manifest(executable: Path) -> str:
    """Extract RT_MANIFEST #1 through the Win32 resource API."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    kernel32.LoadLibraryExW.restype = wintypes.HMODULE
    kernel32.FindResourceW.argtypes = [wintypes.HMODULE, wintypes.LPCWSTR, wintypes.LPCWSTR]
    kernel32.FindResourceW.restype = wintypes.HANDLE
    kernel32.LoadResource.argtypes = [wintypes.HMODULE, wintypes.HANDLE]
    kernel32.LoadResource.restype = wintypes.HANDLE
    kernel32.LockResource.argtypes = [wintypes.HANDLE]
    kernel32.LockResource.restype = ctypes.c_void_p
    kernel32.SizeofResource.argtypes = [wintypes.HMODULE, wintypes.HANDLE]
    kernel32.SizeofResource.restype = wintypes.DWORD
    kernel32.FreeLibrary.argtypes = [wintypes.HMODULE]
    kernel32.FreeLibrary.restype = wintypes.BOOL
    load_library_as_datafile = 0x00000002
    module = kernel32.LoadLibraryExW(str(executable), None, load_library_as_datafile)
    if not module:
        raise OSError(ctypes.get_last_error(), f"cannot load executable resources: {executable}")
    try:
        resource = kernel32.FindResourceW(module, ctypes.cast(1, wintypes.LPCWSTR), ctypes.cast(24, wintypes.LPCWSTR))
        if not resource:
            raise OSError(ctypes.get_last_error(), "executable has no embedded application manifest")
        loaded = kernel32.LoadResource(module, resource)
        address = kernel32.LockResource(loaded)
        size = int(kernel32.SizeofResource(module, resource))
        _assert(bool(address) and size > 0, "embedded manifest resource is empty")
        raw = ctypes.string_at(address, size)
        return raw.decode("utf-8-sig")
    finally:
        kernel32.FreeLibrary(module)


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


def _json_url(url: str, *, timeout: float = 1.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def _main_window(pid: int, *, timeout: float = 30.0) -> tuple[int, list[dict[str, Any]]]:
    import win32gui
    import win32process

    deadline = time.monotonic() + timeout
    last: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        rows: list[dict[str, Any]] = []

        def visit(hwnd: int, _data: Any) -> None:
            _, owner = win32process.GetWindowThreadProcessId(hwnd)
            if owner != pid:
                return
            rows.append(
                {
                    "hwnd": int(hwnd),
                    "title": win32gui.GetWindowText(hwnd),
                    "class_name": win32gui.GetClassName(hwnd),
                    "visible": bool(win32gui.IsWindowVisible(hwnd)),
                    "rect": list(win32gui.GetWindowRect(hwnd)),
                }
            )

        win32gui.EnumWindows(visit, None)
        named = [row for row in rows if row["visible"] and row["title"] == WINDOW_TITLE]
        if len(named) == 1:
            return int(named[0]["hwnd"]), rows
        last = rows
        time.sleep(0.05)
    raise RuntimeError(f"expected one visible {WINDOW_TITLE!r} window for PID {pid}; found {last}")


def _child_classes(hwnd: int) -> list[str]:
    import win32gui

    rows: list[str] = []

    def visit(child: int, _data: Any) -> None:
        rows.append(win32gui.GetClassName(child))

    win32gui.EnumChildWindows(hwnd, visit, None)
    return sorted(set(rows))


def _move_window(hwnd: int, monitor: dict[str, Any]) -> dict[str, Any]:
    import win32gui

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dpi = int(monitor["dpi_x"])
    width = round(MINIMUM_WINDOW_LOGICAL[0] * dpi / 96)
    height = round(MINIMUM_WINDOW_LOGICAL[1] * dpi / 96)
    left, top, right, bottom = monitor["physical_work_area"]
    _assert(
        width <= right - left and height <= bottom - top,
        f"960x640 logical minimum does not fit {monitor['device']}",
    )
    x = left + ((right - left) - width) // 2
    y = top + ((bottom - top) - height) // 2
    user32.ShowWindow(hwnd, SW_RESTORE)
    # First move only a small anchor rect wholly inside the target monitor.
    # WM_DPICHANGED is asynchronous and WinForms applies Windows' suggested
    # rectangle when the effective monitor changes.  Applying the final size
    # before that transition races the suggested rectangle and can escape the
    # target work area even though the product switched DPI correctly.
    anchor_width = min(width, max(320, (right - left) // 2))
    anchor_height = min(height, max(240, (bottom - top) // 2))
    _assert(
        bool(
            user32.SetWindowPos(
                hwnd,
                0,
                left + 24,
                top + 24,
                anchor_width,
                anchor_height,
                SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
        ),
        f"SetWindowPos anchor failed for {monitor['device']}",
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        actual_dpi = int(user32.GetDpiForWindow(hwnd))
        if actual_dpi == dpi:
            break
        time.sleep(0.05)
    actual_dpi = int(user32.GetDpiForWindow(hwnd))
    _assert(actual_dpi == dpi, f"window DPI {actual_dpi} does not match monitor DPI {dpi}")
    # The host's DpiChanged handler runs on the WinForms UI thread.  Give that
    # handler time to replace pywebview's startup-monitor MinimumSize before
    # requesting the exact logical minimum on the destination monitor.
    time.sleep(0.15)
    # Now that the native window and WebView have processed WM_DPICHANGED,
    # apply the stable evidence rectangle in target-monitor physical pixels.
    _assert(
        bool(user32.SetWindowPos(hwnd, 0, x, y, width, height, SWP_NOACTIVATE | SWP_SHOWWINDOW)),
        f"SetWindowPos final rect failed for {monitor['device']}",
    )
    # Let WinForms apply MinimumSize.  A larger result is a product regression:
    # it means the startup monitor's physical minimum survived WM_DPICHANGED.
    time.sleep(0.35)
    rect = list(win32gui.GetWindowRect(hwnd))
    actual_width = rect[2] - rect[0]
    actual_height = rect[3] - rect[1]
    _assert(
        abs(actual_width - width) <= 2 and abs(actual_height - height) <= 2,
        (
            f"logical outer minimum drifted on {monitor['device']}: "
            f"expected {width}x{height} physical at {dpi} DPI, "
            f"got {actual_width}x{actual_height}"
        ),
    )
    _assert(
        actual_width <= right - left and actual_height <= bottom - top,
        f"native minimum window {actual_width}x{actual_height} does not fit {monitor['device']}",
    )
    # Recenter the verified boundary without changing its physical dimensions.
    stable_x = left + ((right - left) - actual_width) // 2
    stable_y = top + ((bottom - top) - actual_height) // 2
    _assert(
        bool(
            user32.SetWindowPos(
                hwnd,
                0,
                stable_x,
                stable_y,
                actual_width,
                actual_height,
                SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
        ),
        f"SetWindowPos centered rect failed for {monitor['device']}",
    )
    time.sleep(0.15)
    rect = list(win32gui.GetWindowRect(hwnd))
    client_left, client_top = win32gui.ClientToScreen(hwnd, (0, 0))
    client = win32gui.GetClientRect(hwnd)
    client_rect = [
        int(client_left),
        int(client_top),
        int(client_left + client[2]),
        int(client_top + client[3]),
    ]
    _assert(rect[0] >= left and rect[1] >= top and rect[2] <= right and rect[3] <= bottom, "window escapes monitor work area")
    return {
        "window_rect_physical": rect,
        "client_rect_physical": client_rect,
        "window_dpi": actual_dpi,
        "window_scale_percent": round(actual_dpi / 96 * 100),
        "requested_outer_logical": list(MINIMUM_WINDOW_LOGICAL),
        "requested_window_physical": [width, height],
        "actual_window_physical": [rect[2] - rect[0], rect[3] - rect[1]],
        "logical_outer_minimum_preserved": True,
    }


def _wait_frontend(page: Any, fixture: str | None, timeout: float = 20.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            state = page.evaluate("window.__MS_EVENT_STUDIO__?.getState?.() ?? null")
        except BaseException:
            state = None
        if isinstance(state, dict) and state.get("ready") is True and state.get("fixture") == fixture:
            return state
        time.sleep(0.03)
    raise RuntimeError(f"frontend did not reach fixture {fixture!r}")


def _navigate_state(page: Any, state_id: str) -> dict[str, Any]:
    _assert(state_id in ALLOWED_STATES, f"unsupported native QA state: {state_id}")
    fixture = None if state_id == "welcome" else state_id
    page.evaluate(
        """fixture => {
          const url = new URL(location.href);
          if (fixture) url.searchParams.set('fixture', fixture);
          else url.searchParams.delete('fixture');
          location.href = url.toString();
        }""",
        fixture,
    )
    return _wait_frontend(page, fixture)


def _audit_required_actions(page: Any, state_id: str) -> list[dict[str, Any]]:
    """Prove representative actions remain keyboard-reachable at native min."""

    rows = page.evaluate(
        """selectors => selectors.map(selector => {
          const element = document.querySelector(selector);
          if (!element) return {selector, exists: false};
          element.focus({preventScroll: false});
          const rect = element.getBoundingClientRect();
          const style = getComputedStyle(element);
          return {
            selector,
            exists: true,
            disabled: Boolean(element.disabled),
            hidden: Boolean(element.hidden),
            display: style.display,
            visibility: style.visibility,
            active: document.activeElement === element,
            rect: {left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom},
            horizontallyReachable: rect.left >= -1 && rect.right <= innerWidth + 1,
            verticallyReachable: rect.top >= -1 && rect.bottom <= innerHeight + 1
          };
        })""",
        list(REQUIRED_STATE_ACTIONS[state_id]),
    )
    for row in rows:
        selector = row["selector"]
        _assert(row.get("exists"), f"native {state_id} is missing required action {selector}")
        _assert(not row["disabled"], f"native {state_id} required action {selector} is disabled")
        _assert(not row["hidden"], f"native {state_id} required action {selector} is hidden")
        _assert(row["display"] != "none" and row["visibility"] != "hidden", f"native {state_id} action {selector} is not visible")
        _assert(row["active"], f"native {state_id} action {selector} cannot receive keyboard focus")
        _assert(row["horizontallyReachable"], f"native {state_id} action {selector} is outside the horizontal viewport")
        _assert(row["verticallyReachable"], f"native {state_id} action {selector} cannot be scrolled into the viewport")
    return rows


def _capture_window(hwnd: int, destination: Path) -> dict[str, Any]:
    from PIL import ImageGrab
    import win32gui

    rect = tuple(win32gui.GetWindowRect(hwnd))
    image = ImageGrab.grab(bbox=rect, all_screens=True)
    _assert(image.size == (rect[2] - rect[0], rect[3] - rect[1]), "native screenshot dimensions drifted")
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG", optimize=False)
    return {
        "path": destination.name,
        "pixel_width": image.width,
        "pixel_height": image.height,
        "sha256": _sha256(destination),
    }


def capture(
    *,
    candidate: Path,
    output: Path,
    requested_scales: Iterable[int],
    states: Iterable[str],
) -> dict[str, Any]:
    _set_native_dpi_awareness()
    validated = _validate_candidate(candidate)
    monitors = _enumerate_monitors()
    selected: list[dict[str, Any]] = []
    for scale in requested_scales:
        matches = [row for row in monitors if row["scale_percent"] == scale]
        _assert(matches, f"no physical Windows monitor is currently configured at {scale}%")
        selected.append(matches[0])
    state_ids = tuple(states)
    _assert(state_ids and len(set(state_ids)) == len(state_ids), "native states must be non-empty and unique")
    for state_id in state_ids:
        _assert(state_id in ALLOWED_STATES, f"unsupported native QA state: {state_id}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for the WebView2 CDP evidence driver") from exc

    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = _utc_now()
    port = _free_loopback_port()
    config_root = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    environment = os.environ.copy()
    environment["MS_EVENT_STUDIO_CONFIG_DIR"] = config_root.name
    environment["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        f"--remote-debugging-port={port} --remote-allow-origins=http://127.0.0.1:{port}"
    )
    process = subprocess.Popen(
        [str(validated["executable"])],
        cwd=validated["executable"].parent,
        env=environment,
    )
    rows: list[dict[str, Any]] = []
    top_windows: list[dict[str, Any]] = []
    child_classes: list[str] = []
    browser_version: dict[str, Any] = {}
    hwnd = 0
    try:
        hwnd, top_windows = _main_window(process.pid)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"packaged executable exited before CDP attach: {process.returncode}")
            try:
                browser_version = _json_url(f"http://127.0.0.1:{port}/json/version")
                if browser_version.get("webSocketDebuggerUrl"):
                    break
            except BaseException:
                pass
            time.sleep(0.05)
        _assert(browser_version.get("webSocketDebuggerUrl"), "WebView2 CDP endpoint did not become ready")
        child_classes = _child_classes(hwnd)
        _assert(any("chrome" in name.casefold() for name in child_classes), "native window has no Edge/Chromium child")
        _assert(not any(name.casefold().startswith(("tk", "qt")) for name in child_classes), "native window contains a second renderer")

        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            try:
                deadline = time.monotonic() + 30
                page = None
                while time.monotonic() < deadline:
                    pages = [page for context in browser.contexts for page in context.pages]
                    ready = [candidate for candidate in pages if candidate.url.startswith("http://127.0.0.1:")]
                    if ready:
                        page = ready[0]
                        break
                    time.sleep(0.05)
                _assert(page is not None, "packaged WebView page did not reach loopback URL")
                _wait_frontend(page, None)
                for monitor in selected:
                    native = _move_window(hwnd, monitor)
                    for state_id in state_ids:
                        frontend = _navigate_state(page, state_id)
                        # Allow native compositor/layout and font rasterization to settle.
                        time.sleep(0.20)
                        metrics = page.evaluate(
                            """() => ({
                              innerWidth, innerHeight, devicePixelRatio,
                              scrollWidth: document.documentElement.scrollWidth,
                              clientWidth: document.documentElement.clientWidth,
                              horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
                              fixture: window.__MS_EVENT_STUDIO__.getState().fixture,
                              view: window.__MS_EVENT_STUDIO__.getState().view,
                              modal: window.__MS_EVENT_STUDIO__.getState().modal
                            })"""
                        )
                        expected_dpr = native["window_dpi"] / 96
                        _assert(
                            abs(float(metrics["devicePixelRatio"]) - expected_dpr) <= 0.01,
                            f"WebView DPR {metrics['devicePixelRatio']} does not match native DPI {native['window_dpi']}",
                        )
                        client_width = native["client_rect_physical"][2] - native["client_rect_physical"][0]
                        client_height = native["client_rect_physical"][3] - native["client_rect_physical"][1]
                        _assert(
                            abs(float(metrics["innerWidth"]) * expected_dpr - client_width) <= 3,
                            "WebView CSS width does not match its native client width/DPI",
                        )
                        _assert(
                            abs(float(metrics["innerHeight"]) * expected_dpr - client_height) <= 3,
                            "WebView CSS height does not match its native client height/DPI",
                        )
                        _assert(
                            all(
                                abs(actual / expected_dpr - logical) <= 2
                                for actual, logical in zip(
                                    native["actual_window_physical"],
                                    MINIMUM_WINDOW_LOGICAL,
                                    strict=True,
                                )
                            ),
                            "native outer window did not preserve the 960x640 logical minimum",
                        )
                        _assert(not metrics["horizontalOverflow"], f"native {state_id}@{monitor['scale_percent']}% has horizontal overflow")
                        screenshot = _capture_window(
                            hwnd,
                            output / f"{state_id}--windows-native-{monitor['scale_percent']}pct.png",
                        )
                        actions = _audit_required_actions(page, state_id)
                        rows.append(
                            {
                                "state": state_id,
                                "monitor": monitor,
                                **native,
                                "webview": metrics,
                                "frontend": {
                                    "ready": frontend.get("ready"),
                                    "fixture": frontend.get("fixture"),
                                    "view": frontend.get("view"),
                                    "modal": frontend.get("modal"),
                                },
                                "required_actions": actions,
                                "screenshot": screenshot,
                            }
                        )
            finally:
                browser.close()
    finally:
        if hwnd:
            try:
                ctypes.WinDLL("user32", use_last_error=True).PostMessageW(hwnd, WM_CLOSE, 0, 0)
            except BaseException:
                pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # This PID was created by this evidence run; never target pre-existing
            # MS Event Studio processes.
            process.terminate()
            process.wait(timeout=5)
        config_root.cleanup()

    _assert(process.returncode == 0, f"packaged executable exited with {process.returncode}")
    report = {
        "schema": "ms-event-studio-windows-native-qa-v1",
        "started_at": started,
        "finished_at": _utc_now(),
        "status": "ok",
        "native_dpi_evidence": True,
        "browser_emulation": False,
        "capture_source": "physical-desktop-pixels",
        "window_boundary": {
            "kind": "pywebview-logical-outer-minimum",
            "logical_width": MINIMUM_WINDOW_LOGICAL[0],
            "logical_height": MINIMUM_WINDOW_LOGICAL[1],
            "client_viewport_is_smaller_due_to_native_chrome": True,
        },
        "candidate": {
            "application_version": validated["manifest"].get("application_version"),
            "executable_sha256": validated["manifest"].get("executable_sha256"),
            "tree_sha256": validated["manifest"].get("tree_sha256"),
            "file_count": validated["manifest"].get("file_count"),
            "bundle_bytes": validated["manifest"].get("bundle_bytes"),
            "config_sha256": validated["config_sha256"],
            "embedded_manifest_per_monitor_v2": "PerMonitorV2" in validated["embedded_manifest"],
            "manifest_tree_recomputed": validated["recomputed_tree_sha256"],
            "source_web_assets_byte_identical": True,
            "source_web_asset_count": validated["source_web_asset_count"],
            "smoke_status": validated["smoke"].get("status"),
            "smoke_renderer": validated["smoke"].get("renderer"),
            "smoke_runtime": validated["smoke"].get("runtime"),
        },
        "runtime": {
            "process_exit_code": process.returncode,
            "edge_cdp_browser": browser_version.get("Browser"),
            "edge_cdp_protocol": browser_version.get("Protocol-Version"),
            "named_main_windows": sum(1 for row in top_windows if row["title"] == WINDOW_TITLE),
            "top_level_windows": top_windows,
            "child_window_classes": child_classes,
            "renderer": "EdgeChromium/WebView2",
            "legacy_or_second_renderer_detected": False,
        },
        "available_monitors": monitors,
        "requested_scales": list(requested_scales),
        "captured_states": list(state_ids),
        "rows": rows,
        "missing_native_scales": sorted({100, 125, 150, 200} - {row["window_scale_percent"] for row in rows}),
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=REPOSITORY / "build/qa/windows-native")
    parser.add_argument("--scale", type=int, action="append", choices=(100, 125, 150, 200))
    parser.add_argument("--state", action="append", choices=sorted(ALLOWED_STATES))
    args = parser.parse_args(argv)
    scales = tuple(args.scale or (100, 150))
    states = tuple(args.state or DEFAULT_STATES)
    report = capture(candidate=args.candidate, output=args.output, requested_scales=scales, states=states)
    print(
        json.dumps(
            {
                "status": report["status"],
                "rows": len(report["rows"]),
                "scales": report["requested_scales"],
                "missing_native_scales": report["missing_native_scales"],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
