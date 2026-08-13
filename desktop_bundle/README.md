# Native desktop builds

PyInstaller must run on the target operating system; it is not a
cross-compiler. The `0.3.0.dev1` application builds one pywebview renderer from
the platform specs under `packaging/windows/` and `packaging/macos/`. The
historical `0.2.0.dev3` Tk package is regression evidence only; its legacy UI
source has been removed. The production source path/entry point cannot import it,
and a final candidate containing Tk/Tcl or another renderer is rejected. Do not
treat the commands below as proof that a Phase 2R WebView candidate is accepted; follow
[`../docs/MS_EVENT_STUDIO_UI_REBUILD_HANDOFF.md`](../docs/MS_EVENT_STUDIO_UI_REBUILD_HANDOFF.md)
for the required pywebview migration and release gates.

```powershell
python -m pip install -e ".[packaging]"
python scripts/capture_ui_matrix.py --validate-only
python -m desktop_bundle.build_desktop
```

To run the same test/build/archive path as CI on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File desktop_bundle/build_windows.ps1 `
  -Version 0.3.0-dev1
```

Without `-PythonExe`, the local script creates and reuses an ignored interpreter
under `build/venv/windows`; it does not install or upgrade packages in the
system/base Python. CI may pass its disposable runner interpreter explicitly.

On an Apple Silicon Mac:

```bash
MS_EVENT_STUDIO_VERSION=0.3.0-dev1 bash desktop_bundle/build_macos.sh
```

The macOS script likewise defaults to `build/venv/macos`; `PYTHON_BIN` is an
explicit override intended for disposable or already-isolated environments.

Windows produces `dist/windows/MS-Event-Studio/MS-Event-Studio.exe`.
Running the same command on macOS produces
`dist/macos/MS-Event-Studio.app`. Each build runs the hidden
`--webview-smoke` executable probe and writes a complete file/hash manifest
beside the candidate. The probe must load the HTML page in a real hidden native
WebView, await the read-only frontend readiness hook, call health/bootstrap
APIs, and complete the NumPy/SciPy, Parquet, SQLite, display-cache, and export
round trip. An import-only probe is rejected by the report validator.
The smoke `application_version` must equal the PEP 440 package version in
`pyproject.toml` (`0.3.0.dev1`); `0.3.0-dev1` is only the filesystem-safe
archive/workflow label. Windows smoke must report `edgechromium`; macOS smoke
must report `cocoa`.

If an older Windows candidate is intentionally still open, do not terminate it
or overwrite its bundle. Build beside it with, for example,
`python -m desktop_bundle.build_desktop --dist-root dist/windows-side-by-side`.

The committed transparent master PNG produces runtime icons and a native
Windows `.ico` or macOS `.icns`. The Phase 2R WebView shell must retain the same
MS identity while packaging its HTML/CSS/JS/SVG assets.

The standard matrix is declared in `qa/screenshot_matrix.json`. Browser rows
use 960×640, 1366×768, and 1920×1080; Windows 100/125/150/200% and macOS Retina
are separate native evidence and cannot be substituted by browser scaling.
`python scripts/capture_ui_matrix.py --require-all ...` intentionally refuses
to pass while any scenario or native sample remains planned.

The Windows WebView2/pythonnet bundle copies the committed
`packaging/windows/MS-Event-Studio.exe.config` next to the executable. Both the
Python build path and PowerShell archive path parse it and require exactly one
`loadFromRemoteSources enabled="true"` policy. The ZIP check also requires the
sidecar entry.

The build manifest records application/Python/PyInstaller versions, executable
hash, complete bundle tree hash, file sizes/hashes, and the smoke payload.
Before writing it, the finalizer requires the HTML/CSS/JS assets, checks the
target WebView backend, and scans the actual candidate tree for Tk/Tcl,
CEF/Qt, Android and legacy WebBrowser payloads. On Windows it additionally
requires the x64 WebView2 Core, WinForms and loader files. Both specs explicitly
exclude the legacy `ms_event_studio.desktop`/`theme` modules and non-target
pywebview backends.
`dist/` and `release/` are ignored. `dist/` contains only the current canonical
platform candidate; named intermediate candidates belong under `build/` and
must be removed or archived before handoff. `release/` contains only validated
ZIP archives and their SHA-256 sidecars. Keep every file in an `onedir`
candidate together.

PyInstaller is not a cross-compiler: a Windows success does not satisfy the
macOS gate. The macOS CI candidate is ad-hoc signed, checked with `codesign`,
`plutil`, and `file`, then smoke-tested again after signing. Apple Developer ID
signing and notarization remain later release operations. The workflow keeps
the native-runner and opt-in candidate policy used by LMA Studio.

The final R8 Windows candidate passed packaged smoke and native 100%, 125%,
150%, and 200% DPI capture on physical Windows displays; every sample retained
the logical 960×640 outer-window minimum, reachable actions, and zero horizontal
overflow. The first unpublished macOS Actions audit also built the ARM64 app and
passed its signed Cocoa hidden-WebView/API/scientific smoke. macOS Retina visual
and mouse UAT still require an Apple Silicon Mac; CSS scaling and the hidden
smoke are not substitutes for that final native sample.
