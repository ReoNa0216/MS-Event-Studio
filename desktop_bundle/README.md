# Native desktop builds

PyInstaller must run on the target operating system; it is not a
cross-compiler. The historical `0.2.0.dev3` build uses a windowed `onedir`
bundle containing Tk, NumPy, SciPy, pandas, and PyArrow. It passed scientific
and packaged-smoke regression, but its UX was rejected. Do not treat the
commands below as proof that a Phase 2R WebView candidate is accepted; follow
[`../docs/MS_EVENT_STUDIO_UI_REBUILD_HANDOFF.md`](../docs/MS_EVENT_STUDIO_UI_REBUILD_HANDOFF.md)
for the required pywebview migration and release gates.

```powershell
python -m pip install -e ".[packaging]"
python -m desktop_bundle.build_desktop
```

To run the same test/build/archive path as CI on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File desktop_bundle/build_windows.ps1 `
  -Version 0.2.0-dev3
```

Without `-PythonExe`, the local script creates and reuses an ignored interpreter
under `build/venv/windows`; it does not install or upgrade packages in the
system/base Python. CI may pass its disposable runner interpreter explicitly.

On an Apple Silicon Mac:

```bash
MS_EVENT_STUDIO_VERSION=0.2.0-dev3 bash desktop_bundle/build_macos.sh
```

The macOS script likewise defaults to `build/venv/macos`; `PYTHON_BIN` is an
explicit override intended for disposable or already-isolated environments.

Windows produces `dist/windows/MS-Event-Studio/MS-Event-Studio.exe`.
Running the same command on macOS produces
`dist/macos/MS-Event-Studio.app`. Each build runs `--smoke-test` and writes a
complete file/hash manifest beside the candidate. The smoke test must load the
native UI plus NumPy/SciPy detection, Parquet, SQLite, display cache, and both
export contracts; a window-only import is not sufficient.

If an older Windows candidate is intentionally still open, do not terminate it
or overwrite its bundle. Build beside it with, for example,
`python -m desktop_bundle.build_desktop --dist-root dist/windows-side-by-side`.

The committed transparent master PNG produces runtime icons and a native
Windows `.ico` or macOS `.icns`. The Phase 2R WebView shell must retain the same
MS identity while packaging its HTML/CSS/JS/SVG assets.

The historical Windows entry point enables Per-Monitor V2 DPI awareness before
Tk creates its first window. Phase 2R must verify native WebView rendering at
100%, 125%, 150%, and 200% rather than relying on that Tk-only check.

The historical dev3 Tk bundle intentionally has no
`MS-Event-Studio.exe.config`: it does not load CLR, so that absence is correct.
This rule must change with Phase 2R. Once the entry point uses
pywebview/pythonnet/CLR/Edge WebView2, the Windows build must copy a validated
`MS-Event-Studio.exe.config` beside the executable with
`loadFromRemoteSources` enabled, and the build/smoke tests must fail if the file
is absent or incorrect.

The build manifest records application/Python/PyInstaller versions, executable
hash, complete bundle tree hash, file sizes/hashes, and the smoke payload.
`dist/` and `release/` are ignored. `dist/` is mutable test output; `release/`
contains only validated ZIP archives and their SHA-256 sidecars. Keep every
file in an `onedir` candidate together.

PyInstaller is not a cross-compiler: a Windows success does not satisfy the
macOS gate. The macOS CI candidate is ad-hoc signed and checked with `codesign`,
`plutil`, and `file`; Apple Developer ID signing and notarization remain a later
release operation. `.github/workflows/release-desktop.yml` retains the same
native-runner and manual-candidate/tag-release policy as LMA Studio, but its
dependencies, spec, and smoke path must be upgraded for WebView before the next
candidate is built.
