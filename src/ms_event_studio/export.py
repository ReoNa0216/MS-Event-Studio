"""Explicit, range-owned human and machine exports."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .canonical import json_value


HUMAN_COLUMNS = (
    "EventID",
    "scan_id",
    "scan_start_time",
    "apex_intensity",
    "review_status",
    "source",
)


@dataclass(frozen=True, slots=True)
class ExportResult:
    path: Path
    row_count: int
    statuses: tuple[str, ...]
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class MachineExportResult:
    output_dir: Path
    event_table_path: Path
    manifest_path: Path
    checksum_path: Path
    row_count: int
    event_table_sha256: str
    manifest_sha256: str


MACHINE_REQUIRED_COLUMNS = (
    "event_id",
    "auto_event_id",
    "generation_id",
    "source_sha256",
    "detector_version",
    "parameter_hash",
    "scan_id",
    "scan_row_index",
    "spectrum_index",
    "scan_time_ns",
    "apex_time_sec",
    "apex_intensity",
    "left_sec",
    "right_sec",
    "peak_width_sec",
    "original_auto_event_id",
    "original_left_sec",
    "original_right_sec",
    "current_scan_id",
    "current_scan_row_index",
    "current_spectrum_index",
    "current_apex_time_ns",
    "current_apex_time_sec",
    "current_apex_intensity",
    "status",
    "origin",
    "revision",
    "snap_offset_sec",
)


def _decimal_minutes(time_ns: int) -> str:
    value = Decimal(int(time_ns)) / Decimal(60_000_000_000)
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _number(value: Any) -> str:
    if value is None:
        return ""
    return format(float(value), ".15g")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def export_human_csv(
    events: Iterable[dict[str, Any]],
    path: str | Path,
    *,
    analysis_start_ns: int,
    analysis_end_ns: int,
    include_pending: bool = False,
) -> ExportResult:
    """Write the six-column downstream contract atomically.

    The default is accepted-only.  Pending is an explicit additive choice;
    unreviewed and rejected are never eligible for this file.
    """

    if int(analysis_end_ns) < int(analysis_start_ns):
        raise ValueError("export range end precedes start")
    statuses = ("accepted", "pending") if include_pending else ("accepted",)
    selected: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    for raw in events:
        event = dict(raw)
        event_id = str(event.get("event_id", ""))
        if not event_id:
            raise ValueError("event is missing EventID")
        if event_id in seen_event_ids:
            raise ValueError(f"duplicate EventID in export input: {event_id}")
        seen_event_ids.add(event_id)
        status = str(event.get("status", ""))
        apex_ns = int(event["current_apex_time_ns"])
        if status not in statuses:
            continue
        if not int(analysis_start_ns) <= apex_ns <= int(analysis_end_ns):
            continue
        selected.append(event)
    selected.sort(key=lambda item: (int(item["current_apex_time_ns"]), str(item["event_id"])))

    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.writing-{uuid.uuid4().hex}")
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=HUMAN_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for event in selected:
                writer.writerow(
                    {
                        "EventID": str(event["event_id"]),
                        "scan_id": str(event["current_scan_id"]),
                        "scan_start_time": _decimal_minutes(int(event["current_apex_time_ns"])),
                        "apex_intensity": _number(event.get("current_apex_intensity")),
                        "review_status": str(event["status"]),
                        "source": str(event["origin"]),
                    }
                )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return ExportResult(
        path=destination,
        row_count=len(selected),
        statuses=statuses,
        sha256=_sha256(destination),
        size_bytes=destination.stat().st_size,
    )


def _machine_row(
    review: dict[str, Any],
    evidence: dict[str, Any] | None,
    *,
    source_sha256: str,
    detector_version: str,
    parameter_hash: str,
) -> dict[str, Any]:
    row = dict(evidence or {})
    if evidence is None:
        row.update(
            {
                "auto_event_id": None,
                "source_sha256": source_sha256,
                "detector_version": None,
                "parameter_hash": None,
                "scan_id": review["current_scan_id"],
                "scan_row_index": review["current_scan_row_index"],
                "spectrum_index": review["current_spectrum_index"],
                "scan_time_ns": review["current_apex_time_ns"],
                "apex_time_sec": review["current_apex_time_sec"],
                "apex_intensity": review["current_apex_intensity"],
                "left_sec": None,
                "right_sec": None,
                "peak_width_sec": None,
            }
        )
    else:
        row.setdefault("source_sha256", source_sha256)
        row.setdefault("detector_version", detector_version)
        row.setdefault("parameter_hash", parameter_hash)
    for key in (
        "event_id",
        "auto_event_id",
        "generation_id",
        "original_auto_event_id",
        "original_left_sec",
        "original_right_sec",
        "current_scan_id",
        "current_scan_row_index",
        "current_spectrum_index",
        "current_apex_time_ns",
        "current_apex_time_sec",
        "current_apex_intensity",
        "status",
        "origin",
        "revision",
        "snap_offset_sec",
    ):
        if key in review:
            row[key] = review[key]
    return row


def _safe_cleanup_export(staging: Path, parent: Path, target_name: str) -> None:
    if not staging.exists():
        return
    resolved = staging.resolve()
    if resolved.parent != parent.resolve() or not resolved.name.startswith(
        f".{target_name}.machine-exporting-"
    ):
        raise RuntimeError("refusing to clean an unrecognized machine-export staging directory")
    shutil.rmtree(resolved)


def export_machine_contract(
    review_events: Iterable[dict[str, Any]],
    automatic_events: Iterable[dict[str, Any]] | pd.DataFrame,
    output_dir: str | Path,
    *,
    source_fingerprint: dict[str, Any],
    detector_version: str,
    parameter_hash: str,
    generation_id: str,
    analysis_start_ns: int,
    analysis_end_ns: int,
    boundary_rule: str = "closed_current_apex_v1",
) -> MachineExportResult:
    """Publish a versioned, all-status downstream contract as one directory."""

    if int(analysis_end_ns) < int(analysis_start_ns):
        raise ValueError("machine export range end precedes start")
    target = Path(output_dir).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target_was_empty = False
    if target.exists():
        if not target.is_dir() or any(target.iterdir()):
            raise FileExistsError(f"machine export target must be absent or empty: {target}")
        target_was_empty = True
    staging = target.parent / f".{target.name}.machine-exporting-{uuid.uuid4().hex}"

    if isinstance(automatic_events, pd.DataFrame):
        automatic_rows = automatic_events.to_dict(orient="records")
    else:
        automatic_rows = [dict(row) for row in automatic_events]
    automatic_ids = [str(row.get("auto_event_id", "")) for row in automatic_rows]
    if any(not identity for identity in automatic_ids):
        raise ValueError("automatic evidence is missing auto_event_id")
    if len(automatic_ids) != len(set(automatic_ids)):
        raise ValueError("duplicate auto_event_id in machine export evidence")
    by_identity = {str(row["auto_event_id"]): row for row in automatic_rows}
    source_sha256 = str(source_fingerprint.get("sha256", ""))
    rows: list[dict[str, Any]] = []
    review_ids: set[str] = set()
    for raw_review in review_events:
        review = dict(raw_review)
        event_id = str(review.get("event_id", ""))
        if not event_id:
            raise ValueError("review event is missing EventID")
        if event_id in review_ids:
            raise ValueError(f"duplicate EventID in machine export input: {event_id}")
        review_ids.add(event_id)
        apex_ns = int(review["current_apex_time_ns"])
        if not int(analysis_start_ns) <= apex_ns <= int(analysis_end_ns):
            continue
        auto_identity = review.get("auto_event_id") or review.get("original_auto_event_id")
        evidence = by_identity.get(str(auto_identity)) if auto_identity else None
        rows.append(
            _machine_row(
                review,
                evidence,
                source_sha256=source_sha256,
                detector_version=detector_version,
                parameter_hash=parameter_hash,
            )
        )
    rows.sort(key=lambda row: (int(row["current_apex_time_ns"]), str(row["event_id"])))
    extras = sorted({key for row in rows for key in row}.difference(MACHINE_REQUIRED_COLUMNS))
    columns = list(MACHINE_REQUIRED_COLUMNS) + extras
    table = pd.DataFrame(rows, columns=columns)
    if table.empty:
        for column in columns:
            table[column] = pd.Series(dtype="object")

    published = False
    try:
        staging.mkdir(parents=False, exist_ok=False)
        event_table = staging / "events.parquet"
        table.to_parquet(event_table, index=False)
        table_sha = _sha256(event_table)
        status_counts = {
            str(status): int(count)
            for status, count in sorted(table["status"].value_counts().to_dict().items())
        }
        created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
        manifest = {
            "schema": "ms-event-machine-contract-v1",
            "created_at": created_at,
            "source_fingerprint": source_fingerprint,
            "detector": {
                "version": detector_version,
                "parameter_hash": parameter_hash,
                "generation_id": generation_id,
            },
            "analysis_range": {
                "start_ns": int(analysis_start_ns),
                "end_ns": int(analysis_end_ns),
                "boundary_rule": boundary_rule,
                "ownership": "current_apex",
            },
            "event_table": {
                "path": "events.parquet",
                "format": "parquet",
                "sha256": table_sha,
                "size_bytes": event_table.stat().st_size,
                "row_count": len(table),
                "columns": columns,
                "dtypes": {column: str(table[column].dtype) for column in columns},
            },
            "status_counts": status_counts,
            "status_policy": "all statuses retained; consumers must filter explicitly",
            "checksum_sidecar": {
                "path": "checksums.sha256",
                "format": "SHA-256 two-space filename",
            },
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(
            json.dumps(json_value(manifest), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        manifest_sha = _sha256(manifest_path)
        checksum_path = staging / "checksums.sha256"
        checksum_path.write_text(
            f"{table_sha}  events.parquet\n{manifest_sha}  manifest.json\n",
            encoding="ascii",
            newline="\n",
        )
        if _sha256(event_table) != manifest["event_table"]["sha256"]:
            raise RuntimeError("machine event table changed before publication")
        if target_was_empty:
            target.rmdir()
        try:
            os.replace(staging, target)
        except Exception:
            if target_was_empty and not target.exists():
                target.mkdir()
            raise
        published = True
        return MachineExportResult(
            output_dir=target,
            event_table_path=target / "events.parquet",
            manifest_path=target / "manifest.json",
            checksum_path=target / "checksums.sha256",
            row_count=len(table),
            event_table_sha256=table_sha,
            manifest_sha256=manifest_sha,
        )
    finally:
        if not published and staging.exists():
            _safe_cleanup_export(staging, target.parent, target.name)
