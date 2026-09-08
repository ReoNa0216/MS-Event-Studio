"""Explicit, range-owned human and machine exports."""

from __future__ import annotations

import csv
import hashlib
import os
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from flame_ms_core.export import (MachineExportResult, MACHINE_REQUIRED_COLUMNS,
    _machine_row, _safe_cleanup_export, export_machine_contract)


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
        if event.get("generation_state") == "stale":
            continue
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
