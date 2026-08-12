"""Strict streaming parser for the v1 ASCII mzML-like export."""

from __future__ import annotations

import hashlib
import re
import time
import warnings
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from .errors import CancelledError, InputChangedError, MSParseError
from .timebase import minutes_to_ns


PARSER_VERSION = "ascii-ms-summary-parser-v1"
TOLERANCE_PPM = 12.0
PSEUDOCOUNT = 1.0
EDGE_BYTES = 1_048_576

MARKERS = (
    ("pc34_760", 760.5851),
    ("qc_782", 782.5616),
)

RE_SPECTRUM_LIST = re.compile(r"spectrumList\s*\((\d+)\s+spectra\)")
RE_INDEX = re.compile(r"^\s*index:\s*(\d+)\s*$")
RE_SCAN_ID = re.compile(r"\bid:\s*scanId=(\d+)(?:\s|$)")
RE_ARRAY_LENGTH = re.compile(r"defaultArrayLength:\s*(\d+)")
RE_SCAN_TIME = re.compile(r"scan start time,\s*([0-9.eE+\-]+),\s*minute")
RE_ARRAY = re.compile(r"binary:\s*\[(\d+)\]\s*(.*)$")
NUMERIC_FIELDS = (
    (re.compile(r"base peak m/z,\s*([0-9.eE+\-]+)"), "base_peak_mz"),
    (re.compile(r"base peak intensity,\s*([0-9.eE+\-]+)"), "base_peak_intensity"),
    (re.compile(r"total ion current,\s*([0-9.eE+\-]+)"), "tic"),
    (re.compile(r"lowest observed m/z,\s*([0-9.eE+\-]+)"), "lowest_observed_mz"),
    (re.compile(r"highest observed m/z,\s*([0-9.eE+\-]+)"), "highest_observed_mz"),
)


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    size_bytes: int
    mtime_ns: int
    head_sha256: str
    tail_sha256: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ParseSummary:
    metadata_spectrum_count: int
    parsed_spectrum_count: int
    size_bytes: int
    tolerance_ppm: float
    parser_version: str
    elapsed_sec: float


@dataclass(frozen=True, slots=True)
class ParseProgress:
    phase: str
    bytes_read: int
    total_bytes: int
    parsed_spectra: int

    @property
    def fraction(self) -> float:
        return 1.0 if self.total_bytes == 0 else min(1.0, self.bytes_read / self.total_bytes)


@dataclass(frozen=True, slots=True)
class ParseResult:
    scans: pd.DataFrame
    fingerprint: SourceFingerprint
    summary: ParseSummary


@dataclass(frozen=True, slots=True)
class _EdgeFingerprint:
    size_bytes: int
    mtime_ns: int
    head_sha256: str
    tail_sha256: str


def _edge_fingerprint(path: Path) -> _EdgeFingerprint:
    stat = path.stat()
    with path.open("rb") as handle:
        head = handle.read(EDGE_BYTES)
        if stat.st_size > EDGE_BYTES:
            handle.seek(max(0, stat.st_size - EDGE_BYTES))
            tail = handle.read(EDGE_BYTES)
        else:
            tail = head
    return _EdgeFingerprint(
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        head_sha256=hashlib.sha256(head).hexdigest(),
        tail_sha256=hashlib.sha256(tail).hexdigest(),
    )


def _float(text: str, *, field: str, spectrum_number: int) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise MSParseError(f"invalid {field} in spectrum {spectrum_number}") from exc
    if not np.isfinite(value):
        raise MSParseError(f"non-finite {field} in spectrum {spectrum_number}")
    return value


def _assign_unique(
    current: dict,
    key: str,
    value,
    *,
    spectrum_number: int,
) -> None:
    if key in current:
        raise MSParseError(f"duplicate field {key} in spectrum {spectrum_number}")
    current[key] = value


def _numeric_array(line: str, *, kind: str, spectrum_number: int) -> tuple[np.ndarray, int]:
    match = RE_ARRAY.search(line)
    if not match:
        raise MSParseError(f"malformed numeric array header for {kind} in spectrum {spectrum_number}")
    declared = int(match.group(1))
    payload = match.group(2)
    try:
        # NumPy parses the generated 6k-element lines in C.  It emits a
        # DeprecationWarning whenever unmatched lexical material remains (for
        # example ``1e`` or ``1abc``); promoting that warning to an exception,
        # then checking declared length and finiteness, retains fail-closed
        # behavior without a Python float object for every source value.
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            values = np.fromstring(payload, sep=" ", dtype=np.float64)
    except (ValueError, DeprecationWarning) as exc:
        raise MSParseError(f"invalid numeric array for {kind} in spectrum {spectrum_number}") from exc
    if len(values) != declared:
        raise MSParseError(
            f"numeric array length mismatch for {kind} in spectrum {spectrum_number}: "
            f"declared {declared}, parsed {len(values)}"
        )
    if not np.isfinite(values).all():
        raise MSParseError(f"non-finite numeric array for {kind} in spectrum {spectrum_number}")
    return values, declared


def _marker_fields(mz: np.ndarray, intensity: np.ndarray) -> dict[str, int | float]:
    result: dict[str, int | float] = {}
    for prefix, target in MARKERS:
        tolerance = target * TOLERANCE_PPM * 1e-6
        left = int(np.searchsorted(mz, target - tolerance, side="left"))
        right = int(np.searchsorted(mz, target + tolerance, side="right"))
        selected_mz = mz[left:right]
        selected_intensity = intensity[left:right]
        result[f"{prefix}_n_mz"] = int(len(selected_mz))
        result[f"{prefix}_closest_mz"] = np.nan
        result[f"{prefix}_closest_ppm_error"] = np.nan
        result[f"{prefix}_max_intensity"] = 0.0
        result[f"{prefix}_sum_intensity"] = 0.0
        result[f"{prefix}_mz_at_max_intensity"] = np.nan
        result[f"{prefix}_ppm_error_at_max_intensity"] = np.nan
        if len(selected_mz):
            closest = int(np.argmin(np.abs(selected_mz - target)))
            maximum = int(np.argmax(selected_intensity))
            closest_mz = float(selected_mz[closest])
            apex_mz = float(selected_mz[maximum])
            result[f"{prefix}_closest_mz"] = closest_mz
            result[f"{prefix}_closest_ppm_error"] = (closest_mz - target) / target * 1e6
            result[f"{prefix}_max_intensity"] = float(selected_intensity[maximum])
            result[f"{prefix}_sum_intensity"] = float(selected_intensity.sum())
            result[f"{prefix}_mz_at_max_intensity"] = apex_mz
            result[f"{prefix}_ppm_error_at_max_intensity"] = (apex_mz - target) / target * 1e6
    return result


def _finalize_spectrum(current: dict, mz: np.ndarray | None, intensity: np.ndarray | None, number: int) -> dict:
    required = (
        "spectrum_index",
        "scan_id",
        "array_length",
        "scan_time_token",
        "base_peak_mz",
        "base_peak_intensity",
        "tic",
    )
    missing = [name for name in required if name not in current]
    if mz is None or intensity is None:
        raise MSParseError(f"truncated spectrum {number}: both m/z and intensity arrays are required")
    if missing:
        raise MSParseError(f"truncated spectrum {number}: missing {', '.join(missing)}")
    expected = int(current["array_length"])
    if len(mz) != expected or len(intensity) != expected or len(mz) != len(intensity):
        raise MSParseError(
            f"array length mismatch in spectrum {number}: default={expected}, "
            f"m/z={len(mz)}, intensity={len(intensity)}"
        )
    if len(mz) > 1 and np.any(np.diff(mz) < 0):
        raise MSParseError(f"m/z array is not sorted in spectrum {number}")
    if np.any(mz < 0):
        raise MSParseError(f"negative m/z in spectrum {number}")
    if np.any(intensity < 0):
        raise MSParseError(f"negative intensity in spectrum {number}")
    try:
        time_decimal = Decimal(str(current.pop("scan_time_token")))
        if not time_decimal.is_finite():
            raise InvalidOperation
    except (InvalidOperation, ValueError) as exc:
        raise MSParseError(f"invalid scan start time in spectrum {number}") from exc
    scan_time_ns = minutes_to_ns(time_decimal)
    if scan_time_ns < 0:
        raise MSParseError(f"negative scan start time in spectrum {number}")
    for name in ("base_peak_mz", "base_peak_intensity", "tic"):
        if float(current[name]) < 0:
            raise MSParseError(f"negative {name} in spectrum {number}")
    row = dict(current)
    row["scan_time_ns"] = scan_time_ns
    row["scan_start_time_min"] = float(time_decimal)
    row["scan_start_time_sec"] = float(time_decimal * Decimal(60))
    row["mz_array_length_parsed"] = len(mz)
    row["intensity_array_length_parsed"] = len(intensity)
    row.update(_marker_fields(mz, intensity))
    return row


def _implicit_empty_arrays(current: dict, declarations: set[str]) -> tuple[np.ndarray, np.ndarray] | None:
    """Recognize the exporter representation of a complete zero-length spectrum."""

    if int(current.get("array_length", -1)) != 0:
        return None
    if declarations != {"m/z", "intensity"}:
        return None
    empty = np.asarray([], dtype=np.float64)
    return empty, empty.copy()


def _add_derived_columns(rows: list[dict]) -> pd.DataFrame:
    scan = pd.DataFrame(rows)
    if scan.empty:
        raise MSParseError("source contains no spectra")
    scan.insert(0, "scan_row_index", np.arange(len(scan), dtype=np.int64))
    scan["scan_step_sec"] = scan["scan_start_time_sec"].diff()
    scan["has_pc34_760"] = scan["pc34_760_n_mz"] > 0
    scan["has_qc_782"] = scan["qc_782_n_mz"] > 0
    scan["has_both_markers"] = scan["has_pc34_760"] & scan["has_qc_782"]
    scan["ratio_760_782_max_pseudo1"] = (
        scan["pc34_760_max_intensity"] + PSEUDOCOUNT
    ) / (scan["qc_782_max_intensity"] + PSEUDOCOUNT)
    scan["ratio_760_782_sum_pseudo1"] = (
        scan["pc34_760_sum_intensity"] + PSEUDOCOUNT
    ) / (scan["qc_782_sum_intensity"] + PSEUDOCOUNT)
    scan["log10_tic"] = np.log10(scan.get("tic", pd.Series(np.zeros(len(scan)))).fillna(0).clip(lower=0) + 1.0)
    scan["log10_pc34_760_max"] = np.log10(scan["pc34_760_max_intensity"] + 1.0)
    scan["log10_qc_782_max"] = np.log10(scan["qc_782_max_intensity"] + 1.0)
    return scan


def parse_ms_scan_summary(
    path: str | Path,
    *,
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[ParseProgress], None] | None = None,
    progress_interval_bytes: int = 64 * 1024 * 1024,
) -> ParseResult:
    """Parse once, hash the complete byte stream, and reject every ambiguity."""

    started = time.monotonic()
    source = Path(path).resolve()
    if not source.is_file():
        raise MSParseError(f"MS source is not a regular file: {source}")
    before = _edge_fingerprint(source)
    if cancel_check is not None and cancel_check():
        raise CancelledError("MS parsing cancelled")

    callback = progress_callback
    if callback is not None:
        callback(ParseProgress("parsing", 0, before.size_bytes, 0))

    digest = hashlib.sha256()
    rows: list[dict] = []
    metadata_count: int | None = None
    current: dict | None = None
    mz_array: np.ndarray | None = None
    intensity_array: np.ndarray | None = None
    array_mode: str | None = None
    array_declarations: set[str] = set()
    bytes_read = 0
    next_progress = max(1, int(progress_interval_bytes))

    try:
        with source.open("rb") as handle:
            for raw_line in handle:
                digest.update(raw_line)
                bytes_read += len(raw_line)
                try:
                    line = raw_line.decode("ascii")
                except UnicodeDecodeError as exc:
                    raise MSParseError(f"source is not strict ASCII near byte {bytes_read}") from exc
                stripped = line.strip()

                if cancel_check is not None and cancel_check():
                    raise CancelledError("MS parsing cancelled")
                if callback is not None and bytes_read >= next_progress:
                    callback(ParseProgress("parsing", bytes_read, before.size_bytes, len(rows)))
                    while next_progress <= bytes_read:
                        next_progress += max(1, int(progress_interval_bytes))

                count_match = RE_SPECTRUM_LIST.search(stripped)
                if count_match:
                    declared = int(count_match.group(1))
                    if metadata_count is not None and metadata_count != declared:
                        raise MSParseError("conflicting spectrum count metadata")
                    metadata_count = declared
                    continue

                if stripped == "spectrum:":
                    if current is not None:
                        implicit = _implicit_empty_arrays(current, array_declarations)
                        if implicit is None:
                            raise MSParseError(f"truncated spectrum {len(rows) + 1} before next spectrum")
                        rows.append(
                            _finalize_spectrum(current, implicit[0], implicit[1], len(rows) + 1)
                        )
                    current = {}
                    mz_array = None
                    intensity_array = None
                    array_mode = None
                    array_declarations = set()
                    continue
                if current is None:
                    continue

                match = RE_INDEX.match(stripped)
                if match:
                    _assign_unique(
                        current,
                        "spectrum_index",
                        int(match.group(1)),
                        spectrum_number=len(rows) + 1,
                    )
                    continue
                match = RE_SCAN_ID.search(stripped)
                if match:
                    _assign_unique(
                        current,
                        "scan_id",
                        str(match.group(1)),
                        spectrum_number=len(rows) + 1,
                    )
                    continue
                match = RE_ARRAY_LENGTH.search(stripped)
                if match:
                    _assign_unique(
                        current,
                        "array_length",
                        int(match.group(1)),
                        spectrum_number=len(rows) + 1,
                    )
                    continue
                matched_numeric = False
                for regex, name in NUMERIC_FIELDS:
                    match = regex.search(stripped)
                    if match:
                        _assign_unique(
                            current,
                            name,
                            _float(
                                match.group(1),
                                field=name,
                                spectrum_number=len(rows) + 1,
                            ),
                            spectrum_number=len(rows) + 1,
                        )
                        matched_numeric = True
                        break
                if matched_numeric:
                    continue
                match = RE_SCAN_TIME.search(stripped)
                if match:
                    _assign_unique(
                        current,
                        "scan_time_token",
                        match.group(1),
                        spectrum_number=len(rows) + 1,
                    )
                    continue
                if "cvParam: m/z array" in stripped:
                    if "m/z" in array_declarations:
                        raise MSParseError(f"duplicate m/z array declaration in spectrum {len(rows) + 1}")
                    array_declarations.add("m/z")
                    array_mode = "m/z"
                    continue
                if "cvParam: intensity array" in stripped:
                    if "intensity" in array_declarations:
                        raise MSParseError(
                            f"duplicate intensity array declaration in spectrum {len(rows) + 1}"
                        )
                    if mz_array is None and int(current.get("array_length", -1)) != 0:
                        raise MSParseError(f"intensity array precedes m/z array in spectrum {len(rows) + 1}")
                    array_declarations.add("intensity")
                    array_mode = "intensity"
                    continue
                if "binary:" in stripped:
                    if array_mode is None:
                        raise MSParseError(f"numeric array has no declared type in spectrum {len(rows) + 1}")
                    values, declared = _numeric_array(
                        stripped,
                        kind=array_mode,
                        spectrum_number=len(rows) + 1,
                    )
                    if array_mode == "m/z":
                        mz_array = values
                        current["mz_array_declared_length"] = declared
                        array_mode = None
                    else:
                        intensity_array = values
                        current["intensity_array_declared_length"] = declared
                        row = _finalize_spectrum(current, mz_array, intensity_array, len(rows) + 1)
                        rows.append(row)
                        current = None
                        mz_array = None
                        intensity_array = None
                        array_mode = None
                        array_declarations = set()
    except OSError as exc:
        raise MSParseError(f"failed while reading MS source: {exc}") from exc

    after = _edge_fingerprint(source)
    if before != after:
        raise InputChangedError("MS source changed while parsing")
    if current is not None:
        implicit = _implicit_empty_arrays(current, array_declarations)
        if implicit is None:
            raise MSParseError(f"truncated spectrum {len(rows) + 1} at end of file")
        rows.append(_finalize_spectrum(current, implicit[0], implicit[1], len(rows) + 1))
    if metadata_count is None:
        raise MSParseError("missing spectrum count metadata")
    if metadata_count != len(rows):
        raise MSParseError(
            f"spectrum count mismatch: metadata declares {metadata_count}, parsed {len(rows)}"
        )

    scan_ids = [str(row["scan_id"]) for row in rows]
    if len(scan_ids) != len(set(scan_ids)):
        raise MSParseError("duplicate scan_id in source")
    spectrum_indices = [int(row["spectrum_index"]) for row in rows]
    if len(spectrum_indices) != len(set(spectrum_indices)):
        raise MSParseError("duplicate spectrum_index in source")
    scan_times = [int(row["scan_time_ns"]) for row in rows]
    if any(right <= left for left, right in zip(scan_times, scan_times[1:])):
        raise MSParseError("scan start times must be strictly increasing in source order")

    scans = _add_derived_columns(rows)
    if callback is not None:
        callback(ParseProgress("complete", bytes_read, before.size_bytes, len(rows)))
    fingerprint = SourceFingerprint(
        size_bytes=after.size_bytes,
        mtime_ns=after.mtime_ns,
        head_sha256=after.head_sha256,
        tail_sha256=after.tail_sha256,
        sha256=digest.hexdigest(),
    )
    summary = ParseSummary(
        metadata_spectrum_count=metadata_count,
        parsed_spectrum_count=len(rows),
        size_bytes=after.size_bytes,
        tolerance_ppm=TOLERANCE_PPM,
        parser_version=PARSER_VERSION,
        elapsed_sec=time.monotonic() - started,
    )
    return ParseResult(scans=scans, fingerprint=fingerprint, summary=summary)
