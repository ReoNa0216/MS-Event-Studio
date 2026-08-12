"""Pure, review-preserving generation reconciliation proposals.

This module never mutates review state.  A proposal is intended for a diff
preview; applying it and reusing EventID requires an explicit later confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class ReconciliationMapping:
    event_id: str
    old_auto_event_id: str
    new_auto_event_id: str
    method: str
    distance_sec: float
    requires_confirmation: bool


@dataclass(frozen=True, slots=True)
class ReconciliationPlan:
    mappings: tuple[ReconciliationMapping, ...]
    stale_event_ids: tuple[str, ...]
    ambiguous_event_ids: tuple[str, ...]
    unmatched_new_auto_event_ids: tuple[str, ...]
    manual_event_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _OldEvidence:
    event_id: str
    auto_event_id: str
    identity: tuple[str, int, int, int]
    apex_ns: int
    left_ns: int
    right_ns: int
    interval_ns: int


@dataclass(frozen=True, slots=True)
class _NewEvidence:
    auto_event_id: str
    identity: tuple[str, int, int, int]
    apex_ns: int
    left_ns: int
    right_ns: int
    interval_ns: int


def _integer(row: dict[str, Any], *names: str) -> int:
    for name in names:
        if name in row and row[name] is not None:
            return int(row[name])
    raise ValueError(f"reconciliation evidence is missing one of: {', '.join(names)}")


def _text(row: dict[str, Any], *names: str) -> str:
    for name in names:
        if name in row and row[name] is not None:
            return str(row[name])
    raise ValueError(f"reconciliation evidence is missing one of: {', '.join(names)}")


def _interval_ns(row: dict[str, Any], *names: str) -> int:
    for name in names:
        if name in row and row[name] is not None:
            value = int(round(float(row[name]) * 1_000_000_000))
            if value <= 0:
                raise ValueError("local scan interval must be positive")
            return value
    raise ValueError("reconciliation evidence is missing local scan interval")


def _old_evidence(row: dict[str, Any]) -> _OldEvidence:
    scan_id = _text(row, "current_auto_scan_id", "original_scan_id")
    spectrum_index = _integer(
        row, "current_auto_spectrum_index", "original_spectrum_index"
    )
    scan_row_index = _integer(
        row, "current_auto_scan_row_index", "original_scan_row_index"
    )
    apex_ns = _integer(
        row, "current_auto_apex_time_ns", "original_apex_time_ns"
    )
    return _OldEvidence(
        event_id=_text(row, "event_id"),
        auto_event_id=_text(row, "auto_event_id", "original_auto_event_id"),
        identity=(scan_id, spectrum_index, scan_row_index, apex_ns),
        apex_ns=apex_ns,
        left_ns=_integer(row, "current_auto_left_time_ns", "original_left_time_ns"),
        right_ns=_integer(row, "current_auto_right_time_ns", "original_right_time_ns"),
        interval_ns=_interval_ns(
            row,
            "current_auto_local_scan_interval_sec",
            "original_local_scan_interval_sec",
        ),
    )


def _new_evidence(row: dict[str, Any]) -> _NewEvidence:
    apex_ns = _integer(row, "scan_time_ns")
    return _NewEvidence(
        auto_event_id=_text(row, "auto_event_id"),
        identity=(
            _text(row, "scan_id"),
            _integer(row, "spectrum_index"),
            _integer(row, "scan_row_index"),
            apex_ns,
        ),
        apex_ns=apex_ns,
        left_ns=_integer(row, "left_time_ns"),
        right_ns=_integer(row, "right_time_ns"),
        interval_ns=_interval_ns(row, "local_scan_interval_sec"),
    )


def _validate_unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {label} in reconciliation input")


def _eligible(old: _OldEvidence, new: _NewEvidence) -> bool:
    supports_overlap = old.left_ns <= new.right_ns and new.left_ns <= old.right_ns
    radius_ns = 2 * max(old.interval_ns, new.interval_ns)
    return supports_overlap and abs(old.apex_ns - new.apex_ns) <= radius_ns


def _unique_minimum(candidates: list[tuple[str, int]]) -> str | None:
    if not candidates:
        return None
    minimum = min(distance for _, distance in candidates)
    winners = [identity for identity, distance in candidates if distance == minimum]
    return winners[0] if len(winners) == 1 else None


def propose_reconciliation(
    existing_events: Iterable[dict[str, Any]],
    new_automatic_events: Iterable[dict[str, Any]],
) -> ReconciliationPlan:
    """Build a deterministic one-to-one proposal without modifying either input."""

    raw_old = [dict(row) for row in existing_events]
    manual_ids = sorted(
        str(row["event_id"])
        for row in raw_old
        if not row.get("auto_event_id") or row.get("origin") == "manual_added"
    )
    old = sorted(
        (_old_evidence(row) for row in raw_old if row.get("auto_event_id")),
        key=lambda item: item.event_id,
    )
    new = sorted(
        (_new_evidence(dict(row)) for row in new_automatic_events),
        key=lambda item: item.auto_event_id,
    )
    _validate_unique([item.event_id for item in old], "EventID")
    _validate_unique([item.auto_event_id for item in old], "old auto_event_id")
    _validate_unique([item.auto_event_id for item in new], "new auto_event_id")

    old_by_identity: dict[tuple[str, int, int, int], list[_OldEvidence]] = {}
    new_by_identity: dict[tuple[str, int, int, int], list[_NewEvidence]] = {}
    for item in old:
        old_by_identity.setdefault(item.identity, []).append(item)
    for item in new:
        new_by_identity.setdefault(item.identity, []).append(item)

    mappings: list[ReconciliationMapping] = []
    mapped_old: set[str] = set()
    mapped_new: set[str] = set()
    ambiguous_old: set[str] = set()
    for identity in sorted(set(old_by_identity).intersection(new_by_identity)):
        old_matches = old_by_identity[identity]
        new_matches = new_by_identity[identity]
        if len(old_matches) == 1 and len(new_matches) == 1:
            old_item, new_item = old_matches[0], new_matches[0]
            mappings.append(
                ReconciliationMapping(
                    event_id=old_item.event_id,
                    old_auto_event_id=old_item.auto_event_id,
                    new_auto_event_id=new_item.auto_event_id,
                    method="exact_scan_identity",
                    distance_sec=0.0,
                    requires_confirmation=True,
                )
            )
            mapped_old.add(old_item.event_id)
            mapped_new.add(new_item.auto_event_id)
        else:
            ambiguous_old.update(item.event_id for item in old_matches)

    remaining_old = [item for item in old if item.event_id not in mapped_old]
    remaining_new = [item for item in new if item.auto_event_id not in mapped_new]
    old_candidates: dict[str, list[tuple[str, int]]] = {}
    new_candidates: dict[str, list[tuple[str, int]]] = {}
    by_old_id = {item.event_id: item for item in remaining_old}
    by_new_id = {item.auto_event_id: item for item in remaining_new}
    for old_item in remaining_old:
        for new_item in remaining_new:
            if not _eligible(old_item, new_item):
                continue
            distance = abs(old_item.apex_ns - new_item.apex_ns)
            old_candidates.setdefault(old_item.event_id, []).append(
                (new_item.auto_event_id, distance)
            )
            new_candidates.setdefault(new_item.auto_event_id, []).append(
                (old_item.event_id, distance)
            )

    old_choice = {
        event_id: _unique_minimum(candidates)
        for event_id, candidates in old_candidates.items()
    }
    new_choice = {
        auto_id: _unique_minimum(candidates)
        for auto_id, candidates in new_candidates.items()
    }
    for event_id in sorted(old_choice):
        auto_id = old_choice[event_id]
        if auto_id is None or new_choice.get(auto_id) != event_id:
            if old_candidates.get(event_id):
                ambiguous_old.add(event_id)
            continue
        old_item = by_old_id[event_id]
        new_item = by_new_id[auto_id]
        mappings.append(
            ReconciliationMapping(
                event_id=event_id,
                old_auto_event_id=old_item.auto_event_id,
                new_auto_event_id=auto_id,
                method="mutual_unique_nearest_support",
                distance_sec=abs(old_item.apex_ns - new_item.apex_ns) / 1_000_000_000,
                requires_confirmation=True,
            )
        )
        mapped_old.add(event_id)
        mapped_new.add(auto_id)

    for item in remaining_old:
        if item.event_id not in mapped_old and old_candidates.get(item.event_id):
            ambiguous_old.add(item.event_id)
    mappings.sort(key=lambda item: item.event_id)
    return ReconciliationPlan(
        mappings=tuple(mappings),
        stale_event_ids=tuple(sorted(item.event_id for item in old if item.event_id not in mapped_old)),
        ambiguous_event_ids=tuple(sorted(ambiguous_old)),
        unmatched_new_auto_event_ids=tuple(
            sorted(item.auto_event_id for item in new if item.auto_event_id not in mapped_new)
        ),
        manual_event_ids=tuple(manual_ids),
    )
