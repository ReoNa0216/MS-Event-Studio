# MS Event Studio

Independent MS-only event extraction and auditable review. This repository does
not import LIF, UMAP coordinates, cell labels, expected event counts, or LMA
Studio project state.

Phase 1 scientific and CLI gates passed on 2026-08-12. The `0.2.0.dev3`
Windows bundle also passed its scientific, persistence, performance, and
packaged-smoke gates, but its desktop UX was rejected on 2026-08-13. It is a
regression baseline, not a release candidate. Phase 2R now rebuilds the UI with
the same WebView design system as frozen LMA Studio v0.4.4 while preserving the
tested MS core. See the
[Phase 2R UI rebuild handoff](docs/MS_EVENT_STUDIO_UI_REBUILD_HANDOFF.md).

## Desktop status

The current source entry point and local `0.2.0.dev3` bundle still launch the
legacy native Tk workbench. It remains useful for backend regression and
packaged-smoke testing, but it must not be presented for UX acceptance. In
particular, its unstructured evidence panel, ambiguous peak-edit modes, range
dialog sequence, marker overflow, and mixed Tk/Canvas component language are
known failures.

The tested baseline provides:

- a Chinese-first legacy welcome/review interface, a cross-platform application
  icon, and a disposable regression fixture;
- one-pass source inspection with byte progress, cancellation, and reuse of the
  parsed scan table during atomic project creation;
- min/max display pyramids, bounded 1/10-minute windows, event apex overlays,
  support intervals, deterministic dense labels, linear/log scale, and pan;
- evidence for the selected real scan, including PC34, MS782, TIC, m/z/ppm,
  prominence, physical width, and quality flags;
- accepted/rejected/pending/unreviewed review, real-scan Add/Adjust, Restore,
  durable Undo/Redo, non-color-only encodings, filters, and keyboard access;
- immediate optimistic status display with asynchronous persistence and failure
  rollback;
- previewed analysis-range recalculation with stable-ID reconciliation, stale
  history retention, and one atomic manifest switch;
- accepted-only review-result CSV and all-active-status versioned audit/data
  export.

Run from source:

```powershell
python -m pip install -e .
ms-event-studio-gui
```

The historical local Windows regression bundle is:

```text
dist/windows/MS-Event-Studio/MS-Event-Studio.exe
```

It is an `onedir` application: keep the whole `MS-Event-Studio` directory
together. The executable alone is not portable.

`dist/` contains mutable native builds under test. Passing packaged smoke allows
a regression archive to be written under `release/`; it does not by itself make
that archive an accepted UX or release candidate.

Do not use the legacy guided test as UX acceptance. It is retained only to
reproduce the dev3 interaction chain; its scope and candidate hashes are
recorded in [the historical Phase 2 evidence](docs/phase2_desktop_and_uat.md).
The next user UAT begins only after the WebView candidate passes the automated
screenshot matrix and three independent agent reviews described in the Phase
2R handoff.

Native macOS ARM64 and Windows x64 candidates are built on GitHub's matching
runners. See [GitHub Actions desktop builds](docs/github_actions_builds.md).

## Scientific core and CLI

- strict one-pass ASCII parser with complete SHA-256, byte progress,
  cancellation, input-mutation checks, and fixed-point nanosecond time;
- versioned PC34/760.5851 detector using a closed ±12 ppm window and the frozen
  v0.4.4 adaptive threshold behavior, with corrected bin ownership and physical
  width on irregular time axes;
- atomic portable project creation and hash-checked preflight;
- SQLite review overlay with stable EventID, immutable automatic evidence,
  optimistic revision checks, append-only audit, and durable undo/redo;
- accepted-only six-column review-result CSV (`pending` is opt-in) and a
  versioned audit/data contract with Parquet and SHA-256 sidecar.

```powershell
ms-event-studio create --source "D:\data\run.txt" --project "D:\projects\run" `
  --name "Run" --start-min 10 --end-min 60
ms-event-studio verify --project "D:\projects\run"
ms-event-studio export --project "D:\projects\run" --output accepted.csv
ms-event-studio export-machine --project "D:\projects\run" --output-dir machine-contract
```

Never overwrite an LMA Studio `ms_events.parquet` with either export. Formal LMA
import is Phase 3 and requires a separate validated contract path.

## Verification

```powershell
$env:PYTHONPATH = "src;tests;."
python -m unittest discover -s tests -v
python scripts/run_real_regression.py
python scripts/run_phase2_performance.py
```

The dev3 Phase 2 baseline discovered 84 tests: 83 passed and one symlink-escape
test was skipped because this Windows account cannot create a symlink. These
results establish scientific and infrastructure behavior, not UI usability.
All lexical Windows/UNC/drive/ADS/traversal attacks passed. Four read-only
real-MS regressions passed with canonical summary SHA-256
`c03232a6153ba48a1f12d1e69c26bbad33d43b2d069f428d2e2ea074616f0b30`.

Further contracts:

- [Scientific rules](docs/scientific_contract.md)
- [Project and export schemas](docs/project_and_export_contracts.md)
- [Phase 1 real-data regression](docs/phase1_real_regression_summary.md)
- [Historical dev3 desktop, performance, and packaging evidence](docs/phase2_desktop_and_uat.md)
- [Phase 2R WebView UI rebuild handoff](docs/MS_EVENT_STUDIO_UI_REBUILD_HANDOFF.md)
