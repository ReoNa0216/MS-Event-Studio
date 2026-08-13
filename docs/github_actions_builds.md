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

The private repository is connected at
[`ReoNa0216/MS-Event-Studio`](https://github.com/ReoNa0216/MS-Event-Studio), and
local `main` tracks `origin/main`. The first unpublished macOS audit ran from
commit `ff91fa9821423f305335549fafa4b9cbae437078` as
[Actions run 31675795071](https://github.com/ReoNa0216/MS-Event-Studio/actions/runs/31675795071).
It completed 152 tests, built the ARM64 `.app`, launched the signed package with
the Cocoa backend, passed the DOM/API/scientific smoke, verified the final
signature and bundle manifest, and uploaded the ZIP plus SHA-256 sidecar. It did
not create a prerelease or stable tag.

For another audit candidate, open GitHub **Actions → Build and release desktop
packages → Run workflow**:

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

Current evidence covers the complete 36×3 browser matrix and the exact final R8
Windows candidate at physical 100%, 125%, 150%, and 200% native DPI. The local
display was restored to its original 150% after the additional captures.
`qa/screenshot_matrix.json` now leaves only macOS Retina as `planned`: the
successful Actions build and Cocoa hidden smoke prove package execution, but do
not replace visible Retina screenshots or mouse UAT on Apple Silicon.
