"""Previewed, review-preserving analysis-range generation changes.

The new generation is built in a unique directory first.  Publication consists
of one atomic root-manifest replacement, so a crash before that switch leaves
the old generation active and a crash after it leaves a complete new one.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from . import __version__
from .canonical import canonical_json, content_sha256, json_value
from .detector import DETECTOR_VERSION, DetectionResult, detect_events
from .errors import ProjectValidationError
from .parser import PARSER_VERSION
from .paths import resolve_project_path
from .project import MANIFEST_NAME, Project, open_project
from .reconcile import ReconciliationPlan, propose_reconciliation
from .review import REVIEW_SCHEMA_VERSION, ReviewStore, _automatic_state
from .timebase import AnalysisRange


@dataclass(frozen=True, slots=True)
class ReviewSnapshotToken:
    event_count: int
    revision_sum: int
    maximum_audit_id: int
    state_sha256: str


@dataclass(frozen=True, slots=True)
class RangeChangePreview:
    project_dir: Path
    old_generation_id: str
    new_generation_id: str
    old_analysis_range: dict[str, Any]
    new_analysis_range: AnalysisRange
    detection: DetectionResult
    detection_sha256: str
    plan: ReconciliationPlan
    review_snapshot: ReviewSnapshotToken
    manifest_sha256: str
    preview_id: str

    @property
    def mapped_count(self) -> int:
        return len(self.plan.mappings)

    @property
    def stale_count(self) -> int:
        return len(self.plan.stale_event_ids)

    @property
    def new_count(self) -> int:
        return len(self.plan.unmatched_new_auto_event_ids)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _artifact_path(project: Project, role: str) -> Path:
    for record in project.manifest["artifacts"]:
        if record["role"] == role:
            return resolve_project_path(project.project_dir, record["path"])
    raise ProjectValidationError(f"project artifact role is missing: {role}")


def _artifact_record(root: Path, relative: str, role: str, *, mutable: bool) -> dict[str, Any]:
    path = resolve_project_path(root, relative)
    if not path.is_file():
        raise ProjectValidationError(f"range-change artifact is missing: {relative}")
    record: dict[str, Any] = {
        "role": role,
        "path": relative,
        "mutable": bool(mutable),
        "size_bytes_at_creation": path.stat().st_size,
    }
    if not mutable:
        record["sha256"] = _sha256(path)
    return record


def _review_token_from_connection(connection: sqlite3.Connection) -> ReviewSnapshotToken:
    rows = connection.execute(
        "SELECT event_id, revision, state_json FROM events ORDER BY event_id"
    ).fetchall()
    maximum_audit = int(
        connection.execute("SELECT COALESCE(MAX(audit_id), 0) FROM audit_events").fetchone()[0]
    )
    states = [(str(event_id), int(revision), str(payload)) for event_id, revision, payload in rows]
    return ReviewSnapshotToken(
        event_count=len(states),
        revision_sum=sum(row[1] for row in states),
        maximum_audit_id=maximum_audit,
        state_sha256=content_sha256(states),
    )


def _review_token(path: Path) -> ReviewSnapshotToken:
    connection = sqlite3.connect(path, timeout=30.0)
    try:
        return _review_token_from_connection(connection)
    finally:
        connection.close()


def _detection_sha256(detection: DetectionResult) -> str:
    return content_sha256(
        {
            "generation_id": detection.generation_id,
            "parameter_hash": detection.parameter_hash,
            "parameters": detection.parameters,
            "columns": list(detection.events.columns),
            "events": detection.events.to_dict(orient="records"),
        }
    )


def _acquire_review_guard(
    path: Path,
    expected: ReviewSnapshotToken,
) -> sqlite3.Connection:
    """Hold a SQLite writer reservation from validation through publication."""

    connection = sqlite3.connect(path, timeout=30.0)
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("BEGIN IMMEDIATE")
        if _review_token_from_connection(connection) != expected:
            raise ProjectValidationError(
                "range-change preview is stale because review state changed"
            )
        return connection
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        connection.close()
        raise


def preview_range_change(
    project_dir: str | Path,
    start_min: str,
    end_min: str,
) -> RangeChangePreview:
    project = open_project(project_dir)
    requested = AnalysisRange.from_minutes(start_min, end_min)
    old_range = dict(project.manifest["analysis_range"])
    if requested.as_dict() == old_range:
        raise ValueError("requested analysis range is already active")
    scans = pd.read_parquet(_artifact_path(project, "scan_summary"))
    source_start = int(scans["scan_time_ns"].iloc[0])
    source_end = int(scans["scan_time_ns"].iloc[-1])
    if requested.start_ns < source_start or requested.end_ns > source_end:
        raise ValueError(
            "new analysis range must be contained in the parsed source time extent "
            f"[{source_start}, {source_end}] ns"
        )
    detection = detect_events(
        scans,
        source_sha256=str(project.manifest["source"]["source_sha256"]),
        analysis_range=requested,
    )
    review_path = resolve_project_path(project.project_dir, project.manifest["review"]["path"])
    store = ReviewStore.open(review_path, project_id=project.manifest["project_id"])
    try:
        all_states = store.list_events()
    finally:
        store.close()
    current_states = [row for row in all_states if row.get("generation_state") != "stale"]
    plan = propose_reconciliation(
        current_states,
        detection.events.to_dict(orient="records"),
    )
    token = _review_token(review_path)
    detection_sha = _detection_sha256(detection)
    manifest_sha = _sha256(project.project_dir / MANIFEST_NAME)
    preview_payload = {
        "project_id": project.manifest["project_id"],
        "old_generation_id": project.manifest["generation_id"],
        "new_generation_id": detection.generation_id,
        "old_analysis_range": old_range,
        "new_analysis_range": requested.as_dict(),
        "parameter_hash": detection.parameter_hash,
        "detection_sha256": detection_sha,
        "plan": plan,
        "review_snapshot": token,
        "manifest_sha256": manifest_sha,
    }
    return RangeChangePreview(
        project_dir=project.project_dir,
        old_generation_id=str(project.manifest["generation_id"]),
        new_generation_id=detection.generation_id,
        old_analysis_range=old_range,
        new_analysis_range=requested,
        detection=detection,
        detection_sha256=detection_sha,
        plan=plan,
        review_snapshot=token,
        manifest_sha256=manifest_sha,
        preview_id="RCP_" + content_sha256(preview_payload),
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(json_value(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _copy_database(source: Path, destination: Path) -> None:
    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


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
        raise ProjectValidationError(f"range reconciliation lost EventID {state['event_id']}")


def _insert_state(connection: sqlite3.Connection, state: dict[str, Any]) -> None:
    connection.execute(
        """INSERT INTO events(
               event_id, generation_id, auto_event_id, status, origin, revision, state_json
           ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            state["event_id"],
            state["generation_id"],
            state.get("auto_event_id"),
            state["status"],
            state["origin"],
            int(state["revision"]),
            canonical_json(state),
        ),
    )


def _insert_evidence(
    connection: sqlite3.Connection,
    row: dict[str, Any],
    generation_id: str,
) -> None:
    auto_id = str(row["auto_event_id"])
    payload = canonical_json(row)
    existing = connection.execute(
        "SELECT generation_id, evidence_json FROM automatic_evidence WHERE auto_event_id = ?",
        (auto_id,),
    ).fetchone()
    if existing is None:
        connection.execute(
            "INSERT INTO automatic_evidence(auto_event_id, generation_id, evidence_json) VALUES (?, ?, ?)",
            (auto_id, generation_id, payload),
        )
    elif str(existing[0]) != generation_id or str(existing[1]) != payload:
        raise ProjectValidationError("automatic evidence identity collision during range change")


def _migrate_review_database(
    source_db: Path,
    destination_db: Path,
    *,
    project_id: str,
    preview: RangeChangePreview,
    actor: str,
    session_id: str,
    reason: str,
) -> None:
    _copy_database(source_db, destination_db)
    connection = sqlite3.connect(destination_db)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        old_states = {
            str(row["event_id"]): json.loads(row["state_json"])
            for row in connection.execute("SELECT event_id, state_json FROM events")
        }
        new_rows = {
            str(row["auto_event_id"]): row
            for row in preview.detection.events.to_dict(orient="records")
        }
        for row in new_rows.values():
            _insert_evidence(connection, row, preview.new_generation_id)

        mapping_by_event = {row.event_id: row for row in preview.plan.mappings}
        touched_events: set[str] = set()
        for event_id, mapping in mapping_by_event.items():
            old = old_states[event_id]
            evidence = new_rows[mapping.new_auto_event_id]
            state = _automatic_state(project_id, preview.new_generation_id, evidence)
            state["event_id"] = event_id
            state["status"] = old["status"]
            state["revision"] = int(old["revision"]) + 1
            state["generation_state"] = "active"
            state["reconciled_from_generation_id"] = old.get("generation_id")
            state["reconciled_from_auto_event_id"] = mapping.old_auto_event_id
            state["reconciliation_method"] = mapping.method
            state["reconciliation_distance_sec"] = mapping.distance_sec
            current_ns = int(old["current_apex_time_ns"])
            within_new_support = int(evidence["left_time_ns"]) <= current_ns <= int(
                evidence["right_time_ns"]
            )
            if within_new_support and preview.new_analysis_range.contains_ns(current_ns):
                for field in (
                    "current_scan_id",
                    "current_scan_row_index",
                    "current_spectrum_index",
                    "current_apex_time_ns",
                    "current_apex_time_sec",
                    "current_apex_intensity",
                    "origin",
                    "snap_offset_sec",
                ):
                    state[field] = old[field]
            elif old.get("origin") == "manual_adjusted":
                state["status"] = "pending"
                state["reconciliation_warning"] = "old adjusted apex fell outside new immutable support"
            _store_state(connection, state)
            touched_events.add(event_id)

        for event_id in preview.plan.stale_event_ids:
            state = dict(old_states[event_id])
            state["revision"] = int(state["revision"]) + 1
            state["generation_state"] = "stale"
            state["stale_for_generation_id"] = preview.new_generation_id
            state["stale_reason"] = (
                "ambiguous_reconciliation"
                if event_id in preview.plan.ambiguous_event_ids
                else "no_active_automatic_match"
            )
            _store_state(connection, state)
            touched_events.add(event_id)

        for event_id in preview.plan.manual_event_ids:
            state = dict(old_states[event_id])
            state["revision"] = int(state["revision"]) + 1
            state["generation_id"] = preview.new_generation_id
            if preview.new_analysis_range.contains_ns(int(state["current_apex_time_ns"])):
                state["generation_state"] = "manual"
                state.pop("stale_for_generation_id", None)
                state.pop("stale_reason", None)
            else:
                state["generation_state"] = "stale"
                state["stale_for_generation_id"] = preview.new_generation_id
                state["stale_reason"] = "manual_apex_outside_new_range"
            _store_state(connection, state)
            touched_events.add(event_id)

        historical_by_auto = {
            str(state.get("auto_event_id")): state
            for state in old_states.values()
            if state.get("generation_state") == "stale" and state.get("auto_event_id")
        }
        for auto_id in preview.plan.unmatched_new_auto_event_ids:
            evidence = new_rows[auto_id]
            historical = historical_by_auto.get(auto_id)
            if historical is not None and historical["event_id"] not in touched_events:
                state = _automatic_state(project_id, preview.new_generation_id, evidence)
                state["event_id"] = historical["event_id"]
                state["status"] = historical["status"]
                state["revision"] = int(historical["revision"]) + 1
                state["generation_state"] = "active"
                state["reactivated_historical_auto_event"] = True
                _store_state(connection, state)
                touched_events.add(state["event_id"])
            else:
                state = _automatic_state(project_id, preview.new_generation_id, evidence)
                state["generation_state"] = "active"
                _insert_state(connection, state)

        connection.execute("DELETE FROM commands")
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = 'generation_id'",
            (preview.new_generation_id,),
        )
        details = {
            "preview_id": preview.preview_id,
            "old_generation_id": preview.old_generation_id,
            "new_generation_id": preview.new_generation_id,
            "old_analysis_range": preview.old_analysis_range,
            "new_analysis_range": preview.new_analysis_range.as_dict(),
            "mapped_count": preview.mapped_count,
            "stale_count": preview.stale_count,
            "ambiguous_count": len(preview.plan.ambiguous_event_ids),
            "new_count": preview.new_count,
            "manual_count": len(preview.plan.manual_event_ids),
            "confirmation": "explicit",
            "undo_stack_reset": True,
        }
        connection.execute(
            """INSERT INTO audit_events(
                   occurred_at, project_id, event_id, action, actor, session_id,
                   reason, before_json, after_json, details_json
               ) VALUES (?, ?, NULL, 'recalculate_analysis_range', ?, ?, ?, ?, ?, ?)""",
            (
                _now(),
                project_id,
                str(actor),
                str(session_id),
                str(reason or ""),
                canonical_json(preview.old_analysis_range),
                canonical_json(preview.new_analysis_range.as_dict()),
                canonical_json(details),
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _safe_remove_staging(path: Path, cache_root: Path) -> None:
    if not path.exists():
        return
    resolved = path.resolve()
    if resolved.parent != cache_root.resolve() or not resolved.name.startswith(
        ".range-change-building-"
    ):
        raise ProjectValidationError("refusing to clean an unrecognized range-change staging path")
    shutil.rmtree(resolved)


def _safe_remove_activation(path: Path, project_root: Path, activation_id: str) -> None:
    if not path.exists():
        return
    resolved = path.resolve()
    generations = (project_root / "generations").resolve()
    try:
        resolved.relative_to(generations)
    except ValueError as exc:
        raise ProjectValidationError("refusing to clean activation outside generations") from exc
    if resolved.name != activation_id or not activation_id.startswith("ACT_"):
        raise ProjectValidationError("refusing to clean an unrecognized generation activation")
    shutil.rmtree(resolved)


def apply_range_change(
    preview: RangeChangePreview,
    *,
    confirmed: bool,
    actor: str,
    session_id: str,
    reason: str = "",
) -> Project:
    if not confirmed:
        raise ProjectValidationError("range change requires explicit confirmation of the diff preview")
    if (
        preview.detection.generation_id != preview.new_generation_id
        or _detection_sha256(preview.detection) != preview.detection_sha256
    ):
        raise ProjectValidationError("range-change preview detection payload changed after preview")
    project = open_project(preview.project_dir)
    manifest_path = project.project_dir / MANIFEST_NAME
    if (
        str(project.manifest["generation_id"]) != preview.old_generation_id
        or _sha256(manifest_path) != preview.manifest_sha256
    ):
        raise ProjectValidationError("range-change preview is stale because the project manifest changed")
    old_review_path = resolve_project_path(project.project_dir, project.manifest["review"]["path"])

    activation_id = "ACT_" + uuid.uuid4().hex
    cache_root = project.project_dir / "cache"
    cache_root.mkdir(exist_ok=True)
    staging = cache_root / f".range-change-building-{activation_id}"
    final_relative_root = f"generations/{preview.new_generation_id}/{activation_id}"
    final_root = resolve_project_path(project.project_dir, final_relative_root)
    if final_root.exists():
        raise ProjectValidationError("unexpected range-generation activation collision")
    final_root.parent.mkdir(parents=True, exist_ok=True)
    switched = False
    old_manifest_bytes = manifest_path.read_bytes()
    review_guard = _acquire_review_guard(old_review_path, preview.review_snapshot)
    try:
        staging.mkdir(parents=False, exist_ok=False)
        automatic_path = staging / "automatic_events.parquet"
        protocol_path = staging / "detector_protocol.json"
        review_path = staging / "review.sqlite"
        retired_review_path = staging / "retired_review.sqlite"
        # Archive a transactionally guarded copy. A stale second application
        # may later write its old path, but it cannot mutate this bound history.
        _copy_database(old_review_path, retired_review_path)
        preview.detection.events.to_parquet(automatic_path, index=False)
        protocol = {
            "schema": "ms-event-detector-protocol-v1",
            "parser_version": PARSER_VERSION,
            "detector_version": DETECTOR_VERSION,
            "generation_id": preview.new_generation_id,
            "parameter_hash": preview.detection.parameter_hash,
            "analysis_range": preview.new_analysis_range.as_dict(),
            "parameters": preview.detection.parameters,
            "event_columns": list(preview.detection.events.columns),
            "scientific_rule": "full trace detection followed by closed current-apex ownership",
            "range_change_preview_id": preview.preview_id,
        }
        _write_json(protocol_path, protocol)
        _migrate_review_database(
            old_review_path,
            review_path,
            project_id=str(project.manifest["project_id"]),
            preview=preview,
            actor=actor,
            session_id=session_id,
            reason=reason,
        )
        os.replace(staging, final_root)

        auto_relative = f"{final_relative_root}/automatic_events.parquet"
        protocol_relative = f"{final_relative_root}/detector_protocol.json"
        review_relative = f"{final_relative_root}/review.sqlite"
        retired_review_relative = f"{final_relative_root}/retired_review.sqlite"
        old_by_role = {row["role"]: dict(row) for row in project.manifest["artifacts"]}
        old_review_history = _artifact_record(
            project.project_dir,
            retired_review_relative,
            "review_database",
            mutable=False,
        )
        history_entry = {
            "activation_id": str(project.manifest.get("activation_id", "initial")),
            "generation_id": preview.old_generation_id,
            "analysis_range": preview.old_analysis_range,
            "retired_at": _now(),
            "automatic_events": old_by_role["automatic_events"],
            "detector_protocol": old_by_role["detector_protocol"],
            "review_database": old_review_history,
        }
        artifacts = [
            row
            for row in project.manifest["artifacts"]
            if row["role"] not in {"automatic_events", "detector_protocol"}
        ]
        artifacts.extend(
            (
                _artifact_record(project.project_dir, auto_relative, "automatic_events", mutable=False),
                _artifact_record(project.project_dir, protocol_relative, "detector_protocol", mutable=False),
            )
        )
        review_record = _artifact_record(
            project.project_dir,
            review_relative,
            "review_database",
            mutable=True,
        )
        candidate = dict(project.manifest)
        candidate.update(
            {
                "application": {"name": "MS Event Studio", "version": __version__},
                "updated_at": _now(),
                "activation_id": activation_id,
                "generation_id": preview.new_generation_id,
                "analysis_range": preview.new_analysis_range.as_dict(),
                "artifacts": artifacts,
                "review": {
                    **review_record,
                    "schema_version": REVIEW_SCHEMA_VERSION,
                    "project_id": project.manifest["project_id"],
                    "generation_id": preview.new_generation_id,
                    "exports_path": project.manifest["review"]["exports_path"],
                },
                "generation_history": [
                    *list(project.manifest.get("generation_history", [])),
                    history_entry,
                ],
                "last_range_change": {
                    "preview_id": preview.preview_id,
                    "confirmed": True,
                    "reason": str(reason or ""),
                    "changed_at": _now(),
                },
            }
        )
        temporary_manifest = manifest_path.with_name(
            f".{manifest_path.name}.writing-{uuid.uuid4().hex}"
        )
        _write_json(temporary_manifest, candidate)
        os.replace(temporary_manifest, manifest_path)
        switched = True
        try:
            return open_project(project.project_dir)
        except Exception:
            rollback = manifest_path.with_name(f".{manifest_path.name}.rollback-{uuid.uuid4().hex}")
            rollback.write_bytes(old_manifest_bytes)
            os.replace(rollback, manifest_path)
            switched = False
            raise
    finally:
        try:
            if staging.exists():
                _safe_remove_staging(staging, cache_root)
            if not switched and final_root.exists():
                _safe_remove_activation(final_root, project.project_dir, activation_id)
        finally:
            if review_guard.in_transaction:
                review_guard.rollback()
            review_guard.close()
