"""Deterministic, disposable source used by the guided Phase 2 walkthrough."""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path


PC34_MZ = 760.5851
QC782_MZ = 782.5616
SCAN_COUNT = 1201
AUTOMATIC_PEAKS = {300: 1000.0, 600: 1500.0, 900: 1200.0}
MANUAL_ONLY_PEAKS = {450: 80.0}


@dataclass(frozen=True, slots=True)
class GuidedTestAssets:
    source_path: Path
    project_path: Path
    source_sha256: str
    source_size_bytes: int


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


def create_guided_source(output: str | Path) -> dict[str, object]:
    destination = Path(output).resolve()
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite existing guided-test source: {destination}")
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


def create_guided_test_assets(parent: str | Path) -> GuidedTestAssets:
    root = Path(parent).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"guided-test parent directory does not exist: {root}")
    suffix = 1
    while True:
        label = "MS_Event_Studio_Guided_Test" + ("" if suffix == 1 else f"_{suffix}")
        source = root / f"{label}_Source.txt"
        project = root / f"{label}_Project"
        if not source.exists() and not project.exists():
            break
        suffix += 1
    result = create_guided_source(source)
    return GuidedTestAssets(
        source_path=source,
        project_path=project,
        source_sha256=str(result["sha256"]),
        source_size_bytes=int(result["size_bytes"]),
    )
