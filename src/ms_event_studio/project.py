"""Atomic, portable MS Event Studio project creation and validation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from . import __version__
from .canonical import json_value
from .detector import DETECTOR_VERSION, detect_events
from .errors import CancelledError, ProjectValidationError
from .parser import (
    PARSER_VERSION,
    ParseProgress,
    ParseResult,
    parse_ms_scan_summary,
    verify_source_fingerprint,
)
from .paths import resolve_project_path
from .review import REVIEW_SCHEMA_VERSION, ReviewStore
from .timebase import AnalysisRange


PROJECT_SCHEMA = "ms-event-project-v1"
MANIFEST_NAME = "ms_event_project.json"
REQUIRED_ARTIFACT_ROLES = frozenset(
    {
        "project_readme",
        "scan_summary",
        "automatic_events",
        "input_manifest",
        "detector_protocol",
        "processing_log",
    }
)


@dataclass(slots=True)
class CreateProjectRequest:
    source_path: str | Path
    project_dir: str | Path
    display_name: str
    analysis_start_min: str
    analysis_end_min: str
    cancel_check: Callable[[], bool] | None = field(default=None, repr=False)
    progress_callback: Callable[[ParseProgress], None] | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class Project:
    project_dir: Path
    manifest: dict


@dataclass(frozen=True, slots=True)
class PreparedProjectSource:
    """One completed source parse retained for the desktop create workflow."""

    source_path: Path
    parsed: ParseResult
    start_ns: int
    end_ns: int


def inspect_project_source(
    source_path: str | Path,
    *,
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[ParseProgress], None] | None = None,
) -> PreparedProjectSource:
    source = Path(source_path).resolve()
    parsed = parse_ms_scan_summary(
        source,
        cancel_check=cancel_check,
        progress_callback=progress_callback,
    )
    return PreparedProjectSource(
        source_path=source,
        parsed=parsed,
        start_ns=int(parsed.scans["scan_time_ns"].iloc[0]),
        end_ns=int(parsed.scans["scan_time_ns"].iloc[-1]),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(json_value(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _manifest_int(value: object, field_name: str, *, minimum: int | None = None) -> int:
    """Read an exact JSON integer without accepting booleans or coercing text."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ProjectValidationError(f"{field_name} must be an integer")
    if minimum is not None and value < minimum:
        raise ProjectValidationError(f"{field_name} must be at least {minimum}")
    return value


def _artifact(root: Path, relative: str, role: str, *, mutable: bool = False) -> dict:
    path = resolve_project_path(root, relative)
    if not path.is_file():
        raise ProjectValidationError(f"required artifact is missing: {relative}")
    record = {
        "role": role,
        "path": relative,
        "mutable": bool(mutable),
        "size_bytes_at_creation": path.stat().st_size,
    }
    if not mutable:
        record["sha256"] = _sha256(path)
    return record


def _safe_cleanup_staging(staging: Path, parent: Path, target_name: str) -> None:
    if not staging.exists():
        return
    resolved = staging.resolve()
    expected_parent = parent.resolve()
    if resolved.parent != expected_parent:
        raise RuntimeError("refusing to clean staging directory outside target parent")
    if not resolved.name.startswith(f".{target_name}.ms-event-building-"):
        raise RuntimeError("refusing to clean an unrecognized staging directory")
    shutil.rmtree(resolved)


def _validate_target(target: Path) -> bool:
    """Return whether an existing empty directory must be restored on failure."""

    if not target.exists():
        return False
    if not target.is_dir():
        raise FileExistsError(f"project target exists and is not a directory: {target}")
    if any(target.iterdir()):
        raise FileExistsError(f"project target directory is not empty: {target}")
    return True


def create_project(
    request: CreateProjectRequest,
    *,
    prepared_source: PreparedProjectSource | None = None,
) -> Project:
    source = Path(request.source_path).resolve()
    target = Path(request.project_dir).resolve()
    display_name = str(request.display_name).strip()
    if not display_name:
        raise ValueError("project display_name cannot be empty")
    analysis_range = AnalysisRange.from_minutes(
        request.analysis_start_min,
        request.analysis_end_min,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target_was_empty = _validate_target(target)
    staging = target.parent / f".{target.name}.ms-event-building-{uuid.uuid4().hex}"
    if staging.exists():
        raise FileExistsError(f"unexpected staging collision: {staging}")

    published = False
    try:
        for relative in (
            "data",
            "annotations/exports",
            "provenance",
            "cache",
            "diagnostics",
        ):
            (staging / relative).mkdir(parents=True, exist_ok=False)

        if prepared_source is None:
            parsed = parse_ms_scan_summary(
                source,
                cancel_check=request.cancel_check,
                progress_callback=request.progress_callback,
            )
        else:
            if prepared_source.source_path.resolve() != source:
                raise ValueError("prepared source belongs to a different MS file")
            if prepared_source.parsed.summary.parser_version != PARSER_VERSION:
                raise ValueError("prepared source parser version is no longer supported")
            verify_source_fingerprint(source, prepared_source.parsed.fingerprint)
            parsed = prepared_source.parsed
            if request.progress_callback is not None:
                request.progress_callback(
                    ParseProgress(
                        "prepared",
                        parsed.fingerprint.size_bytes,
                        parsed.fingerprint.size_bytes,
                        len(parsed.scans),
                    )
                )
        if request.cancel_check is not None and request.cancel_check():
            raise CancelledError("project creation cancelled")
        scan_start_ns = int(parsed.scans["scan_time_ns"].iloc[0])
        scan_end_ns = int(parsed.scans["scan_time_ns"].iloc[-1])
        if analysis_range.start_ns < scan_start_ns or analysis_range.end_ns > scan_end_ns:
            raise ValueError(
                "analysis range must be contained in the parsed source time extent "
                f"[{scan_start_ns}, {scan_end_ns}] ns"
            )
        detected = detect_events(
            parsed.scans,
            source_sha256=parsed.fingerprint.sha256,
            analysis_range=analysis_range,
        )
        if request.cancel_check is not None and request.cancel_check():
            raise CancelledError("project creation cancelled")

        scan_path = staging / "data/ms_scan_summary.parquet"
        event_path = staging / "data/automatic_events.parquet"
        parsed.scans.to_parquet(scan_path, index=False)
        detected.events.to_parquet(event_path, index=False)
        if request.cancel_check is not None and request.cancel_check():
            raise CancelledError("project creation cancelled")

        project_id = "PRJ_" + uuid.uuid4().hex
        created_at = _now()
        input_manifest = {
            "schema": "ms-event-input-manifest-v1",
            "input_mode": "external_reference",
            "source_file_name": source.name,
            "source_fingerprint": asdict(parsed.fingerprint),
            "parse_summary": asdict(parsed.summary),
            "path_policy": "absolute source path is intentionally not serialized; reselect source to recalculate",
        }
        _write_json(staging / "provenance/input_manifest.json", input_manifest)

        detector_protocol = {
            "schema": "ms-event-detector-protocol-v1",
            "parser_version": PARSER_VERSION,
            "detector_version": DETECTOR_VERSION,
            "generation_id": detected.generation_id,
            "parameter_hash": detected.parameter_hash,
            "analysis_range": analysis_range.as_dict(),
            "parameters": detected.parameters,
            "event_columns": list(detected.events.columns),
            "scientific_rule": "full trace detection followed by closed current-apex ownership",
        }
        _write_json(staging / "provenance/detector_protocol.json", detector_protocol)

        processing_lines = (
            f"{created_at}\tproject_create_started\n"
            f"{created_at}\tinput_name={source.name}\n"
            f"{created_at}\tinput_sha256={parsed.fingerprint.sha256}\n"
            f"{created_at}\tparsed_spectra={len(parsed.scans)}\n"
            f"{created_at}\tautomatic_events_in_range={len(detected.events)}\n"
            f"{created_at}\tgeneration_id={detected.generation_id}\n"
            f"{created_at}\tproject_staged_complete\n"
        )
        (staging / "provenance/processing.log").write_text(
            processing_lines,
            encoding="utf-8",
            newline="\n",
        )
        (staging / "README.md").write_text(
            "# MS Event Studio project\n\n"
            f"Display name: {display_name}\n\n"
            "Open this directory as a unit. Do not move individual data, provenance, "
            "or annotation files; their paths and immutable digests are manifest-bound.\n"
            "The original MS source is externally referenced and is never modified or copied.\n",
            encoding="utf-8",
            newline="\n",
        )

        review_path = staging / "annotations/review.sqlite"
        store = ReviewStore.create(
            review_path,
            project_id=project_id,
            generation_id=detected.generation_id,
            automatic_events=detected.events.to_dict(orient="records"),
        )
        store.close()

        immutable_artifacts = [
            _artifact(staging, "README.md", "project_readme"),
            _artifact(staging, "data/ms_scan_summary.parquet", "scan_summary"),
            _artifact(staging, "data/automatic_events.parquet", "automatic_events"),
            _artifact(staging, "provenance/input_manifest.json", "input_manifest"),
            _artifact(staging, "provenance/detector_protocol.json", "detector_protocol"),
            _artifact(staging, "provenance/processing.log", "processing_log"),
        ]
        review_record = _artifact(
            staging,
            "annotations/review.sqlite",
            "review_database",
            mutable=True,
        )
        manifest = {
            "schema": PROJECT_SCHEMA,
            "project_id": project_id,
            "display_name": display_name,
            "created_at": created_at,
            "application": {"name": "MS Event Studio", "version": __version__},
            "generation_id": detected.generation_id,
            "analysis_range": analysis_range.as_dict(),
            "source": {
                "mode": "external_reference",
                "input_manifest_path": "provenance/input_manifest.json",
                "source_sha256": parsed.fingerprint.sha256,
            },
            "artifacts": immutable_artifacts,
            "review": {
                **review_record,
                "schema_version": REVIEW_SCHEMA_VERSION,
                "project_id": project_id,
                "generation_id": detected.generation_id,
                "exports_path": "annotations/exports",
            },
        }
        _write_json(staging / MANIFEST_NAME, manifest)
        if request.cancel_check is not None and request.cancel_check():
            raise CancelledError("project creation cancelled")
        verify_source_fingerprint(source, parsed.fingerprint)
        _open_project(staging)

        if target_was_empty:
            target.rmdir()
        try:
            os.replace(staging, target)
        except Exception:
            if target_was_empty and not target.exists():
                target.mkdir()
            raise
        published = True
        return _open_project(target)
    finally:
        if not published and staging.exists():
            _safe_cleanup_staging(staging, target.parent, target.name)


def _open_project(project_dir: Path) -> Project:
    root = project_dir.resolve()
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ProjectValidationError(f"project manifest is missing: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectValidationError(f"invalid project manifest: {exc}") from exc
    if manifest.get("schema") != PROJECT_SCHEMA:
        raise ProjectValidationError("unsupported project manifest schema")
    project_id = manifest.get("project_id")
    generation = manifest.get("generation_id")
    if not isinstance(project_id, str) or not project_id or not isinstance(generation, str) or not generation:
        raise ProjectValidationError("project identity or generation binding is missing")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ProjectValidationError("project artifact registry is missing")
    roles = [record.get("role") for record in artifacts if isinstance(record, dict)]
    paths = [record.get("path") for record in artifacts if isinstance(record, dict)]
    if len(roles) != len(artifacts) or any(not isinstance(role, str) for role in roles):
        raise ProjectValidationError("artifact role is missing or invalid")
    if len(set(roles)) != len(roles):
        raise ProjectValidationError("artifact roles must be unique")
    missing_roles = sorted(REQUIRED_ARTIFACT_ROLES.difference(roles))
    if missing_roles:
        raise ProjectValidationError(
            f"required artifact role is missing: {', '.join(missing_roles)}"
        )
    if any(not isinstance(path, str) or not path for path in paths):
        raise ProjectValidationError("artifact path is missing or invalid")
    if len(set(paths)) != len(paths):
        raise ProjectValidationError("artifact paths must be unique")
    if len({str(path).replace("\\", "/").casefold() for path in paths}) != len(paths):
        raise ProjectValidationError("artifact paths must be portable-case unique")

    generation_history = manifest.get("generation_history", [])
    if not isinstance(generation_history, list):
        raise ProjectValidationError("generation history must be a list")
    historical_paths: list[str] = []
    for index, entry in enumerate(generation_history):
        if not isinstance(entry, dict) or not isinstance(entry.get("generation_id"), str):
            raise ProjectValidationError(f"invalid generation history entry {index}")
        for role in ("automatic_events", "detector_protocol", "review_database"):
            record = entry.get(role)
            if not isinstance(record, dict) or record.get("role") != role:
                raise ProjectValidationError(f"generation history {index} is missing {role}")
            relative = record.get("path")
            if not isinstance(relative, str) or not relative:
                raise ProjectValidationError(f"generation history {index} has an invalid path")
            historical_paths.append(relative)
            historical_path = resolve_project_path(root, relative)
            if not historical_path.is_file() or record.get("mutable") is not False:
                raise ProjectValidationError(f"generation history artifact is invalid: {relative}")
            expected_size = _manifest_int(
                record.get("size_bytes_at_creation"),
                "generation history size_bytes_at_creation",
                minimum=0,
            )
            if historical_path.stat().st_size != expected_size:
                raise ProjectValidationError(f"generation history artifact size mismatch: {relative}")
            if _sha256(historical_path) != record.get("sha256"):
                raise ProjectValidationError(f"generation history artifact SHA-256 mismatch: {relative}")
    all_bound_paths = [*paths, *historical_paths]
    if len({str(path).replace("\\", "/").casefold() for path in all_bound_paths}) != len(
        all_bound_paths
    ):
        raise ProjectValidationError("active and historical artifact paths must be unique")

    for record in artifacts:
        relative = record.get("path")
        if not isinstance(relative, str):
            raise ProjectValidationError("artifact path is missing")
        path = resolve_project_path(root, relative)
        if not path.is_file():
            raise ProjectValidationError(f"project artifact is missing: {relative}")
        if record.get("mutable") is not False:
            raise ProjectValidationError(
                f"required scientific artifact cannot be mutable: {relative}"
            )
        expected_size = _manifest_int(
            record.get("size_bytes_at_creation"),
            "artifact size_bytes_at_creation",
            minimum=0,
        )
        if path.stat().st_size != expected_size:
            raise ProjectValidationError(f"project artifact size mismatch: {relative}")
        if _sha256(path) != record.get("sha256"):
            raise ProjectValidationError(f"project artifact SHA-256 mismatch: {relative}")

    by_role = {str(record["role"]): record for record in artifacts}
    source_binding = manifest.get("source")
    if not isinstance(source_binding, dict):
        raise ProjectValidationError("manifest source binding is missing")
    input_record = by_role["input_manifest"]
    if source_binding.get("input_manifest_path") != input_record["path"]:
        raise ProjectValidationError("source input-manifest path binding mismatch")
    input_path = resolve_project_path(root, str(input_record["path"]))
    detector_path = resolve_project_path(
        root, str(by_role["detector_protocol"]["path"])
    )
    try:
        input_manifest = json.loads(input_path.read_text(encoding="utf-8"))
        detector_protocol = json.loads(detector_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectValidationError(f"invalid verified provenance JSON: {exc}") from exc
    if not isinstance(input_manifest, dict) or not isinstance(detector_protocol, dict):
        raise ProjectValidationError("verified provenance JSON roots must be objects")
    input_fingerprint = input_manifest.get("source_fingerprint")
    if not isinstance(input_fingerprint, dict) or (
        source_binding.get("source_sha256") != input_fingerprint.get("sha256")
    ):
        raise ProjectValidationError("source fingerprint binding mismatch")
    if detector_protocol.get("generation_id") != generation:
        raise ProjectValidationError("detector protocol generation binding mismatch")
    if detector_protocol.get("analysis_range") != manifest.get("analysis_range"):
        raise ProjectValidationError("detector protocol analysis-range binding mismatch")
    if source_binding.get("mode") != "external_reference":
        raise ProjectValidationError("unsupported source binding mode")

    scan_summary = input_manifest.get("parse_summary")
    if not isinstance(scan_summary, dict):
        raise ProjectValidationError("input parse-summary binding is missing")
    scan_path = resolve_project_path(root, str(by_role["scan_summary"]["path"]))
    event_path = resolve_project_path(root, str(by_role["automatic_events"]["path"]))
    try:
        scan_frame = pd.read_parquet(
            scan_path,
            columns=["scan_row_index", "spectrum_index", "scan_id", "scan_time_ns"],
        )
        event_frame = pd.read_parquet(
            event_path,
            columns=[
                "auto_event_id",
                "generation_id",
                "source_sha256",
                "detector_version",
                "parameter_hash",
                "scan_time_ns",
            ],
        )
    except Exception as exc:
        raise ProjectValidationError(f"failed to validate scientific Parquet schema: {exc}") from exc
    expected_scan_count = _manifest_int(
        scan_summary.get("parsed_spectrum_count"),
        "input parse_summary parsed_spectrum_count",
        minimum=1,
    )
    if len(scan_frame) != expected_scan_count:
        raise ProjectValidationError("scan-summary row-count binding mismatch")
    if scan_frame["scan_id"].astype(str).duplicated().any():
        raise ProjectValidationError("scan-summary contains duplicate scan_id")
    if scan_frame["spectrum_index"].duplicated().any():
        raise ProjectValidationError("scan-summary contains duplicate spectrum_index")
    if not scan_frame["scan_time_ns"].astype("int64").is_monotonic_increasing or (
        scan_frame["scan_time_ns"].astype("int64").diff().dropna() <= 0
    ).any():
        raise ProjectValidationError("scan-summary time is not strictly increasing")
    if event_frame["auto_event_id"].astype(str).duplicated().any():
        raise ProjectValidationError("automatic event table contains duplicate auto_event_id")
    expected_event_bindings = {
        "generation_id": generation,
        "source_sha256": source_binding.get("source_sha256"),
        "detector_version": detector_protocol.get("detector_version"),
        "parameter_hash": detector_protocol.get("parameter_hash"),
    }
    for column, expected in expected_event_bindings.items():
        values = set(event_frame[column].dropna().astype(str))
        if values and values != {str(expected)}:
            raise ProjectValidationError(f"automatic event {column} binding mismatch")
    analysis = manifest.get("analysis_range")
    if not isinstance(analysis, dict):
        raise ProjectValidationError("analysis-range binding is missing")
    start_ns = _manifest_int(analysis.get("start_ns"), "analysis-range start_ns", minimum=0)
    end_ns = _manifest_int(analysis.get("end_ns"), "analysis-range end_ns", minimum=0)
    if end_ns < start_ns or analysis.get("boundary_rule") != "closed_current_apex_v1":
        raise ProjectValidationError("invalid closed analysis-range binding")
    if not event_frame.empty and not event_frame["scan_time_ns"].astype("int64").between(
        start_ns, end_ns, inclusive="both"
    ).all():
        raise ProjectValidationError("automatic event lies outside bound analysis range")

    review = manifest.get("review")
    if not isinstance(review, dict):
        raise ProjectValidationError("manifest review binding is missing")
    if review.get("project_id") != project_id or review.get("generation_id") != generation:
        raise ProjectValidationError("manifest review binding mismatch")
    if review.get("schema_version") != REVIEW_SCHEMA_VERSION or review.get("mutable") is not True:
        raise ProjectValidationError("manifest review schema/mutability binding mismatch")
    review_path = resolve_project_path(root, str(review.get("path", "")))
    store = ReviewStore.open(review_path, project_id=project_id)
    try:
        if store.generation_id != generation:
            raise ProjectValidationError("review database generation binding mismatch")
    finally:
        store.close()
    exports = resolve_project_path(root, str(review.get("exports_path", "")))
    if not exports.is_dir():
        raise ProjectValidationError("review exports directory is missing")
    return Project(project_dir=root, manifest=manifest)


def open_project(project_dir: str | Path) -> Project:
    return _open_project(Path(project_dir))
