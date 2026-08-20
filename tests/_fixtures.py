from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


PRIMARY_MARKER_MZ = 760.5851
QC782_MZ = 782.5616


def spectrum_lines(
    index: int,
    scan_id: int,
    time_min: str | float,
    *,
    mz_values: list[float] | None = None,
    intensities: list[float] | None = None,
    tic: float = 2_000_000.0,
    default_length: int | None = None,
) -> list[str]:
    mz_values = mz_values or [100.0, PRIMARY_MARKER_MZ, QC782_MZ, 900.0]
    intensities = intensities or [0.0, 0.0, 0.0, 0.0]
    length = len(mz_values) if default_length is None else default_length
    mz_payload = " ".join(f"{value:.15g}" for value in mz_values)
    intensity_payload = " ".join(f"{value:.15g}" for value in intensities)
    return [
        "spectrum:",
        f"  index: {index}",
        f"  id: scanId={scan_id}",
        f"  defaultArrayLength: {length}",
        f"  cvParam: base peak m/z, {PRIMARY_MARKER_MZ}",
        f"  cvParam: base peak intensity, {max(intensities)}",
        f"  cvParam: total ion current, {tic}, number of detector counts",
        f"  cvParam: lowest observed m/z, {min(mz_values)}",
        f"  cvParam: highest observed m/z, {max(mz_values)}",
        f"  cvParam: scan start time, {time_min}, minute",
        "  cvParam: m/z array, m/z",
        f"  binary: [{len(mz_values)}] {mz_payload}",
        "  cvParam: intensity array, number of detector counts",
        f"  binary: [{len(intensities)}] {intensity_payload}",
        "",
    ]


def write_ms_file(path: Path, spectra: list[list[str]], *, declared_count: int | None = None) -> Path:
    count = len(spectra) if declared_count is None else declared_count
    lines = [f"spectrumList ({count} spectra)"]
    for item in spectra:
        lines.extend(item)
    path.write_text("\n".join(lines), encoding="ascii")
    return path


def detector_scan(signal: np.ndarray, *, dt_sec: float = 0.1) -> pd.DataFrame:
    signal = np.asarray(signal, dtype=float)
    n = len(signal)
    time_sec = np.arange(n, dtype=float) * dt_sec
    return pd.DataFrame(
        {
            "scan_row_index": np.arange(n, dtype=int),
            "spectrum_index": np.arange(n, dtype=int),
            "scan_id": [str(100_000 + index) for index in range(n)],
            "scan_time_ns": np.rint(time_sec * 1_000_000_000).astype("int64"),
            "scan_start_time_min": time_sec / 60.0,
            "scan_start_time_sec": time_sec,
            "primary_marker_max_intensity": signal,
            "qc_marker_max_intensity": np.ones(n, dtype=float),
            "primary_marker_ppm_error_at_max_intensity": np.zeros(n, dtype=float),
            "qc_marker_ppm_error_at_max_intensity": np.zeros(n, dtype=float),
            "tic": np.full(n, 2_000_000.0),
            "primary_qc_max_ratio_pseudo1": (signal + 1.0) / 2.0,
            "array_length": np.full(n, 7000, dtype=int),
            "base_peak_mz": np.full(n, PRIMARY_MARKER_MZ),
        }
    )
