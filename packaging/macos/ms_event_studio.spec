# -*- mode: python ; coding: utf-8 -*-

"""Apple Silicon app bundle for the Phase 2R WebView renderer."""

import os
from pathlib import Path
import re

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)


repo_root = Path(SPECPATH).parents[1]
source_root = repo_root / "src"
release_version = os.environ.get("MS_EVENT_STUDIO_VERSION", "0.3.0-dev1").lstrip("v")
version_match = re.match(r"\d+(?:\.\d+){0,2}", release_version)
bundle_version = version_match.group(0) if version_match else "0.3.0"


def production_submodule(name):
    parts = name.split(".")
    return not any(
        part in {"tests", "conftest", "benchmark"}
        or part.startswith("test_")
        or part.startswith("_test")
        for part in parts
    )


datas = [
    (str(source_root / "ms_event_studio/assets"), "ms_event_studio/assets"),
    (str(source_root / "ms_event_studio/web"), "ms_event_studio/web"),
]
datas += collect_data_files("webview", subdir="lib")
datas += collect_data_files("webview", subdir="js")
# Cocoa/WebKit is supplied by macOS.  Keep pywebview's shared JS bridge, but
# do not put Android or Windows runtime payloads inside the ARM64 app bundle.
datas = [
    entry
    for entry in datas
    if "pywebview-android.jar" not in str(entry[0]).replace("\\", "/").casefold()
    and "/runtimes/win-" not in str(entry[0]).replace("\\", "/").casefold()
    and not str(entry[0]).casefold().endswith(".dll")
]

binaries = []
binaries += collect_dynamic_libs("pyarrow")
binaries += collect_dynamic_libs("scipy")

hiddenimports = []
hiddenimports += collect_submodules("pyarrow", filter=production_submodule)
hiddenimports += collect_submodules("scipy", filter=production_submodule)
hiddenimports += [
    "AppKit",
    "Foundation",
    "Quartz",
    "Security",
    "UniformTypeIdentifiers",
    "WebKit",
    "objc",
    "pandas._libs.tslibs.timedeltas",
    "webview.platforms.cocoa",
]

a = Analysis(
    [str(repo_root / "desktop_bundle/ms_event_studio_gui.py")],
    pathex=[str(source_root), str(repo_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "IPython",
        "matplotlib",
        "notebook",
        "numba",
        "torch",
        "_tkinter",
        "idlelib",
        "ms_event_studio.desktop",
        "ms_event_studio.theme",
        "tcl",
        "tkinter",
        "turtle",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "webview.platforms.android",
        "webview.platforms.cef",
        "webview.platforms.edgechromium",
        "webview.platforms.gtk",
        "webview.platforms.mshtml",
        "webview.platforms.qt",
        "webview.platforms.win32",
        "webview.platforms.winforms",
    ],
    noarchive=False,
    optimize=0,
)


def macos_production_payload(entry):
    source = str(entry[0]).replace("\\", "/").casefold()
    destination = str(entry[1]).replace("\\", "/").casefold()
    combined = f"{source}/{destination}"
    webview_lib = "/webview/lib/" in f"/{combined}"
    return not (
        "pywebview-android.jar" in combined
        or "/runtimes/win-" in combined
        or (webview_lib and source.endswith(".dll"))
    )


# The contributed hook-webview.py recollects all shared lib files during
# Analysis.  Remove Windows/Android payloads from the final Analysis tables so
# the Cocoa/WebKit app remains a single-platform renderer bundle.
a.datas = [entry for entry in a.datas if macos_production_payload(entry)]
a.binaries = [entry for entry in a.binaries if macos_production_payload(entry)]
a.datas = [
    entry
    for entry in a.datas
    if not str(entry[0]).replace("\\", "/").startswith("pyarrow/tests/")
]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MS-Event-Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
    icon=str(repo_root / "build/icons/MS-Event-Studio.icns"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="MS-Event-Studio",
)
app = BUNDLE(
    coll,
    name="MS-Event-Studio.app",
    icon=str(repo_root / "build/icons/MS-Event-Studio.icns"),
    bundle_identifier="org.hulab.ms-event-studio",
    info_plist={
        "CFBundleDisplayName": "MS Event Studio",
        "CFBundleShortVersionString": bundle_version,
        "CFBundleVersion": bundle_version,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
    },
)
