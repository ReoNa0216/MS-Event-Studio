# GitHub Actions desktop builds

Status on 2026-08-13 (UX-R8): the `0.3.0.dev1` source uses one production
pywebview renderer. The native-runner path pins pywebview 6.2.1, uses platform
WebView specs, copies and validates the Windows `.exe.config`, bundles
HTML/CSS/JS/SVG assets, rejects Tk/Tcl and alternate renderer files, and
requires an executable hidden-WebView/API/scientific smoke report whose version
matches `pyproject.toml`. These checked-in guards are not evidence that a new
native candidate has passed until the exact archived bytes have their own
smoke, hash, native screenshots and independent review. See
[`MS_EVENT_STUDIO_UI_REBUILD_HANDOFF.md`](MS_EVENT_STUDIO_UI_REBUILD_HANDOFF.md).

The workflow mirrors LMA Studio's native-runner policy:

- manual runs default to a macOS ARM64 candidate;
- `macos-14` builds and verifies the real `.app` with an ARM64 Python runtime;
- `windows-2022` builds the x64 onedir bundle;
- both platforms run the full unit suite, screenshot-matrix schema gate, and
  hidden WebView/API/scientific packaged smoke before archiving;
- artifacts include a ZIP and a SHA-256 sidecar;
- a `v*` tag builds both platforms and publishes a stable GitHub Release;
- a manual run publishes nothing unless `publish_prerelease` is explicitly
  enabled.

The macOS candidate is ad-hoc signed for bundle integrity but is not Apple
notarized. The `0.3.0.dev1` candidate must receive packaged smoke, the standard
screenshot/agent pre-UAT gate, and mouse UAT on a real Apple Silicon Mac before
Phase 2R exit. No local Windows build or browser proxy can satisfy that gate.

## First remote build

This local repository currently has no Git remote. After its GitHub repository
has been created, connect and push `main`:

```powershell
git remote add origin <YOUR_GITHUB_REPOSITORY_URL>
git push -u origin main
```

Do not run the workflow as a new UX candidate until the Phase 2R packaging
upgrade is committed. After that gate, open GitHub **Actions → Build and release
desktop packages → Run workflow**:

1. choose `macos`;
2. use the filesystem-safe WebView candidate label recorded by Phase 2R
   (`0.3.0-dev1`; the Python package version remains `0.3.0.dev1`);
3. leave `publish_prerelease` off for the first audit;
4. download the `ms-event-studio-macos-arm64` artifact after the run succeeds;
5. verify the ZIP next to its sidecar with `shasum -a 256 -c <file>.sha256`;
6. unzip it on an Apple Silicon Mac and execute the Phase 2R packaged smoke and
   current Chinese UAT guide; do not reuse the dev3 legacy checklist as UX
   acceptance.

Use a `v*` tag only after both native candidates and mouse UAT are accepted.

Current local evidence covers the complete 36×3 browser matrix and physical
Windows 100%/150% native DPI for the earlier R7 pre-candidate. Windows
125%/200% and macOS Retina are still `planned` in
`qa/screenshot_matrix.json`. The R8 version bump changes packaged bytes, so the
new Windows candidate must rerun hidden smoke, tree/hash audit and native QA;
the first remote macOS build must remain an unpublished audit candidate.
