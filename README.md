# MS Event Studio

Independent MS-only event extraction and review project. This repository does
not import LIF, UMAP coordinates, cell labels, or LMA Studio project state.

Phase 1 exit gates passed on 2026-08-12; Phase 2 desktop UI work has not begun.
The scientific core and CLI were developed test-first from the confirmed Phase
0 contract. Real MS assets remain outside this repository and are read only. No
LIF, UMAP, cell labels, or expected event counts enter the detector.

## Implemented Phase 1 surface

- strict one-pass ASCII parser with complete SHA-256, byte progress,
  cancellation, input-mutation checks, and fixed-point nanosecond time;
- versioned PC34/760.5851 detector using a closed ±12 ppm window and the frozen
  v0.4.4 adaptive threshold behavior, with corrected bin ownership and physical
  width on irregular time axes;
- atomic portable project creation and hash-checked preflight;
- SQLite review overlay with stable EventID, immutable automatic evidence,
  optimistic revision checks, append-only audit, and durable undo/redo;
- accepted-only six-column human CSV (pending is opt-in) and an all-status
  versioned machine contract with SHA-256 sidecar.

## Development

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

Install the CLI in an isolated environment when desired:

```powershell
python -m pip install -e .
ms-event-studio --help
```

## CLI

```powershell
ms-event-studio create --source "D:\data\run.txt" --project "D:\projects\run" `
  --name "Run" --start-min 10 --end-min 60
ms-event-studio verify --project "D:\projects\run"
ms-event-studio export --project "D:\projects\run" --output accepted.csv
ms-event-studio export-machine --project "D:\projects\run" --output-dir machine-contract
```

`export` never includes rejected or unreviewed events. Add
`--include-pending` only when a downstream consumer explicitly supports that
state. Machine export retains every state and the immutable automatic support.

The CLI prints one JSON result to stdout and structured parse progress/errors to
stderr. Project creation accepts only an absent or empty target and publishes it
only after all Parquet, SQLite, provenance, manifest, and preflight steps pass.

Scientific rules: [docs/scientific_contract.md](docs/scientific_contract.md).
Project and export schemas: [docs/project_and_export_contracts.md](docs/project_and_export_contracts.md).
Four-project read-only regression: [docs/phase1_real_regression_summary.md](docs/phase1_real_regression_summary.md).

The final Phase 1 verification discovered 52 tests: 51 passed and the symlink
escape test was skipped because this Windows account cannot create a symlink.
The equivalent lexical Windows/UNC/drive/ADS/traversal path attacks all passed.
The package also compiles, exposes all four CLI commands, and builds as a wheel.
