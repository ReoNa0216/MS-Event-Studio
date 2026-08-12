#!/usr/bin/env python3
"""Create a small deterministic MS text source for Phase 2 mouse UAT."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from pathlib import Path


PC34_MZ = 760.5851
QC782_MZ = 782.5616
SCAN_COUNT = 1201
AUTOMATIC_PEAKS = {300: 1000.0, 600: 1500.0, 900: 1200.0}
MANUAL_ONLY_PEAKS = {450: 80.0}


def _spectrum(index: int) -> list[str]:
    intensity = AUTOMATIC_PEAKS.get(index, MANUAL_ONLY_PEAKS.get(index, 0.0))
    time_min = index / 600.0
    values = (0.0, intensity, 10.0, 0.0)
    return [
        "spectrum:",
        f"  index: {index}",
        f"  id: scanId={100000 + index}",
        "  defaultArrayLength: 4",
        f"  cvParam: base peak m/z, {PC34_MZ}",
        f"  cvParam: base peak intensity, {max(values):.15g}",
        "  cvParam: total ion current, 10000000, number of detector counts",
        "  cvParam: lowest observed m/z, 100",
        "  cvParam: highest observed m/z, 900",
        f"  cvParam: scan start time, {time_min:.12f}, minute",
        "  cvParam: m/z array, m/z",
        f"  binary: [4] 100 {PC34_MZ} {QC782_MZ} 900",
        "  cvParam: intensity array, number of detector counts",
        "  binary: [4] " + " ".join(f"{value:.15g}" for value in values),
        "",
    ]


def create_source(output: str | Path) -> dict[str, object]:
    destination = Path(output).resolve()
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite existing UAT source: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.writing-{uuid.uuid4().hex}")
    try:
        with temporary.open("w", encoding="ascii", newline="\n") as handle:
            handle.write(f"spectrumList ({SCAN_COUNT} spectra)\n")
            for index in range(SCAN_COUNT):
                handle.write("\n".join(_spectrum(index)))
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {
        "path": str(destination),
        "size_bytes": destination.stat().st_size,
        "sha256": digest,
        "scan_count": SCAN_COUNT,
        "expected_automatic_events": len(AUTOMATIC_PEAKS),
        "manual_add_test_time_min": 0.75,
        "closed_range_min": [0, 2],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(create_source(args.output), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
