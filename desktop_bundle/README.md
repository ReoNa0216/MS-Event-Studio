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
  -Version 0.2.0-dev1
```

On an Apple Silicon Mac:

```bash
MS_EVENT_STUDIO_VERSION=0.2.0-dev1 bash desktop_bundle/build_macos.sh
```

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
