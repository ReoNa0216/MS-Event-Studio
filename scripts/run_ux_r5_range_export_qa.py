"""Standalone Playwright acceptance gate for UX-R5 range change and export.

Fixture checks are deterministic and must issue no requests.  The real half
uses a disposable scientific project and a loopback native-dialog provider;
JavaScript receives only an opaque selection capability and display name.
Browser viewport evidence is CSS-pixel evidence, never native DPI evidence.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from typing import Any, Callable
from urllib.parse import urlparse
from unittest.mock import patch


REPOSITORY = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

try:
    from lint_ui_copy import FORBIDDEN_UI_TERMS
    from run_ux_r2_r3_workbench_qa import (
        _assert,
        _assert_no_browser_persistence,
        _event_rows,
        _file_fingerprint,
        _goto_fixture,
        _hook_state,
        _qa,
        _storage_evidence,
        _wait_for_eval,
        _workbench_state,
    )
except ModuleNotFoundError:  # Imported as scripts.run_ux_r5_range_export_qa.
    from scripts.lint_ui_copy import FORBIDDEN_UI_TERMS
    from scripts.run_ux_r2_r3_workbench_qa import (
        _assert,
        _assert_no_browser_persistence,
        _event_rows,
        _file_fingerprint,
        _goto_fixture,
        _hook_state,
        _qa,
        _storage_evidence,
        _wait_for_eval,
        _workbench_state,
    )


R5_FIXTURES = (
    "range-input",
    "range-calculating",
    "range-preview",
    "range-applying",
    "range-error",
    "export-review-results",
    "export-audit-package",
    "exporting",
    "export-error",
)
R5_STANDARD_VIEWPORTS = (
    {"width": 960, "height": 640},
    {"width": 1366, "height": 768},
    {"width": 1920, "height": 1080},
)

RANGE_ENDPOINTS = {
    "preview": "/api/range-changes/preview",
    "apply": "/api/range-changes/apply",
    "cancel": "/api/range-changes/cancel",
}
EXPORT_ENDPOINTS = {
    "review_results": "/api/exports/review-results",
    "audit_package": "/api/exports/audit-package",
}
JOB_PREFIX = "/api/jobs/"
SELECT_PATH_ENDPOINT = "/api/select-path"
HUMAN_COLUMNS = (
    "EventID",
    "scan_id",
    "scan_start_time",
    "apex_intensity",
    "review_status",
    "source",
)
AUDIT_FILES = ("checksums.sha256", "events.parquet", "manifest.json")
FORBIDDEN_DOM_DATA = (
    "action-token",
    "event-id",
    "revision",
    "scan-row-index",
    "spectrum-index",
    "generation-id",
    "project-id",
    "schema",
    "sqlite",
    "manifest",
    "source-path",
    "preview-token",
    "job-id",
    "target-token",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _scientific_snapshot(project_dir: Path) -> dict[str, Any]:
    from ms_event_studio.project import open_project
    from ms_event_studio.window_service import ProjectWindowService

    project = open_project(project_dir)
    analysis = project.manifest["analysis_range"]
    with ProjectWindowService.open(project_dir) as service:
        events = service.all_events()
        audits = service.review_store.audit_events()
        return {
            "range_min": {
                "start": str(int(analysis["start_ns"]) / 60_000_000_000).rstrip("0").rstrip("."),
                "end": str(int(analysis["end_ns"]) / 60_000_000_000).rstrip("0").rstrip("."),
            },
            "event_count": len(events),
            "active_events": [
                {
                    "time_sec": round(float(row["current_apex_time_sec"]), 9),
                    "status": str(row["status"]),
                    "origin": str(row["origin"]),
                }
                for row in events
                if row.get("generation_state") != "stale"
            ],
            "audit_count": len(audits),
            "audit_actions": [str(row["action"]) for row in audits],
        }


def _prepare_project(root: Path) -> tuple[Path, Any]:
    from ms_event_studio.demo import create_guided_source
    from ms_event_studio.project import CreateProjectRequest, create_project
    from ms_event_studio.window_service import ProjectWindowService

    source = root / "r5-range-export-source.txt"
    create_guided_source(source)
    project = create_project(
        CreateProjectRequest(
            source_path=source,
            project_dir=root / "r5-range-export-project",
            display_name="范围与导出门禁项目",
            analysis_start_min="0",
            analysis_end_min="2",
        )
    )
    with ProjectWindowService.open(project.project_dir) as service:
        events = service.all_events()
        _assert(len(events) == 3, f"R5 fixture detected {len(events)} events, expected 3")
        accepted = service.review_store.set_status(
            events[1]["event_id"],
            "accepted",
            expected_revision=int(events[1]["revision"]),
            actor="qa-fixture",
            session_id="qa-fixture",
            reason="accepted export row",
        )
        service.review_store.set_status(
            events[2]["event_id"],
            "pending",
            expected_revision=int(events[2]["revision"]),
            actor="qa-fixture",
            session_id="qa-fixture",
            reason="pending export row",
        )
        _assert(accepted["status"] == "accepted", "R5 fixture did not prepare accepted row")
    return source, project


def _body_json(request: Any) -> Any:
    raw = request.post_data
    if raw in {None, ""}:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return "<non-json>"


def _assert_safe_dom(page: Any) -> dict[str, Any]:
    audit = page.evaluate(
        """arg => {
          const root = document.querySelector('[data-qa="workbench"]') || document.body;
          const visibleText = root.innerText || "";
          const forbiddenCopy = arg.terms.filter(term => visibleText.toLowerCase().includes(term.toLowerCase()));
          const forbiddenData = [];
          for (const node of root.querySelectorAll("*")) {
            for (const name of node.getAttributeNames()) {
              if (name.startsWith("data-") && arg.dataNames.includes(name.slice(5).toLowerCase())) {
                forbiddenData.push(name);
              }
            }
          }
          return {
            forbiddenCopy: Array.from(new Set(forbiddenCopy)),
            forbiddenData: Array.from(new Set(forbiddenData)),
          };
        }""",
        {"terms": sorted(FORBIDDEN_UI_TERMS), "dataNames": list(FORBIDDEN_DOM_DATA)},
    )
    _assert(audit["forbiddenCopy"] == [], f"R5 DOM exposes forbidden copy: {audit}")
    _assert(audit["forbiddenData"] == [], f"R5 DOM exposes internal data fields: {audit}")
    return {"ok": True, **audit}


def _fixture_expected(fixture: str) -> tuple[str, str | None]:
    if fixture.startswith("range-"):
        return fixture.removeprefix("range-"), None
    if fixture == "export-review-results":
        return "input", "review_results"
    if fixture == "export-audit-package":
        return "input", "audit_package"
    if fixture == "exporting":
        return "exporting", "review_results"
    if fixture == "export-error":
        return "error", "audit_package"
    raise AssertionError(f"unknown R5 fixture {fixture}")


def _assert_fixture_surface(page: Any, fixture: str) -> dict[str, Any]:
    state = _hook_state(page)
    workbench = state.get("workbench") or {}
    expected_state, expected_kind = _fixture_expected(fixture)
    if fixture.startswith("range-"):
        actual = workbench.get("rangeState")
        _assert(actual == expected_state, f"{fixture} rangeState={actual!r}")
        dialog = page.locator(_qa("range-dialog"))
        _assert(dialog.count() == 1 and dialog.is_visible(), f"{fixture} range dialog is absent")
        _assert(dialog.get_attribute("role") in {None, "dialog"}, f"{fixture} has invalid role")
        busy = expected_state in {"calculating", "applying"}
        _assert((dialog.get_attribute("aria-busy") == "true") == busy,
                f"{fixture} aria-busy mismatch")
        if expected_state in {"preview", "applying"}:
            _assert(workbench.get("rangePreviewPresent") is True,
                    f"{fixture} lacks range preview capability state")
            for name in (
                "range-old", "range-new", "impact-reusable", "impact-moved-out",
                "impact-reconfirm", "impact-newly-detected", "impact-retained-manual",
            ):
                target = page.locator(_qa(name))
                _assert(target.count() == 1 and target.is_visible(),
                        f"{fixture} lacks visible {name}")
                _assert(bool(target.inner_text().strip()), f"{fixture} has empty {name}")
        if expected_state == "calculating":
            _assert(workbench.get("rangeJobCancellable") is True,
                    "range-calculating must be cancellable")
        if expected_state == "applying":
            _assert(workbench.get("rangeJobCancellable") is False,
                    "range-applying must be non-cancellable")
            _assert(page.locator(_qa("range-cancel")).is_disabled(),
                    "range applying leaves cancel enabled")
        if expected_state == "error":
            alert = page.locator(_qa("range-error"))
            _assert(alert.is_visible() and alert.get_attribute("role") == "alert",
                    "range error lacks accessible alert")
    else:
        actual = workbench.get("exportState")
        kind = workbench.get("exportKind")
        _assert(actual == expected_state, f"{fixture} exportState={actual!r}")
        _assert(kind == expected_kind, f"{fixture} exportKind={kind!r}")
        dialog = page.locator(_qa("export-dialog"))
        _assert(dialog.count() == 1 and dialog.is_visible(), f"{fixture} export dialog is absent")
        busy = expected_state == "exporting"
        _assert((dialog.get_attribute("aria-busy") == "true") == busy,
                f"{fixture} export aria-busy mismatch")
        pending = page.locator(_qa("export-include-pending"))
        if expected_kind == "review_results":
            _assert(pending.count() == 1 and pending.is_visible(),
                    f"{fixture} lacks pending switch")
        else:
            _assert(pending.count() == 0 or not pending.is_visible(),
                    "audit export exposes the review-only pending switch")
        if expected_state == "exporting":
            _assert(workbench.get("exportJobCancellable") is False,
                    "exporting must be non-cancellable")
            _assert(page.locator(_qa("export-cancel")).is_disabled(),
                    "exporting leaves cancel enabled")
        if expected_state == "error":
            alert = page.locator(_qa("export-error"))
            _assert(alert.is_visible() and alert.get_attribute("role") == "alert",
                    "export error lacks accessible alert")
    return {
        "fixture": fixture,
        "range_state": workbench.get("rangeState"),
        "export_state": workbench.get("exportState"),
        "export_kind": workbench.get("exportKind"),
        "dom_safety": _assert_safe_dom(page),
    }


def _check_fixtures(page: Any, base_url: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for fixture in R5_FIXTURES:
        before = len(requests)
        _goto_fixture(page, base_url, fixture)
        rows.append(_assert_fixture_surface(page, fixture))
        _assert(len(requests) == before, f"{fixture} fixture made API requests")
        storage = _storage_evidence(page)
        _assert_no_browser_persistence(storage)
    return {"ok": True, "fixtures": rows, "api_requests": 0}


def _check_standard_fixture_geometry(page: Any, base_url: str) -> dict[str, Any]:
    """Guard compact operation-dialog reflow and scroll/footer separation."""

    rows: list[dict[str, Any]] = []
    try:
        for viewport in R5_STANDARD_VIEWPORTS:
            page.set_viewport_size(viewport)
            for fixture in R5_FIXTURES:
                _goto_fixture(page, base_url, fixture)
                dialog = page.locator("dialog[open]")
                _assert(dialog.count() == 1, f"{fixture}@{viewport['width']} lacks one dialog")
                audit = dialog.evaluate(
                    """dialog => {
                      const surface = dialog.querySelector('.modal__surface');
                      const body = dialog.querySelector('.modal__body');
                      const footer = dialog.querySelector('.modal__footer');
                      const box = node => {
                        const rect = node?.getBoundingClientRect?.();
                        const style = node ? getComputedStyle(node) : null;
                        return node && rect && style ? {
                          left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom,
                          width: rect.width, height: rect.height,
                          clientWidth: node.clientWidth, scrollWidth: node.scrollWidth,
                          clientHeight: node.clientHeight, scrollHeight: node.scrollHeight,
                          scrollTop: node.scrollTop, overflowX: style.overflowX,
                          overflowY: style.overflowY, textOverflow: style.textOverflow,
                        } : null;
                      };
                      const legend = dialog.querySelector('.operation-fields legend');
                      const labels = Array.from(dialog.querySelectorAll('.operation-fields > label'));
                      const legendBox = box(legend);
                      const labelBoxes = labels.map(box);
                      const error = dialog.querySelector(
                        '[data-qa="range-error"]:not([hidden]), '
                        + '[data-qa="export-error"]:not([hidden])'
                      );
                      return {
                        documentOverflow: document.documentElement.scrollWidth
                          > document.documentElement.clientWidth + 1,
                        viewportWidth: document.documentElement.clientWidth,
                        surface: box(surface), body: box(body), footer: box(footer),
                        legend: legendBox, labels: labelBoxes,
                        legendLabelGap: legendBox && labelBoxes[0]
                          ? labelBoxes[0].top - legendBox.bottom : null,
                        labelGap: labelBoxes.length > 1
                          ? Math.max(
                              labelBoxes[1].left - labelBoxes[0].right,
                              labelBoxes[0].left - labelBoxes[1].right,
                              labelBoxes[1].top - labelBoxes[0].bottom,
                              labelBoxes[0].top - labelBoxes[1].bottom,
                            ) : null,
                        error: box(error),
                        errorText: (error?.textContent || '').trim(),
                      };
                    }"""
                )
                surface = audit["surface"]
                body = audit["body"]
                footer = audit["footer"]
                _assert(not audit["documentOverflow"],
                        f"{fixture}@{viewport['width']} has document overflow")
                _assert(surface and body and footer,
                        f"{fixture}@{viewport['width']} dialog geometry is incomplete")
                _assert(
                    surface["left"] >= -1
                    and surface["right"] <= audit["viewportWidth"] + 1,
                    f"{fixture}@{viewport['width']} dialog escapes the viewport",
                )
                _assert(
                    body["scrollWidth"] <= body["clientWidth"] + 1,
                    f"{fixture}@{viewport['width']} dialog body clips horizontally",
                )
                _assert(
                    body["bottom"] <= footer["top"] + 1,
                    f"{fixture}@{viewport['width']} footer overlaps the scrolling body",
                )
                _assert(
                    footer["bottom"] <= surface["bottom"] + 1,
                    f"{fixture}@{viewport['width']} footer escapes its surface",
                )
                _assert(
                    body["overflowY"] in {"auto", "scroll"},
                    f"{fixture}@{viewport['width']} body is not vertically scrollable",
                )
                if fixture.startswith("range-"):
                    _assert(
                        audit["legendLabelGap"] is not None
                        and audit["legendLabelGap"] >= 4.0 - 0.25,
                        f"{fixture}@{viewport['width']} range legend overlaps its labels: "
                        f"gap={audit['legendLabelGap']}",
                    )
                    _assert(
                        audit["labelGap"] is None or audit["labelGap"] >= 4.0 - 0.25,
                        f"{fixture}@{viewport['width']} range labels overlap: "
                        f"gap={audit['labelGap']}",
                    )
                if fixture in {"range-error", "export-error"}:
                    error = audit["error"]
                    _assert(error and audit["errorText"],
                            f"{fixture}@{viewport['width']} lacks visible error copy")
                    _assert(
                        error["scrollWidth"] <= error["clientWidth"] + 1
                        and error["scrollHeight"] <= error["clientHeight"] + 1,
                        f"{fixture}@{viewport['width']} error content is clipped",
                    )

                bottom = dialog.evaluate(
                    """dialog => {
                      const body = dialog.querySelector('.modal__body');
                      const footer = dialog.querySelector('.modal__footer');
                      body.scrollTop = body.scrollHeight;
                      const visible = Array.from(body.children).filter(node => {
                        const style = getComputedStyle(node);
                        const box = node.getBoundingClientRect();
                        return !node.hidden && style.display !== 'none' && box.height > 0;
                      });
                      const last = visible.at(-1);
                      const bodyBox = body.getBoundingClientRect();
                      const footerBox = footer.getBoundingClientRect();
                      const lastBox = last?.getBoundingClientRect?.();
                      return {
                        scrollTop: body.scrollTop,
                        maxScroll: Math.max(0, body.scrollHeight - body.clientHeight),
                        bodyBottom: bodyBox.bottom,
                        footerTop: footerBox.top,
                        lastBottom: lastBox?.bottom ?? null,
                        lastTop: lastBox?.top ?? null,
                      };
                    }"""
                )
                _assert(
                    abs(bottom["scrollTop"] - bottom["maxScroll"]) <= 1,
                    f"{fixture}@{viewport['width']} body cannot reach its scroll end",
                )
                _assert(
                    bottom["lastBottom"] is not None
                    and bottom["lastBottom"] <= bottom["bodyBottom"] + 1
                    and bottom["lastBottom"] <= bottom["footerTop"] + 1,
                    f"{fixture}@{viewport['width']} final body content is covered by footer",
                )
                rows.append({
                    "fixture": fixture,
                    "viewport_css_pixels": dict(viewport),
                    "native_dpi_evidence": False,
                    "audit": audit,
                    "scroll_end": bottom,
                })
    finally:
        page.set_viewport_size({"width": 1366, "height": 768})
    return {"ok": True, "rows": rows, "native_dpi_evidence": False}


def _goto_real(page: Any, base_url: str) -> dict[str, Any]:
    page.goto(base_url)
    _wait_for_eval(page, "window.__MS_EVENT_STUDIO__?.getState?.().ready === true", timeout=20_000)
    _wait_for_eval(
        page,
        "window.__MS_EVENT_STUDIO__?.getState?.().workbench?.rangeState === 'closed'",
        timeout=20_000,
    )
    state = _hook_state(page)
    _assert(state.get("fixture") in {None, ""}, "real R5 gate loaded a fixture")
    _assert(state.get("view") == "project", "real R5 project did not open")
    _assert_safe_dom(page)
    return state["workbench"]


def _wait_workbench(page: Any, field: str, value: str, timeout: int = 20_000) -> dict[str, Any]:
    _wait_for_eval(
        page,
        "arg => window.__MS_EVENT_STUDIO__?.getState?.().workbench?.[arg.field] === arg.value",
        arg={"field": field, "value": value},
        timeout=timeout,
    )
    return _workbench_state(page)


def _assert_focus(
    page: Any,
    expected_qa: str,
    label: str,
    *,
    require_visible: bool,
) -> dict[str, Any]:
    _wait_for_eval(
        page,
        "arg => document.activeElement?.dataset?.qa === arg",
        arg=expected_qa,
        timeout=5_000,
    )
    evidence = page.evaluate(
        """() => {
          const active = document.activeElement;
          const style = active ? getComputedStyle(active) : null;
          return {
            data_qa: active?.dataset?.qa || '',
            focus_visible: Boolean(active?.matches?.(':focus-visible')),
            outline_style: style?.outlineStyle || '',
            outline_width_px: Number.parseFloat(style?.outlineWidth || '0') || 0,
          };
        }"""
    )
    _assert(evidence["data_qa"] == expected_qa, f"{label} focus moved to the wrong control")
    if require_visible:
        _assert(evidence["focus_visible"] is True, f"{label} focus is not :focus-visible")
        _assert(evidence["outline_style"] not in {"", "none"}, f"{label} focus has no outline")
        _assert(evidence["outline_width_px"] >= 2.0, f"{label} focus outline is too thin")
    return evidence


def _wait_request_count(
    page: Any,
    requests: list[dict[str, Any]],
    predicate: Callable[[dict[str, Any]], bool],
    expected: int,
    *,
    timeout: int = 5_000,
) -> int:
    deadline = time.monotonic() + timeout / 1_000
    while time.monotonic() < deadline:
        count = sum(predicate(row) for row in requests)
        if count >= expected:
            return count
        page.wait_for_timeout(20)
    return sum(predicate(row) for row in requests)


def _range_input(page: Any, start: str = "0.75", end: str = "2") -> None:
    page.locator(_qa("range-start")).fill(start)
    page.locator(_qa("range-end")).fill(end)


def _start_range_preview(page: Any, start: str = "0.75", end: str = "2") -> None:
    page.locator(_qa("change-range")).click()
    _wait_workbench(page, "rangeState", "input")
    _range_input(page, start, end)
    page.locator(_qa("range-submit-preview")).click()


def _snapshot_manifest(project_dir: Path) -> tuple[bytes, dict[str, Any]]:
    manifest = project_dir / "ms_event_project.json"
    return manifest.read_bytes(), _scientific_snapshot(project_dir)


def _check_range_cancel_paths(
    page: Any,
    project_dir: Path,
    requests: list[dict[str, Any]],
    background_busy: Callable[[], bool],
) -> dict[str, Any]:
    manifest_before, science_before = _snapshot_manifest(project_dir)
    entered = threading.Event()
    release = threading.Event()
    from ms_event_studio.range_change import preview_range_change as real_preview

    def slow_preview(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        if not release.wait(10):
            raise TimeoutError("R5 gate did not release range preview")
        return real_preview(*args, **kwargs)

    held_responses: list[tuple[Any, Any]] = []

    def hold_initial_response(route: Any) -> None:
        response = route.fetch()
        held_responses.append((route, response))

    before_job_cancel = sum(
        row["method"] == "POST" and row["path"].startswith(JOB_PREFIX)
        and row["path"].endswith("/cancel")
        for row in requests
    )
    page.route("**/api/range-changes/preview", hold_initial_response)
    try:
        with patch("ms_event_studio.web_app.preview_range_change", side_effect=slow_preview):
            _start_range_preview(page)
            _assert(entered.wait(5), "range preview worker did not enter")
            deadline = time.monotonic() + 5
            while not held_responses and time.monotonic() < deadline:
                page.wait_for_timeout(20)
            _assert(held_responses, "range preview 202 response was not held")
            _wait_workbench(page, "rangeState", "calculating")
            page.keyboard.press("Escape")
            page.wait_for_timeout(100)
            _assert(_workbench_state(page).get("rangeState") == "calculating",
                    "Escape hid calculating before the browser received its job capability")
            held_route, held_response = held_responses[0]
            held_route.fulfill(response=held_response)
            cancel_predicate = lambda row: (
                row["method"] == "POST"
                and row["path"].startswith(JOB_PREFIX)
                and row["path"].endswith("/cancel")
            )
            after_job_cancel = _wait_request_count(
                page, requests, cancel_predicate, before_job_cancel + 1
            )
            _assert(after_job_cancel == before_job_cancel + 1,
                    "pending calculating Escape did not cancel exactly one preview job; "
                    f"before={before_job_cancel}, after={after_job_cancel}, requests={requests}")
            release.set()
            _wait_workbench(page, "rangeState", "closed")
            calculating_focus = _assert_focus(
                page,
                "change-range",
                "calculating Escape cancellation",
                require_visible=True,
            )
            deadline = time.monotonic() + 10
            while background_busy() and time.monotonic() < deadline:
                time.sleep(0.02)
            _assert(not background_busy(), "cancelled range preview worker remained active")
    finally:
        release.set()
        page.unroute("**/api/range-changes/preview", hold_initial_response)
    _assert((project_dir / "ms_event_project.json").read_bytes() == manifest_before,
            "cancelling a calculating preview changed manifest")
    _assert(_scientific_snapshot(project_dir) == science_before,
            "cancelling a calculating preview changed science")

    _start_range_preview(page)
    _wait_workbench(page, "rangeState", "preview")
    cancel_before = sum(row["path"] == RANGE_ENDPOINTS["cancel"] for row in requests)
    page.keyboard.press("Escape")
    _wait_workbench(page, "rangeState", "closed")
    preview_focus = _assert_focus(
        page, "change-range", "range preview Escape", require_visible=True
    )
    cancel_after = sum(row["path"] == RANGE_ENDPOINTS["cancel"] for row in requests)
    _assert(cancel_after == cancel_before + 1, "preview Escape did not cancel exactly once")
    _assert(_scientific_snapshot(project_dir) == science_before,
            "ready preview Escape changed science")
    return {
        "ok": True,
        "calculating_escape_job_cancel_requests": 1,
        "background_worker_idle": True,
        "ready_escape_cancel_requests": 1,
        "scientific_writes": 0,
        "focus": {
            "calculating_escape": calculating_focus,
            "preview_escape": preview_focus,
        },
    }


def _dismiss_range_error(page: Any) -> None:
    control = page.locator(_qa("range-cancel"))
    _assert(control.count() == 1 and control.is_enabled(), "range error cannot be dismissed")
    control.click()
    _wait_workbench(page, "rangeState", "closed")


def _check_range_failure_rollback(
    page: Any,
    project_dir: Path,
    make_preview_stale: Callable[[], Any],
) -> dict[str, Any]:
    from ms_event_studio.errors import ProjectValidationError

    _start_range_preview(page)
    _wait_workbench(page, "rangeState", "preview")
    make_preview_stale()
    stale_expected = _scientific_snapshot(project_dir)
    manifest = project_dir / "ms_event_project.json"
    manifest_before = manifest.read_bytes()
    page.locator(_qa("range-apply")).click()
    _wait_workbench(page, "rangeState", "error", timeout=20_000)
    _assert(manifest.read_bytes() == manifest_before, "stale range apply changed manifest")
    _assert(_scientific_snapshot(project_dir) == stale_expected,
            "stale range apply changed the externally updated scientific state")
    stale_alert = page.locator(_qa("range-error"))
    _assert(stale_alert.is_visible() and stale_alert.get_attribute("role") == "alert",
            "stale range apply lacks accessible error")
    stale_text = stale_alert.inner_text().strip()
    _assert(bool(stale_text), "stale range apply error is empty")
    _dismiss_range_error(page)

    _start_range_preview(page)
    _wait_workbench(page, "rangeState", "preview")
    failed_expected = _scientific_snapshot(project_dir)
    manifest_before = manifest.read_bytes()
    with patch(
        "ms_event_studio.web_app.apply_range_change",
        side_effect=ProjectValidationError("injected safe range failure"),
    ):
        page.locator(_qa("range-apply")).click()
        _wait_workbench(page, "rangeState", "error", timeout=20_000)
    _assert(manifest.read_bytes() == manifest_before, "failed range apply changed manifest")
    _assert(_scientific_snapshot(project_dir) == failed_expected,
            "failed range apply changed scientific state")
    failed_alert = page.locator(_qa("range-error"))
    _assert(failed_alert.is_visible() and failed_alert.get_attribute("role") == "alert",
            "failed range apply lacks accessible error")
    failed_text = failed_alert.inner_text().strip()
    _assert(bool(failed_text), "failed range apply error is empty")
    _dismiss_range_error(page)
    return {
        "ok": True,
        "stale": {"original_state_preserved": True, "alert": stale_text},
        "injected_failure": {"original_state_preserved": True, "alert": failed_text},
    }


def _check_range_apply_and_reopen(
    page: Any,
    base_url: str,
    project_dir: Path,
    reopen_project: Callable[[], Any],
) -> dict[str, Any]:
    before = _scientific_snapshot(project_dir)
    _start_range_preview(page)
    preview = _wait_workbench(page, "rangeState", "preview")
    _assert(preview.get("rangePreviewPresent") is True, "range preview capability is absent")
    entered = threading.Event()
    release = threading.Event()
    from ms_event_studio.range_change import apply_range_change as real_apply

    def slow_apply(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        if not release.wait(10):
            raise TimeoutError("R5 gate did not release range apply")
        return real_apply(*args, **kwargs)

    with patch("ms_event_studio.web_app.apply_range_change", side_effect=slow_apply):
        page.locator(_qa("range-apply")).click()
        _assert(entered.wait(5), "range apply worker did not enter")
        applying = _wait_workbench(page, "rangeState", "applying")
        _assert(applying.get("rangeJobCancellable") is False, "range apply is marked cancellable")
        _assert(page.locator(_qa("range-cancel")).is_disabled(),
                "range applying cancel is enabled")
        page.keyboard.press("Escape")
        _assert(_workbench_state(page).get("rangeState") == "applying",
                "Escape closed a non-cancellable range apply")
        release.set()
        _wait_workbench(page, "rangeState", "closed", timeout=30_000)
    after = _scientific_snapshot(project_dir)
    _assert(after["range_min"] == {"start": "0.75", "end": "2"},
            f"range apply produced {after['range_min']}")
    _assert(after["audit_count"] == before["audit_count"] + 1,
            "range apply did not append exactly one audit action")
    _assert(after["audit_actions"][-1] == "recalculate_analysis_range",
            "range apply wrote the wrong audit action")
    reopen_project()
    reopened_hook = _goto_real(page, base_url)
    reopened = _scientific_snapshot(project_dir)
    _assert(reopened == after, "range/project state changed after reopen")
    _assert(reopened_hook.get("analysisRange") in ({"start_min": "0.75", "end_min": "2"}, None)
            or reopened_hook.get("viewport", {}).get("start_min") >= 0.75,
            "reopened UI did not reflect the applied range")
    return {
        "ok": True,
        "applying_escape_blocked": True,
        "before": before,
        "after": after,
        "reopened": reopened,
    }


def _read_csv_contract(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = tuple(reader.fieldnames or ())
    _assert(fields == HUMAN_COLUMNS, f"CSV columns changed: {fields}")
    return {
        "columns": list(fields),
        "rows": len(rows),
        "statuses": sorted({row["review_status"] for row in rows}),
        "sha256": _sha256(path),
    }


def _read_audit_contract(target: Path) -> dict[str, Any]:
    import pandas as pd

    names = tuple(sorted(path.name for path in target.iterdir()))
    _assert(names == AUDIT_FILES, f"audit package files changed: {names}")
    table_path = target / "events.parquet"
    manifest_path = target / "manifest.json"
    sidecar_path = target / "checksums.sha256"
    table = pd.read_parquet(table_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _assert(manifest.get("schema") == "ms-event-machine-contract-v1",
            "audit manifest schema changed")
    _assert(manifest.get("event_table", {}).get("row_count") == len(table),
            "audit manifest row count disagrees with Parquet")
    _assert(manifest.get("event_table", {}).get("sha256") == _sha256(table_path),
            "audit manifest Parquet digest is stale")
    expected_lines = {
        f"{_sha256(table_path)}  events.parquet",
        f"{_sha256(manifest_path)}  manifest.json",
    }
    actual_lines = {
        line for line in sidecar_path.read_text(encoding="ascii").splitlines() if line
    }
    _assert(actual_lines == expected_lines, "audit checksum sidecar is invalid")
    return {
        "files": list(names),
        "schema": manifest["schema"],
        "event_rows": len(table),
        "status_counts": manifest.get("status_counts"),
        "checksums_valid": True,
    }


def _open_export(page: Any, kind: str) -> None:
    page.locator(_qa("export-review-results")).click()
    _wait_workbench(page, "exportState", "input")
    if kind == "audit_package":
        page.locator(_qa("export-audit-package")).click()
        _wait_for_eval(
            page,
            "window.__MS_EVENT_STUDIO__?.getState?.().workbench?.exportKind === 'audit_package'",
            timeout=5_000,
        )


def _select_target_and_submit(page: Any) -> None:
    page.locator(_qa("export-choose-target")).click()
    _wait_for_eval(
        page,
        "window.__MS_EVENT_STUDIO__?.getState?.().workbench?.exportTargetSelected === true",
        timeout=5_000,
    )
    target_name = page.locator(_qa("export-target-name"))
    _assert(target_name.is_visible() and bool(target_name.inner_text().strip()),
            "export target exposes no display name")
    _assert("\\" not in target_name.inner_text() and "/" not in target_name.inner_text(),
            "export target display leaks a filesystem path")
    page.locator(_qa("export-submit")).click()


def _check_export_cancel_paths(page: Any, requests: list[dict[str, Any]]) -> dict[str, Any]:
    export_paths = set(EXPORT_ENDPOINTS.values())
    before = sum(row["path"] in export_paths for row in requests)
    _open_export(page, "review_results")
    page.locator(_qa("export-cancel")).click()
    _wait_workbench(page, "exportState", "closed")
    _assert(sum(row["path"] in export_paths for row in requests) == before,
            "closing export input issued an export request")

    _open_export(page, "review_results")
    page.locator(_qa("export-choose-target")).click()
    _wait_for_eval(
        page,
        "window.__MS_EVENT_STUDIO__?.getState?.().workbench?.exportTargetSelected === false",
        timeout=5_000,
    )
    _assert(sum(row["path"] in export_paths for row in requests) == before,
            "cancelling native target selection issued an export request")
    page.locator(_qa("export-cancel")).click()
    _wait_workbench(page, "exportState", "closed")
    return {"ok": True, "modal_cancel_writes": 0, "native_dialog_cancel_writes": 0}


def _check_exports(
    page: Any,
    output_paths: dict[str, Path],
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    _open_export(page, "review_results")
    _select_target_and_submit(page)
    _wait_workbench(page, "exportState", "success", timeout=20_000)
    default_csv = _read_csv_contract(output_paths["review_default"])
    _assert(default_csv["statuses"] == ["accepted"], "default CSV is not accepted-only")
    results["review_default"] = default_csv

    page.locator(_qa("export-cancel")).click()
    _wait_workbench(page, "exportState", "closed")
    _open_export(page, "review_results")
    page.locator(_qa("export-include-pending")).check()
    _select_target_and_submit(page)
    _wait_workbench(page, "exportState", "success", timeout=20_000)
    pending_csv = _read_csv_contract(output_paths["review_pending"])
    _assert(pending_csv["statuses"] == ["accepted", "pending"],
            "include-pending CSV statuses are incorrect")
    _assert(pending_csv["rows"] == default_csv["rows"] + 1,
            "include-pending did not add exactly the prepared pending row")
    results["review_include_pending"] = pending_csv

    page.locator(_qa("export-cancel")).click()
    _wait_workbench(page, "exportState", "closed")
    _open_export(page, "audit_package")
    with patch(
        "pandas.DataFrame.to_parquet",
        side_effect=PermissionError("injected audit failure"),
    ):
        _select_target_and_submit(page)
        _wait_workbench(page, "exportState", "error", timeout=20_000)
    failed_target = output_paths["audit_failure"]
    _assert(failed_target.is_dir() and list(failed_target.iterdir()) == [],
            "failed audit export left files in its target")
    _assert(list(failed_target.parent.glob(f".{failed_target.name}.machine-exporting-*")) == [],
            "failed audit export left a staging directory")
    alert = page.locator(_qa("export-error"))
    _assert(alert.is_visible() and alert.get_attribute("role") == "alert",
            "failed audit export lacks accessible error")
    results["audit_failure"] = {
        "target_empty": True,
        "staging_cleaned": True,
        "alert": alert.inner_text().strip(),
    }
    page.locator(_qa("export-cancel")).click()
    _wait_workbench(page, "exportState", "closed")

    _open_export(page, "audit_package")
    entered = threading.Event()
    release = threading.Event()
    from ms_event_studio.web_review_service import BrowserWorkspaceService

    real_audit_export = BrowserWorkspaceService.export_audit_package

    def slow_audit_export(service: Any, *args: Any, **kwargs: Any) -> Any:
        entered.set()
        if not release.wait(10):
            raise TimeoutError("R5 gate did not release audit export")
        return real_audit_export(service, *args, **kwargs)

    with patch.object(BrowserWorkspaceService, "export_audit_package", slow_audit_export):
        _select_target_and_submit(page)
        _assert(entered.wait(5), "audit export worker did not enter")
        exporting = _wait_workbench(page, "exportState", "exporting")
        _assert(exporting.get("exportJobCancellable") is False, "audit export is marked cancellable")
        _assert(page.locator(_qa("export-cancel")).is_disabled(),
                "exporting leaves close/cancel enabled")
        page.keyboard.press("Escape")
        _assert(_workbench_state(page).get("exportState") == "exporting",
                "Escape closed a non-cancellable export")
        release.set()
        _wait_workbench(page, "exportState", "success", timeout=30_000)
    target = output_paths["audit_success"]
    results["audit_package"] = _read_audit_contract(target)
    return {"ok": True, **results}


def run_gate(
    *,
    base_url: str,
    fixtures_only: bool,
    headed: bool,
    project_dir: Path | None = None,
    reopen_project: Callable[[], Any] | None = None,
    make_preview_stale: Callable[[], Any] | None = None,
    background_busy: Callable[[], bool] | None = None,
    output_paths: dict[str, Path] | None = None,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required: pip install -e .[qa] && playwright install chromium"
        ) from exc

    requests: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    checks: dict[str, Any] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            color_scheme="light",
            locale="zh-CN",
            device_scale_factor=1,
        )
        try:
            page = context.new_page()

            def record_request(request: Any) -> None:
                path = urlparse(request.url).path
                if path.startswith("/api/"):
                    requests.append({
                        "method": request.method.upper(),
                        "path": path,
                        "body": _body_json(request),
                    })

            def record_response(response: Any) -> None:
                path = urlparse(response.url).path
                if path.startswith("/api/"):
                    responses.append({"path": path, "status": response.status})

            page.on("request", record_request)
            page.on("response", record_response)
            page.on("console", lambda message: console_errors.append(message.text)
                    if message.type == "error" else None)
            page.on("pageerror", lambda error: page_errors.append(str(error)))

            checks["fixture_contract"] = _check_fixtures(page, base_url, requests)
            checks["standard_fixture_geometry"] = _check_standard_fixture_geometry(
                page, base_url
            )
            fixture_requests = list(requests)
            _assert(fixture_requests == [], f"R5 fixtures made requests: {fixture_requests}")
            if not fixtures_only:
                _assert(project_dir is not None, "real R5 gate lacks project directory")
                _assert(reopen_project is not None, "real R5 gate lacks reopen callback")
                _assert(make_preview_stale is not None, "real R5 gate lacks stale callback")
                _assert(background_busy is not None, "real R5 gate lacks job busy callback")
                _assert(output_paths is not None, "real R5 gate lacks export targets")
                _goto_real(page, base_url)
                checks["export_cancel_paths"] = _check_export_cancel_paths(page, requests)
                checks["range_cancel_paths"] = _check_range_cancel_paths(
                    page, project_dir, requests, background_busy
                )
                checks["range_failure_rollback"] = _check_range_failure_rollback(
                    page, project_dir, make_preview_stale
                )
                checks["range_apply_and_reopen"] = _check_range_apply_and_reopen(
                    page, base_url, project_dir, reopen_project
                )
                checks["exports"] = _check_exports(page, output_paths)
                _assert_safe_dom(page)
            unexpected = [
                message for message in console_errors
                if not message.startswith("Failed to load resource:")
            ]
            _assert(not page_errors, f"R5 page errors: {page_errors}")
            _assert(not unexpected, f"R5 application console errors: {unexpected}")
            checks["runtime_errors"] = {
                "ok": True,
                "page_errors": page_errors,
                "application_console_errors": unexpected,
            }
            checks["api_traffic"] = {"ok": True, "requests": requests, "responses": responses}
        finally:
            context.close()
            browser.close()
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", help="already-running server; valid only with --fixtures-only")
    parser.add_argument("--fixtures-only", action="store_true")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        default=REPOSITORY / "build/qa/ux-r5-range-export.json",
    )
    args = parser.parse_args(argv)

    report: dict[str, Any] = {
        "schema": "ms-event-studio-ux-r5-range-export-qa-v1",
        "started_at": _utc_now(),
        "fixtures_only": args.fixtures_only,
        "browser_viewport_is_css_pixels": True,
        "native_dpi_evidence": False,
        "status": "error",
    }
    server = None
    temporary = None
    source: Path | None = None
    source_before: dict[str, Any] | None = None
    try:
        if args.base_url and not args.fixtures_only:
            raise ValueError("real R5 QA requires its internally managed project and paths")
        base_url = args.base_url
        project_dir: Path | None = None
        reopen: Callable[[], Any] | None = None
        make_stale: Callable[[], Any] | None = None
        is_background_busy: Callable[[], bool] | None = None
        outputs: dict[str, Path] | None = None
        if not base_url:
            from ms_event_studio.web_app import WebSession, create_http_server

            temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
            root = Path(temporary.name)
            if args.fixtures_only:
                server = create_http_server(recent_path=root / "recent.json")
            else:
                source, project = _prepare_project(root)
                source_before = _file_fingerprint(source)
                project_dir = project.project_dir
                session = WebSession(root / "recent.json")
                selection = session.register_path("project_open", project_dir)
                project_token = selection["selection_token"]
                session.open_project(project_token)
                outputs = {
                    "review_default": root / "accepted.csv",
                    "review_pending": root / "accepted-pending.csv",
                    "audit_success": root / "audit-package",
                }
                role_queue = [
                    ("cancel:review_export_file", None),
                    ("review_export_file", outputs["review_default"]),
                    ("review_export_file", outputs["review_pending"]),
                    ("audit_export_target", root / "audit-failure"),
                    ("audit_export_target", outputs["audit_success"]),
                ]
                outputs["audit_failure"] = root / "audit-failure"
                outputs["audit_failure"].mkdir()

                def path_dialog(*, role: str, **_unused: Any) -> dict[str, Any]:
                    _assert(role_queue, f"unexpected native path request for {role}")
                    expected_role, path = role_queue.pop(0)
                    if expected_role.startswith("cancel:"):
                        _assert(role == expected_role.removeprefix("cancel:"),
                                f"path role {role!r}, expected cancellation for {expected_role!r}")
                        return {"cancelled": True}
                    _assert(role == expected_role, f"path role {role!r}, expected {expected_role!r}")
                    return {"path": str(path), "cancelled": False}

                server = create_http_server(session=session, path_dialog=path_dialog)
                reopen = lambda: session.open_project(project_token)
                is_background_busy = lambda: session.busy

                def make_stale() -> None:
                    from ms_event_studio.window_service import ProjectWindowService

                    with ProjectWindowService.open(project_dir) as competing:
                        event = competing.all_events()[0]
                        competing.review_store.set_status(
                            event["event_id"],
                            "rejected" if event["status"] != "rejected" else "unreviewed",
                            expected_revision=int(event["revision"]),
                            actor="other-window",
                            session_id="other-window",
                            reason="invalidate range preview",
                        )
            server.start()
            base_url = server.url

        report["server_mode"] = "external" if args.base_url else "ephemeral-loopback"
        report["checks"] = run_gate(
            base_url=base_url,
            fixtures_only=args.fixtures_only,
            headed=args.headed,
            project_dir=project_dir,
            reopen_project=reopen,
            make_preview_stale=make_stale,
            background_busy=is_background_busy,
            output_paths=outputs,
        )
        if source is not None and source_before is not None:
            source_after = _file_fingerprint(source)
            _assert(source_after == source_before, "R5 gate changed its raw scientific source")
            report["checks"]["source_read_only"] = {
                "ok": True,
                "before": source_before,
                "after": source_after,
            }
        report["status"] = "ok"
        return_code = 0
    except BaseException as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        return_code = 1
    finally:
        if server is not None:
            server.stop()
        if temporary is not None:
            temporary.cleanup()
        report["finished_at"] = _utc_now()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
