"""Command-line boundary for Phase 1 project creation, validation, and export."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

import pandas as pd

from .canonical import json_value
from .errors import MSEventStudioError
from .export import export_human_csv, export_machine_contract
from .parser import ParseProgress
from .paths import resolve_project_path
from .project import CreateProjectRequest, create_project, open_project
from .review import ReviewStore


def _print_json(payload: dict, *, stream=None) -> None:
    destination = sys.stdout if stream is None else stream
    print(json.dumps(json_value(payload), ensure_ascii=False, sort_keys=True), file=destination)


def _progress(progress: ParseProgress) -> None:
    _print_json(
        {
            "phase": progress.phase,
            "bytes_read": progress.bytes_read,
            "total_bytes": progress.total_bytes,
            "parsed_spectra": progress.parsed_spectra,
            "fraction": progress.fraction,
        },
        stream=sys.stderr,
    )


def _review_store(project):
    review_path = resolve_project_path(project.project_dir, project.manifest["review"]["path"])
    return ReviewStore.open(review_path, project_id=project.manifest["project_id"])


def _analysis_range(manifest: dict) -> tuple[int, int]:
    range_ = manifest["analysis_range"]
    return int(range_["start_ns"]), int(range_["end_ns"])


def _command_create(args: argparse.Namespace) -> dict:
    project = create_project(
        CreateProjectRequest(
            source_path=args.source,
            project_dir=args.project,
            display_name=args.name,
            analysis_start_min=args.start_min,
            analysis_end_min=args.end_min,
            progress_callback=_progress,
        )
    )
    return {
        "command": "create",
        "project_dir": str(project.project_dir),
        "project_id": project.manifest["project_id"],
        "generation_id": project.manifest["generation_id"],
    }


def _command_verify(args: argparse.Namespace) -> dict:
    project = open_project(args.project)
    store = _review_store(project)
    try:
        event_count = len(store.list_events())
        audit_count = len(store.audit_events())
    finally:
        store.close()
    return {
        "command": "verify",
        "project_dir": str(project.project_dir),
        "project_id": project.manifest["project_id"],
        "generation_id": project.manifest["generation_id"],
        "event_count": event_count,
        "audit_count": audit_count,
        "status": "valid",
    }


def _command_export(args: argparse.Namespace) -> dict:
    project = open_project(args.project)
    start_ns, end_ns = _analysis_range(project.manifest)
    store = _review_store(project)
    try:
        result = export_human_csv(
            store.list_events(),
            args.output,
            analysis_start_ns=start_ns,
            analysis_end_ns=end_ns,
            include_pending=bool(args.include_pending),
        )
        store.record_export(
            actor=args.actor,
            session_id=args.session or ("cli-" + uuid.uuid4().hex),
            reason=args.reason,
            details={
                "contract": "human-csv-v1",
                "file_name": result.path.name,
                "sha256": result.sha256,
                "size_bytes": result.size_bytes,
                "row_count": result.row_count,
                "statuses": result.statuses,
                "analysis_start_ns": start_ns,
                "analysis_end_ns": end_ns,
            },
        )
    finally:
        store.close()
    return {
        "command": "export",
        "path": str(result.path),
        "sha256": result.sha256,
        "row_count": result.row_count,
        "statuses": result.statuses,
    }


def _artifact_path(project, role: str) -> Path:
    for artifact in project.manifest["artifacts"]:
        if artifact["role"] == role:
            return resolve_project_path(project.project_dir, artifact["path"])
    raise ValueError(f"project manifest has no {role!r} artifact")


def _command_export_machine(args: argparse.Namespace) -> dict:
    project = open_project(args.project)
    start_ns, end_ns = _analysis_range(project.manifest)
    input_manifest = json.loads(_artifact_path(project, "input_manifest").read_text(encoding="utf-8"))
    detector_protocol = json.loads(
        _artifact_path(project, "detector_protocol").read_text(encoding="utf-8")
    )
    automatic = pd.read_parquet(_artifact_path(project, "automatic_events"))
    store = _review_store(project)
    try:
        result = export_machine_contract(
            store.list_events(),
            automatic,
            args.output_dir,
            source_fingerprint=input_manifest["source_fingerprint"],
            detector_version=detector_protocol["detector_version"],
            parameter_hash=detector_protocol["parameter_hash"],
            generation_id=detector_protocol["generation_id"],
            analysis_start_ns=start_ns,
            analysis_end_ns=end_ns,
            boundary_rule=project.manifest["analysis_range"]["boundary_rule"],
        )
        store.record_export(
            actor=args.actor,
            session_id=args.session or ("cli-" + uuid.uuid4().hex),
            reason=args.reason,
            details={
                "contract": "ms-event-machine-contract-v1",
                "directory_name": result.output_dir.name,
                "event_table_sha256": result.event_table_sha256,
                "manifest_sha256": result.manifest_sha256,
                "row_count": result.row_count,
            },
        )
    finally:
        store.close()
    return {
        "command": "export-machine",
        "output_dir": str(result.output_dir),
        "event_table_sha256": result.event_table_sha256,
        "manifest_sha256": result.manifest_sha256,
        "row_count": result.row_count,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ms-event-studio")
    subcommands = parser.add_subparsers(dest="command", required=True)

    create = subcommands.add_parser("create", help="atomically create a project")
    create.add_argument("--source", required=True)
    create.add_argument("--project", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--start-min", required=True)
    create.add_argument("--end-min", required=True)
    create.set_defaults(handler=_command_create)

    verify = subcommands.add_parser("verify", help="validate project bindings and hashes")
    verify.add_argument("--project", required=True)
    verify.set_defaults(handler=_command_verify)

    human = subcommands.add_parser("export", help="export the six-column human CSV")
    human.add_argument("--project", required=True)
    human.add_argument("--output", required=True)
    human.add_argument("--include-pending", action="store_true")
    human.add_argument("--actor", default="cli")
    human.add_argument("--session")
    human.add_argument("--reason", default="")
    human.set_defaults(handler=_command_export)

    machine = subcommands.add_parser(
        "export-machine", help="export the all-status versioned machine contract"
    )
    machine.add_argument("--project", required=True)
    machine.add_argument("--output-dir", required=True)
    machine.add_argument("--actor", default="cli")
    machine.add_argument("--session")
    machine.add_argument("--reason", default="")
    machine.set_defaults(handler=_command_export_machine)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.handler(args)
    except KeyboardInterrupt:
        _print_json({"status": "cancelled"}, stream=sys.stderr)
        return 130
    except (MSEventStudioError, ValueError, FileExistsError, OSError) as exc:
        _print_json(
            {"status": "error", "error_type": type(exc).__name__, "message": str(exc)},
            stream=sys.stderr,
        )
        return 2
    _print_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
