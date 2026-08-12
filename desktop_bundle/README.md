# Native desktop candidates

PyInstaller must run on the target operating system; it is not a
cross-compiler. Both supported platforms use a windowed `onedir` bundle so the
Tk, NumPy, SciPy, pandas, and PyArrow runtime is inspectable and does not unpack
into a temporary directory on every launch.

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

The committed transparent master PNG produces runtime Tk icons and a native
Windows `.ico` or macOS `.icns`. PyInstaller embeds the platform icon and the
packaged PNG set, so source and frozen windows share the same identity.

The Windows entry point enables Per-Monitor V2 DPI awareness before Tk creates
its first window. This prevents Windows display scaling from bitmap-stretching
the interface and keeps text and borders sharp on 125–200% displays.

Unlike LMA Studio, this bundle intentionally has no `MS-Event-Studio.exe.config`.
LMA Studio's 142-byte config enables .NET `loadFromRemoteSources` for its
pywebview/pythonnet/CLR/Edge WebView2 host. MS Event Studio uses native Tk and
does not load CLR assemblies, so copying that config would have no effect and
would imply a runtime dependency that does not exist.

The build manifest records application/Python/PyInstaller versions, executable
hash, complete bundle tree hash, file sizes/hashes, and the smoke payload.
`dist/` and `release/` are ignored. `dist/` is mutable test output; `release/`
contains only validated ZIP archives and their SHA-256 sidecars. Keep every
file in an `onedir` candidate together.

PyInstaller is not a cross-compiler: a Windows success does not satisfy the
macOS gate. The macOS CI candidate is ad-hoc signed and checked with `codesign`,
`plutil`, and `file`; Apple Developer ID signing and notarization remain a later
release operation. `.github/workflows/release-desktop.yml` runs both native
paths with the same manual-candidate/tag-release policy as LMA Studio.
