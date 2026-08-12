# -*- mode: python ; coding: utf-8 -*-

"""Windows x64 onedir bundle for the Phase 2R WebView renderer."""

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)


repo_root = Path(SPECPATH).parents[1]
source_root = repo_root / "src"


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
# pywebview publishes every platform/architecture in its shared data tree.
# This is an x64 Edge Chromium candidate: do not ship the Android backend or
# WebView2 loaders for other Windows architectures.
datas = [
    entry
    for entry in datas
    if "pywebview-android.jar" not in str(entry[0]).replace("\\", "/").casefold()
    and "webbrowserinterop." not in str(entry[0]).replace("\\", "/").casefold()
    and "/runtimes/win-arm64/" not in str(entry[0]).replace("\\", "/").casefold()
    and "/runtimes/win-x86/" not in str(entry[0]).replace("\\", "/").casefold()
]

binaries = []
binaries += collect_dynamic_libs("pyarrow")
binaries += collect_dynamic_libs("scipy")
binaries += collect_dynamic_libs("webview")
binaries = [
    entry
    for entry in binaries
    if "/runtimes/win-arm64/" not in str(entry[0]).replace("\\", "/").casefold()
    and "/runtimes/win-x86/" not in str(entry[0]).replace("\\", "/").casefold()
    and "webbrowserinterop." not in str(entry[0]).replace("\\", "/").casefold()
]

hiddenimports = []
hiddenimports += collect_submodules("pyarrow", filter=production_submodule)
hiddenimports += collect_submodules("scipy", filter=production_submodule)
hiddenimports += [
    "clr",
    "pythonnet",
    "pandas._libs.tslibs.timedeltas",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
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
        "webview.platforms.cocoa",
        "webview.platforms.gtk",
        "webview.platforms.mshtml",
        "webview.platforms.qt",
    ],
    noarchive=False,
    optimize=0,
)


def windows_production_payload(entry):
    source = str(entry[0]).replace("\\", "/").casefold()
    destination = str(entry[1]).replace("\\", "/").casefold()
    combined = f"{source}/{destination}"
    return not any(
        marker in combined
        for marker in (
            "pywebview-android.jar",
            "webbrowserinterop.",
            "/runtimes/win-arm64/",
            "/runtimes/win-x86/",
        )
    )


# The contributed hook-webview.py recollects all shared lib files during
# Analysis.  Filter Analysis results as well as our explicit inputs so Android,
# MSHTML and other-architecture payloads cannot be reintroduced by that hook.
a.datas = [entry for entry in a.datas if windows_production_payload(entry)]
a.binaries = [entry for entry in a.binaries if windows_production_payload(entry)]
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
    upx=True,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(repo_root / "build/icons/MS-Event-Studio.ico"),
    manifest=str(repo_root / "packaging/windows/MS-Event-Studio.manifest"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="MS-Event-Studio",
)
