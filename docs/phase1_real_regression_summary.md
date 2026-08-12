# Phase 1 real-data read-only regression

Run date: 2026-08-12 (Asia/Shanghai)

Runner: `scripts/run_real_regression.py`

Result: **passed**

Canonical four-project summary SHA-256:

```text
c03232a6153ba48a1f12d1e69c26bbad33d43b2d069f428d2e2ea074616f0b30
```

## End-to-end results

| Project | Raw bytes | Full raw SHA-256 | Strict spectra | Events | Frozen LMA physical apex match | Legacy recall within one scan |
|---|---:|---|---:|---:|---|---:|
| Lin-_LSK | 8,278,292,803 | `9881e413d74d1a9127fea1b03ce7100bfb805d6d1d91f2663b3fe78d49a978b6` | 54,091/54,091 | 1,794 | exact, parameter delta 0 | 1,512/1,512 |
| Lin-_MPP | 7,925,382,235 | `774cf02988fb3325b5a809f272015c195f37e69d214f830782378316b27aed25` | 51,241/51,241 | 1,414 | exact, parameter delta 0 | 1,414/1,414 |
| Lin-_CLP | 10,736,174,515 | `4c4d122df439acb1b358877ff393244485ca05c366579f8fa20f430a01a2bd9f` | 69,771/69,771 | 1,818 | exact, parameter delta 0 | 1,818/1,818 |
| Lin-_LK | 10,691,650,073 | `370fdad55568c0666d657e5dc29d0d2126936d7aa34109a44e292a5ae41f0af8` | 69,771/69,771 | 1,056 | exact, parameter delta 0 | 1,056/1,056 |

Every run recorded source size/mtime/head/tail before and after. All were
unchanged and matched the LMA manifests. Each corresponding user-project tree
was independently snapshotted from relative path, size, and mtime before and
after; all four snapshots were unchanged.

The comparison oracle was the frozen `lma-studio` v0.4.4 detector source run on
the same newly parsed scan table. Every physical apex vector and adaptive
height/prominence/distance parameter matched exactly. No LIF, UMAP, coordinate,
cell label, or expected event count was read by the detector.

## Phase 0 mixed-baseline correction

The real LSK scan Parquet predates the LMA commit that changed ±10 ppm to
±12 ppm. Phase 0 therefore measured the new detector on an old 10 ppm scan
summary and reported 1,807 events. A true raw→12 ppm parser→detector run produces
1,794. This is a provenance correction, not a hidden threshold adjustment. The
unchanged audit-time snapshot is retained only as
[`docs/archive/phase0_baseline_snapshot.json`](archive/phase0_baseline_snapshot.json)
and is not an executable or end-to-end oracle.

The raw files also expose a valid leading zero-length spectrum. LSK and MPP
therefore contain one more strictly retained spectrum than their older Parquet
tables. The parser accepts that representation only when
`defaultArrayLength == 0` and both array declarations exist.

Against the legacy LSK event table, 1,509/1,512 retain the exact scan ID. The
remaining three move to the immediately adjacent scan after 12 ppm extraction:

| Legacy scan | New scan | Absolute apex shift |
|---:|---:|---:|
| 862022 | 862125 | 0.103000000020 s |
| 5005767 | 5005664 | 0.103000000020 s |
| 5151025 | 5150922 | 0.102999999960 s |

Thus legacy recall is 1,512/1,512 at the confirmed one-scan comparison boundary.

## MS-only morphology spot-check

LSK has 285 calls whose scan IDs are not in the legacy table. A deterministic
time-stratified sample of 30 was plotted over ±1 second of the new MS760 trace.
All 30 red apex markers were visible positive local maxima; no marker landed on
a non-maximum slope or missing signal. This gives an observed obvious-morphology
false-call count of 0/30, but it is not a biological ground-truth FDR estimate.

Across all 285 new-only calls:

- 0 carried `low_quality_scan_window`;
- 98 carried the explicit collision flag and remain review candidates rather
  than being silently suppressed;
- minimum/median height-to-threshold ratios were 1.0005/1.3871;
- minimum/median prominence-to-threshold ratios were 1.0516/7.6731.

The 30-panel plot, three moved-apex plot, complete regression JSON, and cached
real scan summaries stay under ignored `tests/real_output/` and are not part of
source control.
