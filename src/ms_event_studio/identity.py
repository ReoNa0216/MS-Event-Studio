"""Stable project and generation identities."""

from __future__ import annotations

import uuid

from .canonical import content_sha256
from .timebase import AnalysisRange


def generation_id(
    *,
    source_sha256: str,
    parser_version: str,
    detector_version: str,
    parameter_hash: str,
    analysis_range: AnalysisRange,
    boundary_rule: str,
) -> str:
    payload = {
        "source_sha256": source_sha256,
        "parser_version": parser_version,
        "detector_version": detector_version,
        "parameter_hash": parameter_hash,
        "analysis_start_ns": analysis_range.start_ns,
        "analysis_end_ns": analysis_range.end_ns,
        "boundary_rule": boundary_rule,
    }
    return "GEN_" + content_sha256(payload)


def auto_event_id(
    *,
    generation_id: str,
    scan_id: str,
    spectrum_index: int,
    scan_row_index: int,
    scan_time_ns: int,
) -> str:
    payload = {
        "generation_id": generation_id,
        "scan_id": str(scan_id),
        "spectrum_index": int(spectrum_index),
        "scan_row_index": int(scan_row_index),
        "scan_time_ns": int(scan_time_ns),
    }
    return "AE_" + content_sha256(payload)


def new_event_id() -> str:
    return "EV_" + uuid.uuid4().hex
