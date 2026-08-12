# Phase 2 desktop candidate and UAT

Run date: 2026-08-12 (Asia/Shanghai)

Status: **implementation candidate ready; Phase 2 exit pending mouse UAT and a
native macOS build/UAT**.

## Implemented surface

The desktop application opens in a branded, responsive native Tk window with a
cross-platform peak/apex icon, New, Open, a disposable guided test, and
friendly-name Recent project actions. New-project inspection performs the one
large source parse, complete stream hash, target-ion extraction, real byte
progress, and cancellation. Project creation reuses that prepared scan table,
rechecks source edge identity before and after creation, and publishes only a
fully validated sibling staging directory.

The review view provides a min/max-envelope trace, automatic apexes as a
separate never-decimated overlay, physical support intervals, deterministic
dense labels, linear/log display, bounded pan/window requests, status/source
filters, and one SQLite snapshot per backend window response. Selected-event
evidence includes real scan identity, PC34/MS782/TIC, m/z/ppm, prominence,
physical width, snap offset, and quality flags.

Status writes update the visual model immediately and persist on a single
background writer; a conflict or I/O error restores the exact prior visual
state. Add and Adjust call the scientific real-scan snap rules rather than
creating arbitrary times. Restore and durable Undo/Redo append audit rows.
Color is paired with marker shape and text tokens. Plain-letter shortcuts are
disabled whenever a text-entry widget has focus.

Analysis-range changes first compute a read-only diff. Apply requires a second
explicit confirmation, verifies the unchanged manifest/review/detection
snapshot, reserves the old SQLite writer, builds a new generation, archives a
guarded immutable copy of the retired review database, and switches the root
manifest once. Stable mappings retain EventID; ambiguous/unmatched old reviews
remain stale. A stale old application may write its obsolete database path
without changing the bound archive or the new active generation.

Human and machine exports exclude stale generation history. Human CSV defaults
to accepted only; pending remains opt-in. Machine export contains every review
status in the active generation and requires downstream status filtering.

## Automated gates

The polished source run discovered 82 tests: 81 passed and one symlink test was
skipped solely because the Windows account lacks symlink creation privilege.
Covered failure paths include corrupt display-cache rebuild, optimistic-write
rollback, stale range previews, mutation of a previewed detection table,
post-switch manifest rollback, stale old-window writes, project reopen, durable
undo/redo, path attacks, and atomic export/project staging.

The packaged hidden-window smoke test exercises NumPy/SciPy detection, pandas
and PyArrow Parquet round-trip, SQLite review, display-pyramid build/read, human
CSV, and machine contract in the frozen runtime. It produced 1,201 scans, three
automatic events, 304 display points, one accepted human row, and three machine
rows.

The four real-MS regressions were rerun from ignored parse caches. All gates
passed, source and user-project snapshots remained unchanged, and the canonical
summary stayed:

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
| Human export (100 accepted rows) | 14.96 ms |
| Machine export (1,794 rows) | 101.50 ms |

All defined interaction p95 gates are below 250 ms. Detector execution and the
initial cache/database builds are creation/recalculation work, not window or
review interaction gates.

## Windows candidate

Built natively on Windows 11 with Python 3.12.3 and PyInstaller 6.21.0 at
2026-08-12 21:56 CST. This is a local unsigned development candidate, not a
published or code-signed release.

| Field | Value |
|---|---|
| Application version | `0.2.0.dev1` |
| Bundle mode | `onedir-windowed` |
| Executable | `dist/windows/MS-Event-Studio/MS-Event-Studio.exe` |
| Executable SHA-256 | `9324498bca33ee94ad2413dc27315208a1ca1c46da62401232c013b8f9d244fb` |
| Bundle file count | 2,545 |
| Bundle bytes | 529,054,313 |
| Bundle tree SHA-256 | `a83b79f5e36b0aeab8ae3625eb1a4e04ecb38ff419f450993367931398cfbb85` |
| Packaged smoke | exit 0, `status=ok`, `window_system=win32` |
| Embedded icon | extracted from EXE and visually verified at small size |
| Validated archive | `release/MS-Event-Studio-0.2.0-dev1-windows-x64.zip` |
| Archive bytes | 190,731,995 |
| Archive SHA-256 | `01689deb9c67dc764a875c96522e83281725b9476c5f27aac54ad54658b34a57` |

The complete per-file manifest and smoke payload are generated at
`dist/windows/build_manifest.json` and `dist/windows/smoke_test.json`.
`dist/` is the mutable native-candidate area; after validation the wrapper
publishes only `release/MS-Event-Studio-<version>-windows-x64.zip` and its
SHA-256 sidecar. Both directories are intentionally ignored by Git.

PyInstaller is not a cross-compiler. The macOS ARM64 path is implemented and
contract-tested in `.github/workflows/release-desktop.yml`, following LMA
Studio's `macos-14` native-runner pattern. A genuine `.app` candidate still
must be produced by GitHub Actions and exercised on Apple Silicon:

```bash
MS_EVENT_STUDIO_VERSION=0.2.0-dev1 bash desktop_bundle/build_macos.sh
```

The local repository currently has no Git remote, so no workflow run is claimed
yet. See `docs/github_actions_builds.md` for the first push and manual-run path.

## Mouse UAT checklist

Keep the entire Windows bundle together, run the executable, and click **Start
guided test**. The application creates a uniquely named disposable source and
prefills the project form. A Chinese step-by-step version is maintained in
`docs/guided_test_zh.md`.

1. Confirm the Welcome screen shows New project, Open project, and no internal
   schema/version text in Recent projects.
2. Click Start guided test and choose a disposable parent directory. Click
   Analyze source; confirm the available closed range becomes 0–2 min,
   the count is 1,201 scans, and Create project becomes available.
3. Create range 0–2 min. Confirm three automatic apexes at 0.5, 1.0, and 1.5
   min; the weak local apex at 0.75 min is intentionally below auto threshold.
4. Pan, change window width, switch linear/log scale, toggle labels, and try all
   filters. These actions must not change review state.
5. Select each automatic event. Confirm scan IDs 100300, 100600, and 100900 and
   that PC34, MS782, TIC, m/z/ppm, support, and quality evidence are visible.
6. Apply Accepted, Rejected, Pending, and Restore. Close and reopen the project;
   confirm persistence. Exercise Ctrl+Z/Ctrl+Y and reopen again.
7. Focus the Reason field and type `a r p u`; confirm typing does not trigger any
   review action. Move focus to the canvas and confirm A/R/P/U shortcuts work.
8. Enter Add mode and click near 0.75 min. Confirm a real scan is added as
   accepted with a displayed snap offset. Clicking within an existing automatic
   support must navigate to it rather than create a duplicate.
9. Select an automatic event, enter Adjust mode, and click near its apex.
   Confirm snapping stays inside immutable support; a click outside support must
   fail without changing the event.
10. Export human CSV with pending off/on and inspect its six columns. Export a
    machine contract to a new directory and confirm it contains manifest,
    Parquet, and checksum sidecar.
11. Change the range to 0.6–2 min. Inspect the diff before confirmation, then
    apply. Confirm the 0.5-min automatic review becomes stale/history while
    mapped events retain EventID. Repeat exports and confirm stale history is
    absent from active outputs.
12. Close and reopen once more. The project must validate and retain the active
    range, reviews, manual event, and audit-backed undo/redo state.

Record pass/fail, unexpected messages, and screenshots for any visual defect.
Mouse UAT must not use or modify an LMA Studio project; the generated source and
project are disposable.

The deterministic guided source is a reproducible interaction smoke fixture,
not a substitute for real-data acceptance. Windows Phase 2 UAT must additionally
open the packaged application against read-only `HSC1_data/Lin-_MPP.txt`
(approximately 7.38 GiB), stream the complete source through New Project, and
write only to a separate disposable UAT project outside `HSC1_data` and this
repository. The detailed sequence is in `docs/guided_test_zh.md`.
