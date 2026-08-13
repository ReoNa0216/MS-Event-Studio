# Phase 2R QA and packaging gates

This file records the executable UX-R0–R8 guardrails and current evidence
boundary. It is not a UAT pass report and does not supersede
`MS_EVENT_STUDIO_UI_REBUILD_HANDOFF.md`.

## Source gates

Run from the repository root:

```powershell
$env:PYTHONPATH = "src;tests;."
python -m unittest discover -s tests -q
python scripts/lint_ui_copy.py
python scripts/capture_ui_matrix.py --validate-only
```

The UI tests inspect the Web DOM structure, Chinese-first visible copy, named
controls, live regions, shared design colors, keyboard focus styling, reduced
motion handling, frontend readiness hook, and the standard screenshot matrix.
The copy linter deliberately reads only user-visible HTML text and accessible
names; private API fields are checked by API contract tests and are not confused
with rendered terminology.

## Screenshot evidence

`qa/screenshot_matrix.json` contains every state required by the handoff plus
the three browser viewports and native DPI/Retina samples. Capture currently
starts with rows whose `automation` is `browser`:

```powershell
python -m pip install -e ".[qa]"
playwright install chromium
python scripts/capture_ui_matrix.py
python scripts/run_ux_r1_browser_qa.py
```

With no `--base-url`, the command starts and always stops a repository-local
random-port server for the fixture captures. `--base-url` remains available to
audit an already-running source or packaged server.

The stage-specific Playwright gates under `scripts/run_ux_*.py` cover keyboard
focus, dialogs, Escape/cancel, review persistence, event editing, range/export
jobs, rollback, geometry, long Chinese copy, contrast and reflow. Fixture
states issue zero writes; real API gates use temporary projects. They write
ignored JSON reports under `build/qa/` and exit nonzero on any failed check.

The chart gates also require pointer hover/focus to leave the visible time-label
set unchanged. Peak adjustment must focus the real local curve without widening
the scientific support interval, preserve real trace samples, accept mouse and
keyboard targeting, and restore the prior viewport on cancel. Every browser
matrix row audits visible-control overlap, navigation-to-selection spacing and
toolbar-label legibility in addition to document overflow.

Generated PNGs and `report.json` default to ignored `build/qa/screenshots/`.
They are evidence for the exact source run, not committed golden assets.
`--require-all` is the pre-UAT hard gate: it fails while a browser scenario is
`planned` or a native sample is not `captured`. Browser device-scale emulation
never satisfies Windows native DPI or macOS Retina evidence.

## Native bundle contract

- Both native requirements files pin `pywebview==6.2.1`.
- Both PyInstaller specs collect the package Web assets plus pywebview `lib/js`
  data and only the target platform backend.
- The production entry point is `ms_event_studio.web_desktop:main`; specs exclude
  the legacy Tk modules, `tkinter/_tkinter/idlelib`, Tcl/Tk runtime data and all
  non-target pywebview Python backends.
- Manifest finalization scans the actual packaged tree and refuses Tk/Tcl,
  CEF/Qt, Android or legacy WebBrowser artifacts, missing Web assets, or the
  wrong platform runtime.
- The Windows bundle uses Edge Chromium/WinForms and ships the validated
  `MS-Event-Studio.exe.config` CLR policy beside the executable.
- The macOS ARM64 bundle uses Cocoa/WebKit and is built only on an ARM64 Mac.
- `desktop_bundle.build_desktop` accepts only a successful executable
  `--webview-smoke` report proving a hidden native window, loaded/ready DOM,
  health/bootstrap API calls, and the scientific Parquet/SQLite/export loop.
- The smoke `application_version` must exactly match the package version in
  `pyproject.toml` (`0.3.0.dev1`); archive/CI labels use `0.3.0-dev1`.
- macOS repeats the executable smoke after ad-hoc signing, then runs `codesign`,
  `plutil`, and architecture checks.

## UX-R8 evidence boundary

- All 36 browser scenarios are automated at 960×640, 1366×768 and 1920×1080
  (108 images). UX-R1–R6 interaction/API/accessibility gates and independent
  pre-UAT reviews are complete for the source implementation.
- The final R8 Windows package passed hidden smoke, tree/hash/config and
  single-renderer checks, plus physical 100%, 125%, 150%, and 200% native DPI
  capture. Each scale used the packaged WebView2 window at its logical 960×640
  outer minimum; no browser/CSS proxy was counted as native evidence.
- GitHub Actions run 31678141049 built the ARM64 macOS app on `macos-14`, ran all
  152 tests, launched the signed package through Cocoa, and passed the hidden
  DOM/API/scientific smoke and final codesign/manifest/archive checks.
- Windows automated/native evidence and user-facing manual acceptance are
  complete. macOS Retina screenshots and mouse UAT remain `planned` for a later
  Apple Silicon tester; a hidden native window proves the package starts, but
  it is not visible Retina acceptance. The earlier Actions artifact predates
  the final Windows feedback fixes and must be rebuilt from the accepted source.

Therefore `python scripts/capture_ui_matrix.py --validate-only --require-all`
must now fail only on macOS Retina. Do not label `0.3.0.dev1` UAT-complete until
the later non-substitutable Retina sample exists.
