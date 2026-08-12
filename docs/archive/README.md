# Archived scientific provenance

This directory contains historical evidence that remains useful for provenance
but is not an active implementation brief or executable test oracle.

`phase0_baseline_snapshot.json` records the irreversible synthetic and real-data
summary captured from frozen LMA Studio v0.4.4 during Phase 0. The LSK and MPP
real-data rows combine legacy 10-ppm scan tables with the later 12-ppm detector,
so they must not be treated as an end-to-end raw-to-event baseline. The strict
raw-data results and correction are authoritative in
[`../phase1_real_regression_summary.md`](../phase1_real_regression_summary.md).

New work should use the executable tests plus
[`../scientific_contract.md`](../scientific_contract.md) and
[`../project_and_export_contracts.md`](../project_and_export_contracts.md).
