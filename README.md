# MS Event Studio

Independent MS-only event extraction and auditable review. This repository does
not import LIF, UMAP coordinates, cell labels, expected event counts, or LMA
Studio project state.

Phase 1 scientific and CLI gates passed on 2026-08-12. The Phase 2 Windows
implementation candidate is ready; Phase 2 is not declared exited until mouse
UAT and a native macOS candidate pass. LMA Studio remains frozen at v0.4.4.

## Desktop candidate

The native Tk desktop application provides:

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
- accepted-only human CSV and all-active-status versioned machine export.

Run from source:

```powershell
python -m pip install -e .
ms-event-studio-gui
```

The current local Windows candidate is:

```text
release/windows/MS-Event-Studio/MS-Event-Studio.exe
```

It is an `onedir` application: keep the whole `MS-Event-Studio` directory
together. The executable alone is not portable.

To create a small, deterministic two-minute source for mouse UAT:

```powershell
python scripts/create_phase2_uat_source.py `
  --output "$env:TEMP\ms-event-studio-phase2-uat.txt"
```

The UAT and candidate hashes are recorded in
[docs/phase2_desktop_and_uat.md](docs/phase2_desktop_and_uat.md).

## Scientific core and CLI

- strict one-pass ASCII parser with complete SHA-256, byte progress,
  cancellation, input-mutation checks, and fixed-point nanosecond time;
- versioned PC34/760.5851 detector using a closed ±12 ppm window and the frozen
  v0.4.4 adaptive threshold behavior, with corrected bin ownership and physical
  width on irregular time axes;
- atomic portable project creation and hash-checked preflight;
- SQLite review overlay with stable EventID, immutable automatic evidence,
  optimistic revision checks, append-only audit, and durable undo/redo;
- accepted-only six-column human CSV (`pending` is opt-in) and a versioned
  machine contract with Parquet and SHA-256 sidecar.

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

The final Phase 2 implementation run discovered 75 tests: 74 passed and one
symlink-escape test was skipped because this Windows account cannot create a
symlink. All lexical Windows/UNC/drive/ADS/traversal attacks passed. Four
read-only real-MS regressions passed with canonical summary SHA-256
`c03232a6153ba48a1f12d1e69c26bbad33d43b2d069f428d2e2ea074616f0b30`.

Further contracts:

- [Scientific rules](docs/scientific_contract.md)
- [Project and export schemas](docs/project_and_export_contracts.md)
- [Phase 1 real-data regression](docs/phase1_real_regression_summary.md)
- [Phase 2 desktop, performance, packaging, and UAT](docs/phase2_desktop_and_uat.md)
