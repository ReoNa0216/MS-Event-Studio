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
- R7 proved the Windows pre-candidate on physical 100% and 150% monitors,
  including PerMonitorV2, WebView2, logical outer minimum, tree/hash/config,
  single renderer and LMA 150% comparison. Those artifacts remain traceable
  under `build/qa/`, but the R8 version bump requires a fresh Windows package
  and the same gates on its exact bytes.
- Windows 125% and 200% remain `planned`; no browser/CSS proxy is counted as
  native evidence.
- macOS ARM64 build, signed packaged smoke, native Retina screenshots and mouse
  UAT remain `planned`. This Windows workspace has no remote and does not run or
  claim macOS validation.

Therefore `python scripts/capture_ui_matrix.py --validate-only --require-all`
must still fail only on Windows 125%/200% and macOS Retina. Do not label
`0.3.0.dev1` pre-UAT-complete until the new package evidence and these
non-substitutable native samples exist.
