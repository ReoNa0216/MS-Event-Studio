# Phase 2 dev3 scientific and packaging baseline

Evidence date: 2026-08-13 (Asia/Shanghai)

Status: **scientific, persistence, performance, and Windows packaged-smoke
baseline passed; desktop UX rejected; Phase 2 not exited.**

The `0.2.0.dev3` Tk bundle described here is retained for regression evidence.
It is not a polished candidate and must not be sent to the user for further UX
acceptance. The superseding architecture, task flow, QA matrix, and staged
execution plan are in
[`MS_EVENT_STUDIO_UI_REBUILD_HANDOFF.md`](MS_EVENT_STUDIO_UI_REBUILD_HANDOFF.md).

## Why the UX status changed

User review and three independent audits found blocking issues that the current
automated suite did not exercise:

- the review page mixes a Canvas-painted welcome screen with `ttk/clam`
  workbench controls and cannot reliably match LMA Studio's WebView typography,
  rounded cards, spacing, and component states;
- selected-event evidence is an unstructured text block, while review and peak
  edit actions are visually crowded and do not expose a clear hierarchy;
- Add/Adjust mode only changes the cursor and bottom status text; it has no
  persistent active state, allowed-region overlay, hover candidate, preview, or
  explicit apply/cancel step;
- clicks outside the data plot can reach the add/adjust write path because the
  legacy handler does not validate the complete plot rectangle;
- the highest peak maps to the plot top and its triangle/label necessarily
  crosses the border;
- analysis-range editing is split across multiple native dialogs, and export
  copy exposes developer-facing human/machine terminology;
- the 84 baseline tests do not construct the complete review UI or compare
  standard-state screenshots.

The approved remedy is a Phase 2R move to the same pywebview + HTML/CSS/SVG
application shell and design system as frozen LMA Studio v0.4.4. The Python
scientific and project core below remains the regression baseline.

## Baseline behavior that passed

- one-pass source inspection with complete stream hash, real byte progress,
  cancellation, mutation guards, and reuse of the parsed scan table during
  atomic project creation;
- min/max display pyramids, bounded 1/10-minute windows, event overlays,
  physical support intervals, deterministic labels, linear/log display, and
  one SQLite snapshot per backend window response;
- real-scan PC34/MS782/TIC, m/z/ppm, prominence, physical-width, snap-offset,
  and quality evidence;
- optimistic review updates with a single background writer and exact visual
  rollback on conflict or I/O failure;
- real-scan Add/Adjust rules, durable Undo/Redo, append-only audit, and reopen
  persistence;
- previewed analysis-range recalculation with unchanged-state guards,
  stable-ID reconciliation, archived old generations, and one atomic manifest
  switch;
- active-generation review-result CSV and versioned audit/data exports.

These capabilities are implementation facts, not proof that their legacy UI is
understandable or visually acceptable.

## Automated baseline

The source run discovered 84 tests: 83 passed and one symlink test was skipped
solely because the Windows account lacks symlink creation privilege. Covered
failure paths include corrupt display-cache rebuild, optimistic-write rollback,
stale range previews, mutation of a previewed detection table, post-switch
manifest rollback, stale old-window writes, project reopen, durable undo/redo,
path attacks, and atomic export/project staging.

The packaged hidden-window smoke test exercised NumPy/SciPy detection, pandas
and PyArrow Parquet round-trip, SQLite review, display-pyramid build/read,
review-result CSV, and the versioned audit/data export in the frozen runtime. It
produced 1,201 scans, three automatic events, 304 display points, one accepted
CSV row, and three rows in the complete data export.

Four real-MS regressions were rerun from ignored parse caches. All gates passed,
source and user-project snapshots remained unchanged, and the canonical summary
stayed:

```text
c03232a6153ba48a1f12d1e69c26bbad33d43b2d069f428d2e2ea074616f0b30
```

## Real-data performance snapshot

Dataset: read-only Lin-_LSK parse cache, 8,278,292,803 source bytes, 54,091
scans, and 1,794 automatic events. The recorded first full parse took 501.531 s;
subsequent browsing does not reread the raw TXT.

| Measurement | Result |
|---|---:|
| Scan Parquet load | 37.82 ms |
| Full-trace detector | 675.83 ms |
| Initial display-pyramid build | 187.35 ms |
| Review DB initial creation | 279.59 ms |
| 1-minute window p95 (100 windows) | 21.20 ms |
| 10-minute window p95 (100 windows) | 20.92 ms |
| Review p95 (100 writes) | 9.65 ms |
| 100 review writes total | 865.25 ms |
| Review-result export (100 accepted rows) | 14.96 ms |
| Complete data export (1,794 rows) | 101.50 ms |

All defined backend interaction p95 gates are below 250 ms. Detector execution
and initial cache/database builds are creation/recalculation work rather than
window/review interaction gates.

## Historical Windows dev3 bundle

Built natively on Windows 11 with Python 3.12.3 and PyInstaller 6.21.0 on
2026-08-13 in the isolated `build/venv/windows` environment. This is a local,
unsigned, scientific/package regression bundle—not an accepted release.

| Field | Value |
|---|---|
| Application version | `0.2.0.dev3` |
| Bundle mode | `onedir-windowed` |
| Executable | `dist/windows/MS-Event-Studio/MS-Event-Studio.exe` |
| Runtime versions | NumPy 2.5.2, pandas 2.3.3, PyArrow 21.0.0, SciPy 1.18.0, Pillow 11.3.0 |
| Executable SHA-256 | `e77432602bd027b5b03f6535bca04465d1d13311da54653783ef7e6e12ac78f4` |
| Bundle file count | 2,427 |
| Bundle bytes | 240,675,352 |
| Bundle tree SHA-256 | `3a95ebc32a5d1af565061185a9137d1737a03b0ccf5e36e5b9fc61862175d75c` |
| Packaged smoke | exit 0, `status=ok`, `window_system=win32` |
| Validated regression archive | `release/MS-Event-Studio-0.2.0-dev3-windows-x64.zip` |
| Archive bytes | 92,676,388 |
| Archive SHA-256 | `f20dc43485742c24a681377520e12ba9ac1d2af8cbb445415d79f4fc4ebc855e` |

Per-file evidence is generated at `dist/windows/build_manifest.json` and
`dist/windows/smoke_test.json`. `dist/` and `release/` are ignored mutable
outputs. A smoke-validated archive is still not evidence of UX acceptance.

The current dev3 Windows package correctly has no `.exe.config`: it uses Tk and
does not load CLR. Phase 2R changes that rule. Its pywebview/Windows bundle must
ship and test `MS-Event-Studio.exe.config` next to the executable, following the
LMA WebView2/pythonnet packaging pattern.

## macOS status

At the time of this dev3 baseline there was no remote workflow run or genuine
`.app` UAT. The later Phase 2R WebView work connected a GitHub repository and
successfully built/smoke-tested an unpublished ARM64 Cocoa candidate on
`macos-14`; current evidence and remaining Retina UAT are tracked in
[`github_actions_builds.md`](github_actions_builds.md). The old dev3 command is
historical only:

```bash
MS_EVENT_STUDIO_VERSION=0.2.0-dev3 bash desktop_bundle/build_macos.sh
```

## UAT status

This section records the rejected dev3 state only. The current user-facing
Windows operation card is [`guided_test_zh.md`](guided_test_zh.md); it applies
to the `0.3.0.dev1` WebView candidate and intentionally does not repeat this
historical Tk checklist. Windows human sign-off for that candidate is complete;
the separate Apple Silicon Retina UAT is the remaining native human gate.
Current status is maintained in
[`phase2r_qa_and_packaging.md`](phase2r_qa_and_packaging.md).
