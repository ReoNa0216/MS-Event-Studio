"""SQLite-backed review overlay with append-only audit and durable history."""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np
import pandas as pd
from scipy.signal import find_peaks

from .canonical import canonical_json, json_value
from .errors import ExistingEventNavigation, ProjectValidationError, ReviewConflict, SnapError
from .identity import new_event_id


REVIEW_SCHEMA_VERSION = "review-schema-v1"
VALID_STATUSES = frozenset({"unreviewed", "accepted", "pending", "rejected"})
BULK_SET_STATUS_ACTION = "bulk_set_status"
_BULK_COMMAND_EVENT_ID = "__bulk__"


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE automatic_evidence (
    auto_event_id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL,
    evidence_json TEXT NOT NULL
);

CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL,
    auto_event_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('unreviewed', 'accepted', 'pending', 'rejected')),
    origin TEXT NOT NULL CHECK (origin IN ('automatic', 'manual_added', 'manual_adjusted')),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    state_json TEXT NOT NULL,
    FOREIGN KEY (auto_event_id) REFERENCES automatic_evidence(auto_event_id)
);

CREATE INDEX events_apex_status_idx ON events(status, event_id);

CREATE TABLE commands (
    command_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    action TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT,
    applied INTEGER NOT NULL CHECK (applied IN (0, 1)),
    redoable INTEGER NOT NULL CHECK (redoable IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE audit_events (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    project_id TEXT NOT NULL,
    event_id TEXT,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    session_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT,
    details_json TEXT NOT NULL
);

CREATE TRIGGER audit_events_no_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;

CREATE TRIGGER audit_events_no_delete
BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_load(payload: str | None) -> dict[str, Any] | None:
    return None if payload is None else json.loads(payload)


def _event_id_for_automatic(project_id: str, auto_event_id: str) -> str:
    # The confirmed contract requires a persistent UUIDv4 at first introduction,
    # not an order-derived or content-derived identifier.  The arguments remain
    # explicit to make that boundary visible to callers and future migrations.
    del project_id, auto_event_id
    return new_event_id()


def _automatic_state(project_id: str, generation: str, row: dict[str, Any]) -> dict[str, Any]:
    auto_identity = str(row["auto_event_id"])
    scan_time_ns = int(row["scan_time_ns"])
    apex_sec = float(row.get("apex_time_sec", scan_time_ns / 1_000_000_000))
    left_sec = float(row["left_sec"])
    right_sec = float(row["right_sec"])
    left_ns = int(row.get("left_time_ns", round(left_sec * 1_000_000_000)))
    right_ns = int(row.get("right_time_ns", round(right_sec * 1_000_000_000)))
    local_interval_sec = float(
        row.get("local_scan_interval_sec", max(np.finfo(float).eps, (right_sec - left_sec) / 2.0))
    )
    state = {
        "event_id": _event_id_for_automatic(project_id, auto_identity),
        "auto_event_id": auto_identity,
        "original_auto_event_id": auto_identity,
        "generation_id": str(row.get("generation_id", generation)),
        "original_scan_id": str(row["scan_id"]),
        "original_scan_row_index": int(row["scan_row_index"]),
        "original_spectrum_index": int(row["spectrum_index"]),
        "original_apex_time_ns": scan_time_ns,
        "original_apex_time_sec": apex_sec,
        "original_apex_intensity": float(row["apex_intensity"]),
        "original_left_time_ns": left_ns,
        "original_right_time_ns": right_ns,
        "original_left_sec": left_sec,
        "original_right_sec": right_sec,
        "original_local_scan_interval_sec": local_interval_sec,
        "current_auto_scan_id": str(row["scan_id"]),
        "current_auto_scan_row_index": int(row["scan_row_index"]),
        "current_auto_spectrum_index": int(row["spectrum_index"]),
        "current_auto_apex_time_ns": scan_time_ns,
        "current_auto_left_time_ns": left_ns,
        "current_auto_right_time_ns": right_ns,
        "current_auto_local_scan_interval_sec": local_interval_sec,
        "current_scan_id": str(row["scan_id"]),
        "current_scan_row_index": int(row["scan_row_index"]),
        "current_spectrum_index": int(row["spectrum_index"]),
        "current_apex_time_ns": scan_time_ns,
        "current_apex_time_sec": apex_sec,
        "current_apex_intensity": float(row["apex_intensity"]),
        "status": "unreviewed",
        "origin": "automatic",
        "revision": 0,
        "snap_offset_sec": 0.0,
    }
    return state


class ReviewStore:
    """Short-transaction review database safe for multiple UI sessions."""

    def __init__(self, path: Path, *, project_id: str, generation_id: str):
        self.path = path.resolve()
        self.project_id = project_id
        self.generation_id = generation_id

    @classmethod
    def create(
        cls,
        path: str | Path,
        *,
        project_id: str,
        generation_id: str,
        automatic_events: Iterable[dict[str, Any]],
    ) -> "ReviewStore":
        database = Path(path).resolve()
        if database.exists():
            raise FileExistsError(f"review database already exists: {database}")
        database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database, timeout=30.0)
        try:
            connection.executescript(SCHEMA_SQL)
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                (
                    ("schema_version", REVIEW_SCHEMA_VERSION),
                    ("project_id", str(project_id)),
                    ("generation_id", str(generation_id)),
                ),
            )
            for raw_row in automatic_events:
                row = dict(raw_row)
                state = _automatic_state(project_id, generation_id, row)
                connection.execute(
                    "INSERT INTO automatic_evidence(auto_event_id, generation_id, evidence_json) VALUES (?, ?, ?)",
                    (
                        state["auto_event_id"],
                        state["generation_id"],
                        canonical_json(row),
                    ),
                )
                connection.execute(
                    """INSERT INTO events(
                           event_id, generation_id, auto_event_id, status, origin, revision, state_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        state["event_id"],
                        state["generation_id"],
                        state["auto_event_id"],
                        state["status"],
                        state["origin"],
                        state["revision"],
                        canonical_json(state),
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            try:
                database.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        finally:
            if connection:
                connection.close()
        return cls(database, project_id=str(project_id), generation_id=str(generation_id))

    @classmethod
    def open(cls, path: str | Path, *, project_id: str) -> "ReviewStore":
        database = Path(path).resolve()
        if not database.is_file():
            raise ProjectValidationError(f"review database is missing: {database}")
        connection = cls._new_connection(database)
        try:
            metadata = dict(connection.execute("SELECT key, value FROM metadata").fetchall())
        except sqlite3.DatabaseError as exc:
            raise ProjectValidationError(f"invalid review database: {exc}") from exc
        finally:
            connection.close()
        if metadata.get("schema_version") != REVIEW_SCHEMA_VERSION:
            raise ProjectValidationError("unsupported review database schema")
        if metadata.get("project_id") != str(project_id):
            raise ProjectValidationError("review database project_id binding mismatch")
        generation = metadata.get("generation_id")
        if not generation:
            raise ProjectValidationError("review database generation binding is missing")
        return cls(database, project_id=str(project_id), generation_id=generation)

    @staticmethod
    def _new_connection(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._new_connection(self.path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _state(connection: sqlite3.Connection, event_id: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT state_json FROM events WHERE event_id = ?",
            (str(event_id),),
        ).fetchone()
        if row is None:
            raise ReviewConflict(f"event does not exist: {event_id}")
        return json.loads(row["state_json"])

    @staticmethod
    def _store_state(connection: sqlite3.Connection, state: dict[str, Any]) -> None:
        cursor = connection.execute(
            """UPDATE events
               SET generation_id = ?, auto_event_id = ?, status = ?, origin = ?, revision = ?, state_json = ?
               WHERE event_id = ?""",
            (
                state["generation_id"],
                state.get("auto_event_id"),
                state["status"],
                state["origin"],
                int(state["revision"]),
                canonical_json(state),
                state["event_id"],
            ),
        )
        if cursor.rowcount != 1:
            raise ReviewConflict(f"event disappeared during update: {state['event_id']}")

    def _append_audit(
        self,
        connection: sqlite3.Connection,
        *,
        event_id: str | None,
        action: str,
        actor: str,
        session_id: str,
        reason: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO audit_events(
                   occurred_at, project_id, event_id, action, actor, session_id,
                   reason, before_json, after_json, details_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _now(),
                self.project_id,
                event_id,
                action,
                str(actor),
                str(session_id),
                str(reason or ""),
                canonical_json(before) if before is not None else None,
                canonical_json(after) if after is not None else None,
                canonical_json(details or {}),
            ),
        )

    def _normal_update(
        self,
        *,
        event_id: str,
        expected_revision: int,
        action: str,
        actor: str,
        session_id: str,
        reason: str,
        mutate,
    ) -> dict[str, Any]:
        with self._transaction() as connection:
            before = self._state(connection, event_id)
            if int(before["revision"]) != int(expected_revision):
                raise ReviewConflict(
                    f"event revision conflict: expected {expected_revision}, found {before['revision']}"
                )
            after = dict(before)
            mutate(after)
            after["revision"] = int(before["revision"]) + 1
            if after["status"] not in VALID_STATUSES:
                raise ValueError(f"invalid review status: {after['status']}")
            connection.execute(
                "UPDATE commands SET redoable = 0 WHERE applied = 0 AND redoable = 1"
            )
            self._store_state(connection, after)
            timestamp = _now()
            connection.execute(
                """INSERT INTO commands(
                       event_id, action, before_json, after_json, applied, redoable, created_at
                   ) VALUES (?, ?, ?, ?, 1, 1, ?)""",
                (
                    event_id,
                    action,
                    canonical_json(before),
                    canonical_json(after),
                    timestamp,
                ),
            )
            self._append_audit(
                connection,
                event_id=event_id,
                action=action,
                actor=actor,
                session_id=session_id,
                reason=reason,
                before=before,
                after=after,
            )
            return after

    def list_events(self) -> list[dict[str, Any]]:
        connection = self._new_connection(self.path)
        try:
            rows = connection.execute("SELECT state_json FROM events").fetchall()
        finally:
            connection.close()
        states = [json.loads(row["state_json"]) for row in rows]
        states.sort(key=lambda item: (int(item["current_apex_time_ns"]), item["event_id"]))
        return states

    def audit_events(self) -> list[dict[str, Any]]:
        connection = self._new_connection(self.path)
        try:
            rows = connection.execute("SELECT * FROM audit_events ORDER BY audit_id").fetchall()
        finally:
            connection.close()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for field in ("before_json", "after_json", "details_json"):
                item[field.removesuffix("_json")] = _json_load(item.pop(field))
            result.append(item)
        return result

    def set_status(
        self,
        event_id: str,
        status: str,
        *,
        expected_revision: int,
        actor: str,
        session_id: str,
        reason: str = "",
    ) -> dict[str, Any]:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid review status: {status}")
        return self._normal_update(
            event_id=event_id,
            expected_revision=expected_revision,
            action="set_status",
            actor=actor,
            session_id=session_id,
            reason=reason,
            mutate=lambda state: state.update(status=status),
        )

    def set_status_bulk(
        self,
        updates: Iterable[tuple[str, int]],
        status: str,
        *,
        actor: str,
        session_id: str,
        reason: str = "",
    ) -> list[dict[str, Any]]:
        """Apply one status to several events as one durable command.

        Every expected revision is checked before the first row is written.
        The command is therefore all-or-nothing and a single undo/redo step.
        """

        if status not in VALID_STATUSES:
            raise ValueError(f"invalid review status: {status}")
        requested = [(str(event_id), int(revision)) for event_id, revision in updates]
        if len({event_id for event_id, _revision in requested}) != len(requested):
            raise ValueError("bulk status update contains duplicate events")
        if not requested:
            return []

        with self._transaction() as connection:
            before_states: list[dict[str, Any]] = []
            after_states: list[dict[str, Any]] = []
            for event_id, expected_revision in requested:
                before = self._state(connection, event_id)
                if int(before["revision"]) != expected_revision:
                    raise ReviewConflict(
                        "event revision conflict in bulk update: "
                        f"expected {expected_revision}, found {before['revision']}"
                    )
                after = dict(before)
                after["status"] = status
                after["revision"] = int(before["revision"]) + 1
                before_states.append(before)
                after_states.append(after)

            connection.execute(
                "UPDATE commands SET redoable = 0 WHERE applied = 0 AND redoable = 1"
            )
            for after in after_states:
                self._store_state(connection, after)
            timestamp = _now()
            connection.execute(
                """INSERT INTO commands(
                       event_id, action, before_json, after_json, applied, redoable, created_at
                   ) VALUES (?, ?, ?, ?, 1, 1, ?)""",
                (
                    _BULK_COMMAND_EVENT_ID,
                    BULK_SET_STATUS_ACTION,
                    canonical_json({"events": before_states}),
                    canonical_json({"events": after_states}),
                    timestamp,
                ),
            )
            for before, after in zip(before_states, after_states):
                self._append_audit(
                    connection,
                    event_id=str(before["event_id"]),
                    action=BULK_SET_STATUS_ACTION,
                    actor=actor,
                    session_id=session_id,
                    reason=reason,
                    before=before,
                    after=after,
                    details={"event_count": len(after_states)},
                )
            return after_states

    def restore(
        self,
        event_id: str,
        *,
        expected_revision: int,
        actor: str,
        session_id: str,
        reason: str = "",
    ) -> dict[str, Any]:
        def mutate(state: dict[str, Any]) -> None:
            if state.get("original_auto_event_id"):
                state.update(
                    current_scan_id=state["original_scan_id"],
                    current_scan_row_index=state["original_scan_row_index"],
                    current_spectrum_index=state["original_spectrum_index"],
                    current_apex_time_ns=state["original_apex_time_ns"],
                    current_apex_time_sec=state["original_apex_time_sec"],
                    current_apex_intensity=state["original_apex_intensity"],
                    status="unreviewed",
                    origin="automatic",
                    snap_offset_sec=0.0,
                )
            else:
                state.update(
                    current_scan_id=state["created_scan_id"],
                    current_scan_row_index=state["created_scan_row_index"],
                    current_spectrum_index=state["created_spectrum_index"],
                    current_apex_time_ns=state["created_apex_time_ns"],
                    current_apex_time_sec=state["created_apex_time_sec"],
                    current_apex_intensity=state["created_apex_intensity"],
                    status="accepted",
                    origin="manual_added",
                    snap_offset_sec=state.get("created_snap_offset_sec", 0.0),
                )

        return self._normal_update(
            event_id=event_id,
            expected_revision=expected_revision,
            action="restore",
            actor=actor,
            session_id=session_id,
            reason=reason,
            mutate=mutate,
        )

    def restore_automatic_apex(
        self,
        event_id: str,
        *,
        expected_revision: int,
        actor: str,
        session_id: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """Restore only immutable automatic apex evidence, preserving review status.

        The legacy :meth:`restore` operation intentionally retains its original
        combined semantics for existing callers.  The WebView review workflow
        needs a narrower domain action: returning an adjusted automatic event
        to its detected apex must not silently clear or otherwise change the
        scientist's review decision.
        """

        def mutate(state: dict[str, Any]) -> None:
            if not state.get("original_auto_event_id"):
                raise ValueError("manual events do not have an automatic apex")
            if (
                int(state["current_apex_time_ns"]) == int(state["original_apex_time_ns"])
                and int(state["current_scan_row_index"])
                == int(state["original_scan_row_index"])
            ):
                raise ValueError("the automatic apex is already active")
            status = state["status"]
            state.update(
                current_scan_id=state["original_scan_id"],
                current_scan_row_index=state["original_scan_row_index"],
                current_spectrum_index=state["original_spectrum_index"],
                current_apex_time_ns=state["original_apex_time_ns"],
                current_apex_time_sec=state["original_apex_time_sec"],
                current_apex_intensity=state["original_apex_intensity"],
                status=status,
                origin="automatic",
                snap_offset_sec=0.0,
            )

        return self._normal_update(
            event_id=event_id,
            expected_revision=expected_revision,
            action="restore_automatic_apex",
            actor=actor,
            session_id=session_id,
            reason=reason,
            mutate=mutate,
        )

    def history_state(self) -> dict[str, bool]:
        """Return whether the durable command history can move backward/forward."""

        connection = self._new_connection(self.path)
        try:
            connection.execute("BEGIN")
            can_undo = connection.execute(
                "SELECT 1 FROM commands WHERE applied = 1 AND redoable = 1 LIMIT 1"
            ).fetchone()
            can_redo = connection.execute(
                "SELECT 1 FROM commands WHERE applied = 0 AND redoable = 1 LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
        return {"can_undo": can_undo is not None, "can_redo": can_redo is not None}

    @staticmethod
    def _snap(
        *,
        click_time_sec: float,
        scans: pd.DataFrame,
        minimum_sec: float | None = None,
        maximum_sec: float | None = None,
    ) -> tuple[pd.Series, float]:
        required = {
            "scan_id",
            "scan_row_index",
            "spectrum_index",
            "scan_time_ns",
            "scan_start_time_sec",
            "primary_marker_max_intensity",
        }
        missing = required.difference(scans.columns)
        if missing:
            raise SnapError(f"scan evidence is missing: {', '.join(sorted(missing))}")
        if not math.isfinite(float(click_time_sec)):
            raise SnapError("click time must be finite")
        times = scans["scan_start_time_sec"].to_numpy(dtype=float)
        if len(times) < 3 or np.any(np.diff(times) <= 0):
            raise SnapError("scan times must contain at least three increasing samples")
        signal = scans["primary_marker_max_intensity"].to_numpy(dtype=float)
        peaks, _ = find_peaks(signal, height=np.nextafter(0.0, 1.0))
        if not len(peaks):
            raise SnapError("no real positive local peak is available for snapping")

        nearest = int(np.argmin(np.abs(times - float(click_time_sec))))
        local_start = max(0, nearest - 10)
        local_end = min(len(times) - 1, nearest + 10)
        local_differences = np.diff(times[local_start : local_end + 1])
        positive = local_differences[local_differences > 0]
        if not len(positive):
            positive = np.diff(times)
        local_median_interval = float(np.median(positive))
        global_differences = np.diff(times)
        global_positive = global_differences[global_differences > 0]
        global_median_interval = float(np.median(global_positive))
        radius = 2.0 * local_median_interval

        insertion = int(np.searchsorted(times, float(click_time_sec), side="left"))
        if 0 < insertion < len(times):
            left_time = float(times[insertion - 1])
            right_time = float(times[insertion])
            if (
                left_time < float(click_time_sec) < right_time
                and right_time - left_time > 3.0 * global_median_interval
            ):
                raise SnapError("click lies inside a scan gap and cannot be snapped across it")

        candidates: list[int] = []
        for candidate in peaks:
            candidate_time = float(times[candidate])
            if abs(candidate_time - float(click_time_sec)) > radius + np.finfo(float).eps:
                continue
            if minimum_sec is not None and candidate_time < minimum_sec:
                continue
            if maximum_sec is not None and candidate_time > maximum_sec:
                continue
            lower = min(nearest, int(candidate))
            upper = max(nearest, int(candidate))
            if upper > lower and np.any(
                np.diff(times[lower : upper + 1]) > 3.0 * global_median_interval
            ):
                continue
            candidates.append(int(candidate))
        if not candidates:
            raise SnapError("no real local peak satisfies the snap radius and gap rule")
        offsets = {
            index: abs(float(times[index]) - float(click_time_sec)) for index in candidates
        }
        minimum_offset = min(offsets.values())
        nearest_candidates = [
            index
            for index, offset in offsets.items()
            if math.isclose(offset, minimum_offset, rel_tol=0.0, abs_tol=1e-12)
        ]
        if len(nearest_candidates) != 1:
            raise SnapError("ambiguous equidistant local peaks; no event was added")
        chosen = nearest_candidates[0]
        return scans.iloc[chosen], float(times[chosen] - float(click_time_sec))

    def add_event(
        self,
        *,
        click_time_sec: float,
        scans: pd.DataFrame,
        analysis_start_ns: int,
        analysis_end_ns: int,
        actor: str,
        session_id: str,
        reason: str = "",
    ) -> dict[str, Any]:
        snapped, offset = self._snap(click_time_sec=click_time_sec, scans=scans)
        apex_ns = int(snapped["scan_time_ns"])
        if not int(analysis_start_ns) <= apex_ns <= int(analysis_end_ns):
            raise SnapError("snapped apex is outside the closed analysis range")
        event_id = new_event_id()
        state = {
            "event_id": event_id,
            "auto_event_id": None,
            "original_auto_event_id": None,
            "generation_id": self.generation_id,
            "created_scan_id": str(snapped["scan_id"]),
            "created_scan_row_index": int(snapped["scan_row_index"]),
            "created_spectrum_index": int(snapped["spectrum_index"]),
            "created_apex_time_ns": apex_ns,
            "created_apex_time_sec": float(snapped["scan_start_time_sec"]),
            "created_apex_intensity": float(snapped["primary_marker_max_intensity"]),
            "created_snap_offset_sec": offset,
            "current_scan_id": str(snapped["scan_id"]),
            "current_scan_row_index": int(snapped["scan_row_index"]),
            "current_spectrum_index": int(snapped["spectrum_index"]),
            "current_apex_time_ns": apex_ns,
            "current_apex_time_sec": float(snapped["scan_start_time_sec"]),
            "current_apex_intensity": float(snapped["primary_marker_max_intensity"]),
            "status": "accepted",
            "origin": "manual_added",
            "revision": 0,
            "snap_offset_sec": offset,
        }
        with self._transaction() as connection:
            existing = [json.loads(row[0]) for row in connection.execute("SELECT state_json FROM events")]
            owners: set[str] = {
                str(item["event_id"])
                for item in existing
                if item["current_scan_id"] == state["current_scan_id"]
            }
            snapped_sec = float(state["current_apex_time_sec"])
            for item in existing:
                if (
                    item.get("original_auto_event_id")
                    and float(item["original_left_sec"])
                    <= snapped_sec
                    <= float(item["original_right_sec"])
                ):
                    owners.add(str(item["event_id"]))
            if len(owners) == 1:
                raise ExistingEventNavigation(next(iter(owners)))
            if len(owners) > 1:
                raise SnapError("snapped evidence overlaps multiple existing event supports")
            connection.execute(
                "UPDATE commands SET redoable = 0 WHERE applied = 0 AND redoable = 1"
            )
            connection.execute(
                """INSERT INTO events(
                       event_id, generation_id, auto_event_id, status, origin, revision, state_json
                   ) VALUES (?, ?, NULL, ?, ?, 0, ?)""",
                (
                    event_id,
                    self.generation_id,
                    state["status"],
                    state["origin"],
                    canonical_json(state),
                ),
            )
            timestamp = _now()
            connection.execute(
                """INSERT INTO commands(
                       event_id, action, before_json, after_json, applied, redoable, created_at
                   ) VALUES (?, 'add_event', NULL, ?, 1, 1, ?)""",
                (event_id, canonical_json(state), timestamp),
            )
            self._append_audit(
                connection,
                event_id=event_id,
                action="add_event",
                actor=actor,
                session_id=session_id,
                reason=reason,
                before=None,
                after=state,
                details={"click_time_sec": click_time_sec, "snap_offset_sec": offset},
            )
        return state

    def adjust_apex(
        self,
        event_id: str,
        *,
        click_time_sec: float,
        scans: pd.DataFrame,
        analysis_start_ns: int,
        analysis_end_ns: int,
        expected_revision: int,
        actor: str,
        session_id: str,
        reason: str = "",
    ) -> dict[str, Any]:
        if int(analysis_end_ns) < int(analysis_start_ns):
            raise ValueError("analysis range end precedes start")
        connection = self._new_connection(self.path)
        try:
            current = self._state(connection, event_id)
        finally:
            connection.close()
        minimum = maximum = None
        if current.get("original_auto_event_id"):
            minimum = float(current["original_left_sec"])
            maximum = float(current["original_right_sec"])
            if not minimum <= float(click_time_sec) <= maximum:
                raise SnapError("automatic adjustment cannot leave immutable original support")
        snapped, offset = self._snap(
            click_time_sec=click_time_sec,
            scans=scans,
            minimum_sec=minimum,
            maximum_sec=maximum,
        )
        snapped_ns = int(snapped["scan_time_ns"])
        if not int(analysis_start_ns) <= snapped_ns <= int(analysis_end_ns):
            raise SnapError("adjusted apex is outside the closed analysis range")

        def mutate(state: dict[str, Any]) -> None:
            state.update(
                current_scan_id=str(snapped["scan_id"]),
                current_scan_row_index=int(snapped["scan_row_index"]),
                current_spectrum_index=int(snapped["spectrum_index"]),
                current_apex_time_ns=snapped_ns,
                current_apex_time_sec=float(snapped["scan_start_time_sec"]),
                current_apex_intensity=float(snapped["primary_marker_max_intensity"]),
                origin="manual_adjusted",
                snap_offset_sec=offset,
            )

        return self._normal_update(
            event_id=event_id,
            expected_revision=expected_revision,
            action="adjust_apex",
            actor=actor,
            session_id=session_id,
            reason=reason,
            mutate=mutate,
        )

    def undo(self, *, actor: str, session_id: str, reason: str = "") -> dict[str, Any] | None:
        with self._transaction() as connection:
            command = connection.execute(
                """SELECT * FROM commands
                   WHERE applied = 1 AND redoable = 1
                   ORDER BY command_id DESC LIMIT 1"""
            ).fetchone()
            if command is None:
                raise ReviewConflict("there is no command to undo")
            if command["action"] == BULK_SET_STATUS_ACTION:
                payload = _json_load(command["before_json"]) or {}
                targets = payload.get("events")
                if not isinstance(targets, list) or not targets:
                    raise ProjectValidationError("bulk undo command is missing event states")
                restored: list[dict[str, Any]] = []
                for stored in targets:
                    if not isinstance(stored, dict):
                        raise ProjectValidationError("bulk undo command contains an invalid state")
                    current = self._state(connection, str(stored.get("event_id", "")))
                    target = dict(stored)
                    target["revision"] = int(current["revision"]) + 1
                    self._store_state(connection, target)
                    restored.append(target)
                    self._append_audit(
                        connection,
                        event_id=str(target["event_id"]),
                        action="undo",
                        actor=actor,
                        session_id=session_id,
                        reason=reason,
                        before=current,
                        after=target,
                        details={
                            "command_id": command["command_id"],
                            "original_action": command["action"],
                            "event_count": len(targets),
                        },
                    )
                connection.execute(
                    "UPDATE commands SET applied = 0 WHERE command_id = ?",
                    (command["command_id"],),
                )
                return restored[0]
            current = self._state(connection, command["event_id"])
            target = _json_load(command["before_json"])
            if target is None:
                connection.execute("DELETE FROM events WHERE event_id = ?", (command["event_id"],))
                after = None
            else:
                target["revision"] = int(current["revision"]) + 1
                self._store_state(connection, target)
                after = target
            connection.execute(
                "UPDATE commands SET applied = 0 WHERE command_id = ?",
                (command["command_id"],),
            )
            self._append_audit(
                connection,
                event_id=command["event_id"],
                action="undo",
                actor=actor,
                session_id=session_id,
                reason=reason,
                before=current,
                after=after,
                details={"command_id": command["command_id"], "original_action": command["action"]},
            )
            return after

    def redo(self, *, actor: str, session_id: str, reason: str = "") -> dict[str, Any]:
        with self._transaction() as connection:
            command = connection.execute(
                """SELECT * FROM commands
                   WHERE applied = 0 AND redoable = 1
                   ORDER BY command_id ASC LIMIT 1"""
            ).fetchone()
            if command is None:
                raise ReviewConflict("there is no command to redo")
            if command["action"] == BULK_SET_STATUS_ACTION:
                payload = _json_load(command["after_json"]) or {}
                targets = payload.get("events")
                if not isinstance(targets, list) or not targets:
                    raise ProjectValidationError("bulk redo command is missing event states")
                restored: list[dict[str, Any]] = []
                for stored in targets:
                    if not isinstance(stored, dict):
                        raise ProjectValidationError("bulk redo command contains an invalid state")
                    current = self._state(connection, str(stored.get("event_id", "")))
                    target = dict(stored)
                    target["revision"] = int(current["revision"]) + 1
                    self._store_state(connection, target)
                    restored.append(target)
                    self._append_audit(
                        connection,
                        event_id=str(target["event_id"]),
                        action="redo",
                        actor=actor,
                        session_id=session_id,
                        reason=reason,
                        before=current,
                        after=target,
                        details={
                            "command_id": command["command_id"],
                            "original_action": command["action"],
                            "event_count": len(targets),
                        },
                    )
                connection.execute(
                    "UPDATE commands SET applied = 1 WHERE command_id = ?",
                    (command["command_id"],),
                )
                return restored[0]
            target = _json_load(command["after_json"])
            if target is None:
                raise ProjectValidationError("redo command is missing its after state")
            existing = connection.execute(
                "SELECT state_json FROM events WHERE event_id = ?", (command["event_id"],)
            ).fetchone()
            if existing is None:
                target["revision"] = int(target.get("revision", 0)) + 1
                connection.execute(
                    """INSERT INTO events(
                           event_id, generation_id, auto_event_id, status, origin, revision, state_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        target["event_id"],
                        target["generation_id"],
                        target.get("auto_event_id"),
                        target["status"],
                        target["origin"],
                        target["revision"],
                        canonical_json(target),
                    ),
                )
                before = None
            else:
                before = json.loads(existing["state_json"])
                target["revision"] = int(before["revision"]) + 1
                self._store_state(connection, target)
            connection.execute(
                "UPDATE commands SET applied = 1 WHERE command_id = ?",
                (command["command_id"],),
            )
            self._append_audit(
                connection,
                event_id=command["event_id"],
                action="redo",
                actor=actor,
                session_id=session_id,
                reason=reason,
                before=before,
                after=target,
                details={"command_id": command["command_id"], "original_action": command["action"]},
            )
            return target

    def record_export(
        self,
        *,
        actor: str,
        session_id: str,
        details: dict[str, Any],
        reason: str = "",
    ) -> None:
        with self._transaction() as connection:
            self._append_audit(
                connection,
                event_id=None,
                action="export",
                actor=actor,
                session_id=session_id,
                reason=reason,
                before=None,
                after=None,
                details=details,
            )

    def close(self) -> None:
        """Connections are per-operation; retained for context-manager symmetry."""

    def __enter__(self) -> "ReviewStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
