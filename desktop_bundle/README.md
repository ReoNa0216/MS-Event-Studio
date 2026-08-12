# Native desktop candidates

PyInstaller must run on the target operating system; it is not a
cross-compiler. Both supported platforms use a windowed `onedir` bundle so the
Tk, NumPy, SciPy, pandas, and PyArrow runtime is inspectable and does not unpack
into a temporary directory on every launch.

```powershell
python -m pip install -e ".[packaging]"
python -m desktop_bundle.build_desktop
```

Windows produces `release/windows/MS-Event-Studio/MS-Event-Studio.exe`.
Running the same command on macOS produces
`release/macos/MS-Event-Studio.app`. Each build runs `--smoke-test` and writes a
complete file/hash manifest beside the candidate. The smoke test must load the
native UI plus NumPy/SciPy detection, Parquet, SQLite, display cache, and both
export contracts; a window-only import is not sufficient.

The build manifest records application/Python/PyInstaller versions, executable
hash, complete bundle tree hash, file sizes/hashes, and the smoke payload.
`release/` is ignored and candidates are not committed. Keep every file in an
`onedir` candidate together.

PyInstaller is not a cross-compiler: a Windows success does not satisfy the
macOS gate. Code signing and notarization are separate release operations and
are intentionally not impersonated by this local development build.
