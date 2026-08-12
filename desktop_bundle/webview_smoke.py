"""Launch and validate the packaged hidden-WebView smoke contract.

The actual WebView lifecycle belongs to ``ms_event_studio.web_desktop``.  This
module is deliberately renderer-agnostic build glue: it invokes the frozen
executable's hidden probe and refuses to accept an import-only or Tk result.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any, Mapping


REQUIRED_WEBVIEW_CHECKS = (
    "page_loaded",
    "frontend_ready",
    "api_health",
    "api_bootstrap",
)
EXPECTED_SCIENTIFIC_COUNTS = {
    "scan_rows": 1201,
    "event_rows": 3,
    "human_rows": 1,
    "machine_rows": 3,
}


def _check_value(payload: Mapping[str, Any], name: str) -> Any:
    checks = payload.get("checks")
    if isinstance(checks, Mapping) and name in checks:
        return checks[name]
    return payload.get(name)


def validate_webview_smoke_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a normalized payload or raise for an incomplete smoke report."""

    if payload.get("status") != "ok":
        raise ValueError("hidden WebView smoke status is not ok")
    if payload.get("renderer") != "pywebview":
        raise ValueError("packaged smoke did not prove the pywebview renderer")
    if payload.get("hidden") is not True:
        raise ValueError("packaged smoke did not use a hidden native window")
    if not isinstance(payload.get("application_version"), str):
        raise ValueError("packaged smoke omitted application_version")

    missing_checks = [
        name for name in REQUIRED_WEBVIEW_CHECKS if _check_value(payload, name) is not True
    ]
    if missing_checks:
        raise ValueError(
            "hidden WebView smoke omitted successful checks: " + ", ".join(missing_checks)
        )

    scientific = payload.get("scientific")
    if not isinstance(scientific, Mapping):
        raise ValueError("packaged smoke omitted the scientific round-trip payload")
    wrong_scientific = [
        name
        for name, expected in EXPECTED_SCIENTIFIC_COUNTS.items()
        if scientific.get(name) != expected
    ]
    if not isinstance(scientific.get("display_points"), int) or int(
        scientific["display_points"]
    ) <= 0:
        wrong_scientific.append("display_points")
    if wrong_scientific:
        raise ValueError(
            "packaged smoke returned unexpected scientific counts: "
            + ", ".join(wrong_scientific)
        )
    return dict(payload)


def run_packaged_webview_smoke(
    executable: Path,
    report_path: Path,
    *,
    cwd: Path,
    timeout_seconds: int = 90,
) -> tuple[subprocess.CompletedProcess[bytes], dict[str, Any]]:
    """Run the executable-only WebView/API/scientific probe."""

    report_path.unlink(missing_ok=True)
    completed = subprocess.run(
        [
            str(executable),
            "--webview-smoke",
            "--smoke-report",
            str(report_path),
        ],
        cwd=cwd,
        timeout=timeout_seconds,
        check=False,
    )
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "packaged hidden WebView smoke produced no valid report "
            f"(exit {completed.returncode})"
        ) from exc
    if completed.returncode != 0:
        raise RuntimeError(
            "packaged hidden WebView smoke failed with exit "
            f"{completed.returncode}: {payload}"
        )
    try:
        normalized = validate_webview_smoke_payload(payload)
    except ValueError as exc:
        raise RuntimeError(f"invalid packaged hidden WebView smoke report: {exc}") from exc
    return completed, normalized
