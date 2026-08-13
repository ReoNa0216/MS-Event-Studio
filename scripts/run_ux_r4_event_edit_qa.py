"""Standalone Playwright gate for the UX-R4 event-edit state machine.

The fixture half validates deterministic aim/preview/error states without any
requests.  The real-project half exercises the four canonical event-edit API
routes against a tiny disposable scientific project.  Browser viewports are
CSS-pixel evidence only; this script does not claim native DPI coverage.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Callable
from urllib.parse import urlparse


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
except ModuleNotFoundError:  # Imported as ``scripts.run_ux_r4_event_edit_qa``.
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


R4_FIXTURES = (
    "add-aim",
    "add-preview",
    "adjust-aim",
    "adjust-preview",
    "edit-out-of-range",
)
R4_STANDARD_VIEWPORTS = (
    {"width": 960, "height": 640},
    {"width": 1366, "height": 768},
    {"width": 1920, "height": 1080},
)
EDIT_ENDPOINTS = {
    "aim": "/api/event-edits/aim",
    "preview": "/api/event-edits/preview",
    "apply": "/api/event-edits/apply",
    "cancel": "/api/event-edits/cancel",
}
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
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _create_r4_scientific_project(root: Path) -> tuple[Path, Any]:
    """Build three automatic events plus one undetected real local peak."""

    from ms_event_studio.project import CreateProjectRequest, create_project
    from ms_event_studio.window_service import ProjectWindowService

    scan_count = 1201
    pc34_mz = 760.5851
    qc782_mz = 782.5616
    signal = [
        1000.0 * math.exp(-0.5 * ((index - 300.0) / 30.0) ** 2)
        for index in range(scan_count)
    ]
    # A real secondary local maximum inside the first automatic support lets
    # adjust preview snap from scan 300 to scan 310.
    signal[309] -= 10.0
    signal[310] += 10.0
    signal[311] -= 10.0
    # This positive local maximum remains below the automatic detector's
    # calling threshold and is the real add-event candidate at 0.75 min.
    signal[450] = 80.0
    signal[600] = 1200.0
    signal[900] = 1100.0

    source = root / "r4-event-edit-source.txt"
    with source.open("w", encoding="ascii", newline="\n") as handle:
        handle.write(f"spectrumList ({scan_count} spectra)\n")
        for index, intensity in enumerate(signal):
            values = (0.0, intensity, 10.0, 0.0)
            lines = (
                "spectrum:",
                f"  index: {index}",
                f"  id: scanId={100000 + index}",
                "  defaultArrayLength: 4",
                f"  cvParam: base peak m/z, {pc34_mz}",
                f"  cvParam: base peak intensity, {max(values):.15g}",
                "  cvParam: total ion current, 10000000, number of detector counts",
                "  cvParam: lowest observed m/z, 100",
                "  cvParam: highest observed m/z, 900",
                f"  cvParam: scan start time, {index / 600.0:.12f}, minute",
                "  cvParam: m/z array, m/z",
                f"  binary: [4] 100 {pc34_mz} {qc782_mz} 900",
                "  cvParam: intensity array, number of detector counts",
                "  binary: [4] " + " ".join(f"{value:.15g}" for value in values),
                "",
            )
            handle.write("\n".join(lines))

    project = create_project(
        CreateProjectRequest(
            source_path=source,
            project_dir=root / "r4-event-edit-project",
            display_name="异常峰编辑门禁项目",
            analysis_start_min="0",
            analysis_end_min="2",
        )
    )
    with ProjectWindowService.open(project.project_dir) as service:
        events = service.all_events()
        if len(events) != 3:
            raise AssertionError(f"R4 scientific fixture detected {len(events)} events, expected 3")
        first = min(events, key=lambda row: abs(int(row["current_spectrum_index"]) - 300))
        accepted = service.review_store.set_status(
            first["event_id"],
            "accepted",
            expected_revision=int(first["revision"]),
            actor="qa-fixture",
            session_id="qa-fixture",
            reason="prepare status-preserving adjustment",
        )
        if accepted.get("status") != "accepted":
            raise AssertionError("R4 scientific fixture could not prepare its accepted event")
    return source, project


def _scientific_snapshot(project_dir: Path) -> dict[str, Any]:
    """Return comparison evidence without exposing scientific identities."""

    from ms_event_studio.window_service import ProjectWindowService

    with ProjectWindowService.open(project_dir) as service:
        events = service.all_events()
        audits = service.review_store.audit_events()
        return {
            "event_count": len(events),
            "events": [
                {
                    "apex_time_sec": round(float(row["current_apex_time_sec"]), 9),
                    "status": str(row["status"]),
                    "origin": str(row["origin"]),
                }
                for row in events
            ],
            "audit_count": len(audits),
            "audit_actions": [str(row["action"]) for row in audits],
            "history": service.review_store.history_state(),
        }


def _request_count(requests: list[dict[str, Any]], kind: str) -> int:
    return sum(row["path"] == EDIT_ENDPOINTS[kind] for row in requests)


def _domain_state(page: Any) -> dict[str, Any]:
    workbench = _workbench_state(page)
    rows = _event_rows(page)
    return {
        "selected": workbench.get("selectedEventKey"),
        "status": workbench.get("status"),
        "source": workbench.get("source"),
        "event_count": workbench.get("eventCount"),
        "rows": [
            {
                "status": row["status"],
                "selected": row["selected"],
                "current": row["current"],
                "text": row["text"],
            }
            for row in rows
        ],
    }


def _selected_semantics(locator: Any) -> bool:
    for attribute in ("aria-pressed", "aria-checked", "aria-selected", "aria-current"):
        value = locator.get_attribute(attribute)
        if value is not None and value.casefold() not in {"", "false", "none"}:
            return True
    return False


def _wait_edit_state(
    page: Any,
    expected_state: str,
    expected_mode: str | None,
    *,
    timeout: int = 5_000,
) -> dict[str, Any]:
    _wait_for_eval(
        page,
        "arg => { const state = window.__MS_EVENT_STUDIO__?.getState?.().workbench; "
        "return state?.editState === arg.state && state?.editMode === arg.mode; }",
        arg={"state": expected_state, "mode": expected_mode},
        timeout=timeout,
    )
    return _workbench_state(page)


def _assert_edit_surface(
    page: Any,
    *,
    expected_state: str,
    expected_mode: str,
) -> dict[str, Any]:
    workbench = _workbench_state(page)
    _assert(workbench.get("editState") == expected_state, "wrong event-edit state")
    _assert(workbench.get("editMode") == expected_mode, "wrong event-edit mode")
    _assert(workbench.get("editTokenPresent") is True, "event-edit capability is absent")
    allowed = workbench.get("allowedInterval")
    _assert(
        isinstance(allowed, dict)
        and isinstance(allowed.get("startMin"), (int, float))
        and isinstance(allowed.get("endMin"), (int, float))
        and allowed["endMin"] >= allowed["startMin"],
        "event-edit allowed interval is absent or malformed",
    )

    bar = page.locator(_qa("edit-mode-bar"))
    cancel = page.locator(_qa("edit-cancel"))
    overlay = page.locator(_qa("edit-allowed-range"))
    _assert(bar.count() == 1 and bar.is_visible(), "event-edit mode bar is not visible")
    _assert(bool(bar.inner_text().strip()), "event-edit mode bar has no instruction")
    _assert(cancel.count() == 1 and cancel.is_visible(), "event-edit cancel is not visible")
    _assert(cancel.is_enabled(), "event-edit cancel is disabled outside saving")
    _assert(overlay.count() == 1 and overlay.is_visible(), "allowed interval overlay is absent")
    clipped = overlay.evaluate(
        """node => {
          for (let current = node; current; current = current.parentElement) {
            const clip = current.getAttribute?.('clip-path') || '';
            if (clip.includes('plotContentClip')) return true;
          }
          return false;
        }"""
    )
    _assert(clipped, "allowed interval overlay is not clipped to plot content")
    overlay_box = overlay.bounding_box()
    content_box = page.locator(_qa("plot-content")).bounding_box()
    _assert(overlay_box is not None and content_box is not None, "edit overlay has no geometry")
    _assert(
        overlay_box["x"] >= content_box["x"] - 0.5
        and overlay_box["y"] >= content_box["y"] - 0.5
        and overlay_box["x"] + overlay_box["width"]
        <= content_box["x"] + content_box["width"] + 0.5
        and overlay_box["y"] + overlay_box["height"]
        <= content_box["y"] + content_box["height"] + 0.5,
        "allowed interval overlay escapes plot content",
    )

    active = page.locator(_qa("add-event" if expected_mode == "add" else "adjust-apex"))
    _assert(active.count() == 1 and _selected_semantics(active), "edit mode button lacks active semantics")
    preview = expected_state == "preview"
    candidate = page.locator(_qa("edit-candidate"))
    change = page.locator(_qa("edit-change"))
    apply = page.locator(_qa("edit-apply"))
    if preview:
        _assert(candidate.count() == 1 and candidate.is_visible(), "preview candidate is absent")
        _assert(change.count() == 1 and change.is_visible(), "before/after change is absent")
        _assert(bool(change.inner_text().strip()), "before/after change has no readable values")
        _assert(apply.count() == 1 and apply.is_visible() and apply.is_enabled(),
                "preview apply action is unavailable")
        _assert(isinstance(workbench.get("candidateTimeMin"), (int, float)),
                "preview candidate time is absent from the read-only hook")
    else:
        _assert(not candidate.is_visible(), "aim/error state shows a committed candidate")
        _assert(not change.is_visible(), "aim/error state shows a before/after preview")
        _assert(not apply.is_visible() or not apply.is_enabled(),
                "aim/error state exposes an enabled apply action")
    return {
        "edit_state": expected_state,
        "edit_mode": expected_mode,
        "allowed_interval": allowed,
        "overlay_clipped": True,
        "active_semantics": True,
        "candidate_time_min": workbench.get("candidateTimeMin"),
    }


def _assert_no_internal_dom_fields(page: Any) -> dict[str, Any]:
    audit = page.evaluate(
        r"""arg => {
          const visible = (document.body.innerText || '') + '\n' +
            Array.from(document.querySelectorAll('[aria-label],[title],[placeholder]'))
              .flatMap(node => ['aria-label','title','placeholder'].map(name => node.getAttribute(name) || ''))
              .join('\n');
          const dataNames = Array.from(document.querySelectorAll('*'))
            .flatMap(node => Array.from(node.attributes || []))
            .map(attr => attr.name.toLocaleLowerCase())
            .filter(name => name.startsWith('data-'));
          return {
            forbiddenCopy: arg.copy.filter(term => visible.toLocaleLowerCase().includes(term.toLocaleLowerCase())),
            forbiddenDataNames: [...new Set(dataNames.filter(name => arg.data.some(term => name.includes(term))))],
          };
        }""",
        {"copy": list(FORBIDDEN_UI_TERMS), "data": list(FORBIDDEN_DOM_DATA)},
    )
    _assert(audit["forbiddenCopy"] == [], f"event-edit UI leaked forbidden copy: {audit}")
    _assert(audit["forbiddenDataNames"] == [], f"event-edit DOM leaked implementation fields: {audit}")
    return {"ok": True, **audit}


def _check_fixture_states(
    page: Any,
    base_url: str,
    requests: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = {
        "add-aim": ("aiming", "add"),
        "add-preview": ("preview", "add"),
        "adjust-aim": ("aiming", "adjust"),
        "adjust-preview": ("preview", "adjust"),
        "edit-out-of-range": ("error", "adjust"),
    }
    rows: list[dict[str, Any]] = []
    before_requests = len(requests)
    for fixture in R4_FIXTURES:
        _goto_fixture(page, base_url, fixture)
        page.locator(_qa("workbench")).wait_for(state="visible", timeout=20_000)
        state, mode = expected[fixture]
        evidence = _assert_edit_surface(
            page,
            expected_state=state,
            expected_mode=mode,
        )
        if fixture == "edit-out-of-range":
            error = page.locator(_qa("edit-error"))
            _assert(error.count() == 1 and error.is_visible(), "out-of-range fixture has no error")
            _assert(error.get_attribute("role") == "alert", "edit error is not an alert")
            _assert(bool(error.inner_text().strip()), "edit error has no actionable copy")
        else:
            error = page.locator(_qa("edit-error"))
            _assert(error.count() == 1 and not error.is_visible(), f"{fixture}: stale error is visible")
        evidence["fixture"] = fixture
        evidence["dom_safety"] = _assert_no_internal_dom_fields(page)
        rows.append(evidence)
    _assert(len(requests) == before_requests, "R4 visual fixtures issued network requests")
    storage = _storage_evidence(page)
    _assert_no_browser_persistence(storage)
    return {
        "ok": True,
        "fixtures": rows,
        "network_requests": 0,
        "storage": storage,
    }


def _check_compact_fact_visibility(page: Any, base_url: str) -> dict[str, Any]:
    """Guard scientific edit facts against compact-viewport ellipsis/crop."""

    page.set_viewport_size({"width": 960, "height": 640})
    rows: list[dict[str, Any]] = []
    try:
        for fixture in ("add-preview", "adjust-preview"):
            _goto_fixture(page, base_url, fixture)
            facts = page.eval_on_selector_all(
                "[data-qa='edit-allowed-range-copy'], [data-qa='edit-candidate'], "
                "[data-qa='edit-change']",
                """nodes => nodes.map(node => {
                  const value = node.querySelector('dd') || node;
                  const style = getComputedStyle(value);
                  const range = document.createRange();
                  range.selectNodeContents(value);
                  const textRect = range.getBoundingClientRect();
                  const box = value.getBoundingClientRect();
                  return {
                    qa: node.dataset.qa,
                    text: (value.textContent || '').trim(),
                    clientWidth: value.clientWidth,
                    scrollWidth: value.scrollWidth,
                    clientHeight: value.clientHeight,
                    scrollHeight: value.scrollHeight,
                    textRight: textRect.right,
                    boxRight: box.right,
                    textBottom: textRect.bottom,
                    boxBottom: box.bottom,
                    overflowX: style.overflowX,
                    textOverflow: style.textOverflow,
                    whiteSpace: style.whiteSpace,
                    visible: Boolean(box.width && box.height),
                  };
                })""",
            )
            _assert(len(facts) == 3, f"{fixture}: expected all three edit facts")
            for fact in facts:
                _assert(fact["visible"] and fact["text"],
                        f"{fixture}: {fact['qa']} is hidden or empty")
                _assert(fact["scrollWidth"] <= fact["clientWidth"] + 1,
                        f"{fixture}: {fact['qa']} is horizontally clipped")
                _assert(fact["scrollHeight"] <= fact["clientHeight"] + 1,
                        f"{fixture}: {fact['qa']} is vertically clipped")
                _assert(fact["textRight"] <= fact["boxRight"] + 1,
                        f"{fixture}: {fact['qa']} text escapes its right edge")
                _assert(fact["textBottom"] <= fact["boxBottom"] + 1,
                        f"{fixture}: {fact['qa']} text escapes its bottom edge")
                _assert(fact["textOverflow"] != "ellipsis",
                        f"{fixture}: {fact['qa']} uses ellipsis for a critical value")
            rows.append({"fixture": fixture, "facts": facts})
    finally:
        page.set_viewport_size({"width": 1366, "height": 768})
    return {
        "ok": True,
        "viewport_css_pixels": {"width": 960, "height": 640},
        "native_dpi_evidence": False,
        "fixtures": rows,
    }


def _check_standard_fixture_reflow(page: Any, base_url: str) -> dict[str, Any]:
    """Audit all R4 fixtures at the standard screenshot viewports.

    Vertical reflow is allowed.  Horizontal document overflow, child clipping,
    ellipsis, or overlap between the mode-bar regions is not.
    """

    rows: list[dict[str, Any]] = []
    try:
        for viewport in R4_STANDARD_VIEWPORTS:
            page.set_viewport_size(viewport)
            for fixture in R4_FIXTURES:
                _goto_fixture(page, base_url, fixture)
                page.locator(_qa("edit-mode-bar")).wait_for(state="visible", timeout=20_000)
                audit = page.evaluate(
                    """() => {
                      const root = document.documentElement;
                      const bar = document.querySelector('[data-qa="edit-mode-bar"]');
                      const copy = bar?.querySelector('.edit-mode-bar__copy');
                      const facts = bar?.querySelector('.edit-mode-bar__facts');
                      const actions = bar?.querySelector('.edit-mode-bar__actions');
                      const position = bar?.querySelector('[data-qa="edit-position-readout"]');
                      const rect = node => {
                        const box = node?.getBoundingClientRect?.();
                        const style = node ? getComputedStyle(node) : null;
                        return node && box && style ? {
                          left: box.left, top: box.top, right: box.right, bottom: box.bottom,
                          width: box.width, height: box.height,
                          clientWidth: node.clientWidth, scrollWidth: node.scrollWidth,
                          clientHeight: node.clientHeight, scrollHeight: node.scrollHeight,
                          overflowX: style.overflowX, textOverflow: style.textOverflow,
                          whiteSpace: style.whiteSpace,
                        } : null;
                      };
                      const boxes = {
                        bar: rect(bar), copy: rect(copy), facts: rect(facts),
                        actions: rect(actions), position: rect(position),
                      };
                      const intersects = (a, b) => Boolean(a && b
                        && Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1
                        && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1);
                      const children = Array.from(bar?.querySelectorAll(
                        '.edit-mode-bar__copy, .edit-mode-bar__facts, '
                        + '.edit-mode-bar__actions, [data-qa="edit-error"]'
                      ) || []).filter(node => !node.hidden).map(node => ({
                        className: node.className || '',
                        qa: node.dataset.qa || '',
                        ...rect(node),
                      }));
                      return {
                        state: bar?.dataset.state || '',
                        positionHidden: Boolean(position?.closest('[hidden]')),
                        document: {
                          clientWidth: root.clientWidth,
                          scrollWidth: root.scrollWidth,
                          horizontalOverflow: root.scrollWidth > root.clientWidth + 1,
                        },
                        boxes,
                        children,
                        overlaps: {
                          copyFacts: intersects(boxes.copy, boxes.facts),
                          copyActions: intersects(boxes.copy, boxes.actions),
                          factsActions: intersects(boxes.facts, boxes.actions),
                        },
                      };
                    }"""
                )
                _assert(
                    not audit["document"]["horizontalOverflow"],
                    f"{fixture}@{viewport['width']} has document horizontal overflow",
                )
                bar = audit["boxes"]["bar"]
                _assert(bar is not None and bar["width"] > 0 and bar["height"] > 0,
                        f"{fixture}@{viewport['width']} mode bar has no geometry")
                _assert(
                    bar["left"] >= -1
                    and bar["right"] <= audit["document"]["clientWidth"] + 1,
                    f"{fixture}@{viewport['width']} mode bar escapes the viewport",
                )
                _assert(
                    bar["scrollWidth"] <= bar["clientWidth"] + 1,
                    f"{fixture}@{viewport['width']} mode bar clips horizontally",
                )
                for name in ("copy", "facts", "actions"):
                    box = audit["boxes"][name]
                    _assert(box is not None and box["width"] >= 44 and box["height"] >= 16,
                            f"{fixture}@{viewport['width']} lacks visible {name}")
                    _assert(
                        box["left"] >= bar["left"] - 1
                        and box["right"] <= bar["right"] + 1,
                        f"{fixture}@{viewport['width']} {name} escapes mode bar",
                    )
                    _assert(
                        box["scrollWidth"] <= box["clientWidth"] + 1,
                        f"{fixture}@{viewport['width']} {name} clips horizontally",
                    )
                    _assert(
                        box["textOverflow"] != "ellipsis",
                        f"{fixture}@{viewport['width']} {name} hides content with ellipsis",
                    )
                position = audit["boxes"]["position"]
                if audit["state"] in {"aiming", "error"}:
                    _assert(
                        not audit["positionHidden"]
                        and position is not None
                        and position["width"] >= 44
                        and position["height"] >= 16,
                        f"{fixture}@{viewport['width']} lacks visible aiming position",
                    )
                else:
                    _assert(
                        audit["positionHidden"]
                        or position is None
                        or position["width"] < 1
                        or position["height"] < 1,
                        f"{fixture}@{viewport['width']} keeps aiming instructions after preview",
                    )
                _assert(
                    not any(audit["overlaps"].values()),
                    f"{fixture}@{viewport['width']} mode-bar regions overlap: "
                    f"{audit['overlaps']}",
                )
                rows.append({
                    "fixture": fixture,
                    "viewport_css_pixels": dict(viewport),
                    "native_dpi_evidence": False,
                    "audit": audit,
                })
    finally:
        page.set_viewport_size({"width": 1366, "height": 768})
    return {"ok": True, "rows": rows, "native_dpi_evidence": False}


def _goto_real_workspace(page: Any, base_url: str) -> dict[str, Any]:
    page.goto(base_url)
    _wait_for_eval(page, "window.__MS_EVENT_STUDIO__?.getState?.().ready === true", timeout=20_000)
    _wait_for_eval(
        page,
        "window.__MS_EVENT_STUDIO__?.getState?.().workbench?.editState === 'selected'",
        timeout=20_000,
    )
    state = _hook_state(page)
    _assert(state.get("fixture") in {None, ""}, "real edit gate accidentally loaded a fixture")
    _assert(state.get("view") == "project", "real project did not open")
    return state["workbench"]


def _plot_point(page: Any, minute: float, *, y_fraction: float = 0.72) -> dict[str, float]:
    value = page.evaluate(
        """arg => {
          const content = document.querySelector('[data-qa="plot-content"]');
          const state = window.__MS_EVENT_STUDIO__?.getState?.().workbench;
          if (!content || !state?.viewport) return null;
          const rect = content.getBoundingClientRect();
          const start = Number(state.viewport.start_min);
          const end = Number(state.viewport.end_min);
          const fraction = (Number(arg.minute) - start) / (end - start);
          return {
            x: rect.left + Math.max(0, Math.min(1, fraction)) * rect.width,
            y: rect.top + Number(arg.yFraction) * rect.height,
            left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom,
          };
        }""",
        {"minute": minute, "yFraction": y_fraction},
    )
    _assert(isinstance(value, dict), "plot content geometry is unavailable")
    return {key: float(number) for key, number in value.items()}


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
            data_qa: active?.dataset?.qa || "",
            tag_name: active?.tagName?.toLowerCase() || "",
            focus_visible: Boolean(active?.matches?.(":focus-visible")),
            outline_style: style?.outlineStyle || "",
            outline_width_px: Number.parseFloat(style?.outlineWidth || "0") || 0,
            outline_color: style?.outlineColor || "",
          };
        }"""
    )
    _assert(evidence["data_qa"] == expected_qa, f"{label} focus moved to the wrong control")
    if require_visible:
        _assert(evidence["focus_visible"] is True, f"{label} focus is not :focus-visible")
        _assert(evidence["outline_style"] not in {"", "none"}, f"{label} focus has no outline")
        _assert(evidence["outline_width_px"] >= 2.0, f"{label} focus outline is too thin")
    return evidence


def _assert_committed_ui(
    page: Any,
    expected_origin_qa: str,
    label: str,
    *,
    keyboard: bool = False,
) -> dict[str, Any]:
    error = page.locator(_qa("edit-error"))
    _assert(error.count() == 1 and not error.is_visible(),
            f"{label} displayed an edit failure after a committed response")
    toast = page.locator("#toast")
    _assert(toast.count() == 1 and toast.get_attribute("data-tone") != "error",
            f"{label} displayed an error toast after a committed response")
    return _assert_focus(
        page,
        expected_origin_qa,
        f"{label} completion",
        require_visible=keyboard,
    )


def _begin_edit(
    page: Any,
    mode: str,
    requests: list[dict[str, Any]],
    *,
    keyboard: bool = False,
) -> dict[str, Any]:
    before = _request_count(requests, "aim")
    control = page.locator(_qa("add-event" if mode == "add" else "adjust-apex"))
    _assert(control.count() == 1 and control.is_enabled(), f"{mode} edit action is unavailable")
    if keyboard:
        control.focus()
        page.keyboard.press("Enter")
    else:
        control.click()
    _wait_edit_state(page, "aiming", mode)
    _wait_for_eval(
        page,
        "window.__MS_EVENT_STUDIO__?.getState?.().workbench?.editTokenPresent === true",
        timeout=5_000,
    )
    _assert(_request_count(requests, "aim") == before + 1, "edit aim did not issue one request")
    surface = _assert_edit_surface(page, expected_state="aiming", expected_mode=mode)
    if keyboard:
        surface["keyboard_focus"] = _assert_focus(
            page, "plot-svg", f"keyboard {mode} aim", require_visible=True
        )
    return surface


def _click_preview(
    page: Any,
    minute: float,
    mode: str,
    requests: list[dict[str, Any]],
) -> dict[str, Any]:
    before = _request_count(requests, "preview")
    point = _plot_point(page, minute)
    page.mouse.click(point["x"], point["y"])
    _wait_edit_state(page, "preview", mode)
    _assert(_request_count(requests, "preview") == before + 1,
            "plot-content click did not issue exactly one preview request")
    surface = _assert_edit_surface(page, expected_state="preview", expected_mode=mode)
    surface["preview_focus"] = _assert_focus(
        page, "edit-apply", f"{mode} preview", require_visible=False
    )
    return surface


def _cancel_edit(
    page: Any,
    requests: list[dict[str, Any]],
    scientific_snapshot: Callable[[], dict[str, Any]],
    *,
    escape: bool,
) -> dict[str, Any]:
    mode = _workbench_state(page).get("editMode")
    _assert(mode in {"add", "adjust"}, "cancel gate lacks an active edit mode")
    before_science = scientific_snapshot()
    before_cancel = _request_count(requests, "cancel")
    if escape:
        page.keyboard.press("Escape")
    else:
        page.locator(_qa("edit-cancel")).click()
    _wait_edit_state(page, "selected", None)
    _wait_for_eval(
        page,
        "window.__MS_EVENT_STUDIO__?.getState?.().workbench?.editTokenPresent === false",
        timeout=5_000,
    )
    _assert(_request_count(requests, "cancel") == before_cancel + 1,
            "edit cancel did not consume exactly one capability")
    after_science = scientific_snapshot()
    _assert(after_science == before_science, "cancel/Escape changed scientific state")
    focus = _assert_focus(
        page,
        "adjust-apex" if mode == "adjust" else "add-event",
        f"{mode} {'Escape' if escape else 'cancel'}",
        require_visible=escape,
    )
    return {
        "ok": True,
        "method": "Escape" if escape else "button",
        "scientific_writes": 0,
        "focus": focus,
    }


def _check_non_target_clicks(
    page: Any,
    requests: list[dict[str, Any]],
    scientific_snapshot: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    _begin_edit(page, "add", requests, keyboard=True)
    before_preview = _request_count(requests, "preview")
    before_science = scientific_snapshot()

    hover = _plot_point(page, 0.75)
    page.mouse.move(hover["x"], hover["y"])
    time.sleep(0.12)
    _assert(_request_count(requests, "preview") == before_preview,
            "mousemove issued a preview request")
    _assert(_workbench_state(page).get("editState") == "aiming",
            "mousemove left aim state")

    content = page.locator(_qa("plot-content")).bounding_box()
    _assert(content is not None, "plot content is missing")
    clicks: list[tuple[str, float, float]] = [
        ("plot-padding", content["x"] - 8.0, content["y"] + content["height"] / 2),
    ]
    for label, selector in (
        ("axis", ".plot-axis text"),
        ("legend", _qa("plot-legend")),
        ("label", _qa("plot-label")),
    ):
        box = page.locator(selector).first.bounding_box()
        _assert(box is not None, f"{label} has no geometry for hit testing")
        clicks.append((label, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2))

    checked: list[str] = ["mousemove"]
    for label, x, y in clicks:
        page.mouse.click(x, y)
        time.sleep(0.08)
        _assert(_request_count(requests, "preview") == before_preview,
                f"{label} issued an event-edit preview request")
        _assert(_workbench_state(page).get("editState") == "aiming",
                f"{label} left event-edit aim state")
        _assert(scientific_snapshot() == before_science, f"{label} changed scientific state")
        checked.append(label)

    cancel = _cancel_edit(page, requests, scientific_snapshot, escape=False)
    return {"ok": True, "targets": checked, "preview_requests": 0, "cancel": cancel}


def _check_cancel_and_escape(
    page: Any,
    requests: list[dict[str, Any]],
    scientific_snapshot: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    _begin_edit(page, "add", requests)
    _click_preview(page, 0.75, "add", requests)
    button = _cancel_edit(page, requests, scientific_snapshot, escape=False)
    _begin_edit(page, "add", requests, keyboard=True)
    escape = _cancel_edit(page, requests, scientific_snapshot, escape=True)
    return {"ok": True, "preview_cancel": button, "aim_escape": escape}


def _check_apply_failure_rollback(
    page: Any,
    requests: list[dict[str, Any]],
    scientific_snapshot: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    _begin_edit(page, "add", requests)
    _click_preview(page, 0.75, "add", requests)
    before_domain = _domain_state(page)
    before_science = scientific_snapshot()

    def fail_apply(route: Any) -> None:
        route.fulfill(
            status=500,
            content_type="application/json; charset=utf-8",
            body=json.dumps(
                {"error": {"code": "operation_failed", "message": "编辑未保存，请重试。"}},
                ensure_ascii=False,
            ),
        )

    page.route("**/api/event-edits/apply", fail_apply)
    page.locator(_qa("edit-apply")).click()
    _wait_edit_state(page, "error", "add")
    page.unroute("**/api/event-edits/apply", fail_apply)
    after_domain = _domain_state(page)
    after_science = scientific_snapshot()
    _assert(after_domain == before_domain, "failed apply did not restore exact workbench domain state")
    _assert(after_science == before_science, "failed apply changed persisted scientific state")
    error = page.locator(_qa("edit-error"))
    _assert(error.is_visible() and error.get_attribute("role") == "alert",
            "failed apply lacks an accessible edit alert")
    alert_text = error.inner_text().strip()
    _assert(bool(alert_text), "failed apply alert is empty")
    failure_focus = _assert_focus(
        page, "edit-cancel", "failed add apply", require_visible=False
    )
    cancelled = _cancel_edit(page, requests, scientific_snapshot, escape=False)
    return {
        "ok": True,
        "exact_domain_rollback": True,
        "scientific_writes": 0,
        "alert": alert_text,
        "focus": failure_focus,
        "cancel": cancelled,
    }


def _check_overlap_navigation(
    page: Any,
    requests: list[dict[str, Any]],
    responses: list[dict[str, Any]],
    scientific_snapshot: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    before = scientific_snapshot()
    before_apply = _request_count(requests, "apply")
    _begin_edit(page, "add", requests)
    _click_preview(page, 0.5, "add", requests)
    _assert(
        page.locator(_qa("edit-apply")).get_attribute("disabled") is None,
        "preview apply control is not keyboard operable",
    )
    page.keyboard.press("Enter")
    _wait_edit_state(page, "selected", None)
    _assert(_request_count(requests, "apply") == before_apply + 1,
            "overlap navigation did not issue one apply request")
    _wait_for_eval(
        page,
        "document.querySelector('#selectedApexTime')?.textContent?.includes('0.500')",
        timeout=5_000,
    )
    _assert(scientific_snapshot() == before, "navigate-existing outcome wrote scientific state")
    _assert(any(row.get("outcome") == "navigate_existing" for row in responses),
            "apply response did not report navigate_existing")
    focus = _assert_committed_ui(
        page, "add-event", "keyboard navigate-existing add", keyboard=True
    )
    return {
        "ok": True,
        "outcome": "navigate_existing",
        "scientific_writes": 0,
        "focus": focus,
    }


def _assert_saving_disabled(page: Any) -> dict[str, bool]:
    surface = page.locator(_qa("workbench"))
    _assert(surface.get_attribute("aria-busy") == "true", "saving edit lacks aria-busy=true")
    names = (
        "edit-apply",
        "edit-cancel",
        "add-event",
        "adjust-apex",
        "review-accept",
        "review-reject",
        "review-pending",
        "review-clear",
        "review-note",
        "undo",
        "redo",
        "previous-event",
        "next-event",
    )
    result: dict[str, bool] = {}
    for name in names:
        control = page.locator(_qa(name))
        _assert(control.count() == 1, f"saving control {name} is missing or duplicated")
        disabled = control.is_disabled() or control.get_attribute("aria-disabled") == "true"
        _assert(disabled, f"{name} remains operable while event edit is saving")
        result[name] = disabled
    _assert(all(row["disabled"] for row in _event_rows(page)),
            "event rows remain operable while event edit is saving")
    return result


def _check_successful_add_history_reopen(
    page: Any,
    base_url: str,
    requests: list[dict[str, Any]],
    responses: list[dict[str, Any]],
    scientific_snapshot: Callable[[], dict[str, Any]],
    reopen_project: Callable[[], Any],
) -> dict[str, Any]:
    initial = scientific_snapshot()
    initial_key = _workbench_state(page).get("selectedEventKey")
    _begin_edit(page, "add", requests)
    preview = _click_preview(page, 0.75, "add", requests)
    after_preview = scientific_snapshot()
    _assert(after_preview == initial, "add preview changed scientific state before apply")
    _assert(abs(float(preview["candidate_time_min"]) - 0.75) < 1e-9,
            "add preview did not snap to the real 0.75 min peak")
    before_apply = _request_count(requests, "apply")

    held: list[Any] = []

    def hold_apply(route: Any) -> None:
        held.append(route)

    page.route("**/api/event-edits/apply", hold_apply)
    page.locator(_qa("edit-apply")).click()
    _wait_edit_state(page, "saving", "add")
    _assert(held, "add apply request was not held for saving-state inspection")
    disabled = _assert_saving_disabled(page)
    held[0].continue_()
    _wait_edit_state(page, "selected", None)
    page.unroute("**/api/event-edits/apply", hold_apply)
    _assert(_request_count(requests, "apply") == before_apply + 1,
            "add action did not issue exactly one apply request")
    _wait_for_eval(
        page,
        "arg => { const workbench = window.__MS_EVENT_STUDIO__?.getState?.().workbench; "
        "return workbench?.eventCount === arg && workbench?.source === 'manual_added' "
        "&& workbench?.status === 'accepted'; }",
        arg=initial["event_count"] + 1,
        timeout=5_000,
    )
    added = scientific_snapshot()
    _assert(added["event_count"] == initial["event_count"] + 1, "add apply did not add one event")
    _assert(added["audit_count"] == initial["audit_count"] + 1,
            "add apply did not append exactly one audit action")
    _assert(added["audit_actions"][-1] == "add_event", "add apply wrote the wrong action")
    _assert(any(row.get("outcome") == "applied" for row in responses),
            "successful add response did not report applied")
    committed_focus = _assert_committed_ui(page, "add-event", "successful add")

    page.locator(_qa("undo")).click()
    _wait_for_eval(
        page,
        "arg => window.__MS_EVENT_STUDIO__?.getState?.().workbench?.eventCount === arg",
        arg=initial["event_count"],
        timeout=5_000,
    )
    undone = scientific_snapshot()
    _assert(undone["event_count"] == initial["event_count"], "undo did not remove added event")
    page.locator(_qa("redo")).click()
    _wait_for_eval(
        page,
        "arg => { const workbench = window.__MS_EVENT_STUDIO__?.getState?.().workbench; "
        "return workbench?.eventCount === arg && workbench?.source === 'manual_added'; }",
        arg=initial["event_count"] + 1,
        timeout=5_000,
    )
    redone = scientific_snapshot()
    _assert(redone["event_count"] == initial["event_count"] + 1,
            "redo did not restore added event")

    before_reopen_key = _workbench_state(page).get("selectedEventKey")
    reopen_project()
    reopened_hook = _goto_real_workspace(page, base_url)
    reopened = scientific_snapshot()
    _assert(reopened == redone, "added event changed after project reopen")
    _assert(reopened_hook.get("selectedEventKey") not in {initial_key, before_reopen_key},
            "project reopen reused the old opaque event key space")
    return {
        "ok": True,
        "candidate_time_min": preview["candidate_time_min"],
        "preview_scientific_writes": 0,
        "apply_requests": 1,
        "committed_ui": {
            "no_false_failure": True,
            "selected_source": "manual_added",
            "selected_status": "accepted",
            "focus": committed_focus,
        },
        "saving_disabled": disabled,
        "event_counts": {
            "initial": initial["event_count"],
            "added": added["event_count"],
            "undone": undone["event_count"],
            "redone": redone["event_count"],
            "reopened": reopened["event_count"],
        },
        "persistent": True,
    }


def _select_event_index(page: Any, index: int) -> dict[str, Any]:
    rows = page.locator(_qa("event-row"))
    _assert(0 <= index < rows.count(), f"event row {index} is unavailable")
    key = rows.nth(index).get_attribute("data-event-key")
    rows.nth(index).click()
    _wait_for_eval(
        page,
        "arg => window.__MS_EVENT_STUDIO__?.getState?.().workbench?.selectedEventKey === arg",
        arg=key,
        timeout=5_000,
    )
    return _workbench_state(page)


def _check_adjust_outside_apply_history_reopen(
    page: Any,
    base_url: str,
    requests: list[dict[str, Any]],
    scientific_snapshot: Callable[[], dict[str, Any]],
    reopen_project: Callable[[], Any],
) -> dict[str, Any]:
    selected = _select_event_index(page, 0)
    _assert(selected.get("status") == "accepted" and selected.get("source") == "automatic",
            "adjust gate did not select the accepted automatic event")
    initial = scientific_snapshot()
    _begin_edit(page, "adjust", requests)
    aim = _workbench_state(page)
    allowed = aim.get("allowedInterval")
    _assert(allowed["startMin"] < 0.5 < allowed["endMin"],
            "adjust allowed interval does not contain the original apex")

    before_preview = _request_count(requests, "preview")
    outside = _plot_point(page, 0.75)
    page.mouse.click(outside["x"], outside["y"])
    time.sleep(0.12)
    _assert(_workbench_state(page).get("editState") == "aiming",
            "out-of-range click left adjust aim state")
    _assert(_request_count(requests, "preview") == before_preview,
            "out-of-range plot click issued a preview request")
    _assert(scientific_snapshot() == initial, "out-of-range preview changed scientific state")
    error = page.locator(_qa("edit-error"))
    _assert(not error.is_visible(), "suppressed out-of-range click displayed a stale error")

    preview = _click_preview(page, 31.0 / 60.0, "adjust", requests)
    after_preview = scientific_snapshot()
    _assert(after_preview == initial, "adjust preview changed scientific state before apply")
    _assert(abs(float(preview["candidate_time_min"]) - 31.0 / 60.0) < 1e-9,
            "adjust preview did not snap to scan 310")
    before_apply = _request_count(requests, "apply")
    page.locator(_qa("edit-apply")).click()
    _wait_edit_state(page, "selected", None)
    _assert(_request_count(requests, "apply") == before_apply + 1,
            "adjust action did not issue exactly one apply request")
    _wait_for_eval(
        page,
        "() => { const workbench = window.__MS_EVENT_STUDIO__?.getState?.().workbench; "
        "return workbench?.source === 'manual_adjusted' && workbench?.status === 'accepted'; }",
        timeout=5_000,
    )
    adjusted = scientific_snapshot()
    _assert(adjusted["event_count"] == initial["event_count"],
            "adjust apply changed event count")
    _assert(adjusted["audit_count"] == initial["audit_count"] + 1,
            "adjust apply did not append exactly one audit action")
    _assert(adjusted["audit_actions"][-1] == "adjust_apex", "adjust wrote the wrong action")
    committed_focus = _assert_committed_ui(page, "adjust-apex", "successful adjust")

    page.locator(_qa("undo")).click()
    _wait_for_eval(
        page,
        "window.__MS_EVENT_STUDIO__?.getState?.().workbench?.source === 'automatic'",
        timeout=5_000,
    )
    _assert(_workbench_state(page).get("status") == "accepted",
            "undo adjustment changed review status")
    page.locator(_qa("redo")).click()
    _wait_for_eval(
        page,
        "window.__MS_EVENT_STUDIO__?.getState?.().workbench?.source === 'manual_adjusted'",
        timeout=5_000,
    )
    _assert(_workbench_state(page).get("status") == "accepted",
            "redo adjustment changed review status")
    redone = scientific_snapshot()
    reopen_project()
    _goto_real_workspace(page, base_url)
    reopened = scientific_snapshot()
    _assert(reopened == redone, "adjusted apex changed after project reopen")
    _assert(any(
        event["origin"] == "manual_adjusted"
        and abs(event["apex_time_sec"] - 31.0) < 1e-8
        and event["status"] == "accepted"
        for event in reopened["events"]
    ), "reopened project lacks the status-preserving adjusted apex")
    return {
        "ok": True,
        "outside_preview_requests": 0,
        "outside_scientific_writes": 0,
        "candidate_time_min": preview["candidate_time_min"],
        "preview_scientific_writes": 0,
        "apply_requests": 1,
        "committed_focus": committed_focus,
        "status_preserved": True,
        "undo_redo_reopen": True,
    }


def _check_real_flow(
    page: Any,
    base_url: str,
    requests: list[dict[str, Any]],
    responses: list[dict[str, Any]],
    scientific_snapshot: Callable[[], dict[str, Any]],
    reopen_project: Callable[[], Any],
) -> dict[str, Any]:
    initial = _goto_real_workspace(page, base_url)
    _assert(initial.get("editState") == "selected", "real workbench did not start selected")
    _assert_no_internal_dom_fields(page)
    return {
        "non_target_zero_preview": _check_non_target_clicks(
            page, requests, scientific_snapshot
        ),
        "cancel_and_escape_zero_write": _check_cancel_and_escape(
            page, requests, scientific_snapshot
        ),
        "apply_failure_rollback": _check_apply_failure_rollback(
            page, requests, scientific_snapshot
        ),
        "overlap_navigation": _check_overlap_navigation(
            page, requests, responses, scientific_snapshot
        ),
        "add_apply_history_reopen": _check_successful_add_history_reopen(
            page,
            base_url,
            requests,
            responses,
            scientific_snapshot,
            reopen_project,
        ),
        "adjust_outside_apply_history_reopen": _check_adjust_outside_apply_history_reopen(
            page,
            base_url,
            requests,
            scientific_snapshot,
            reopen_project,
        ),
    }


def run_gate(
    *,
    base_url: str,
    fixtures_only: bool,
    headed: bool,
    scientific_snapshot: Callable[[], dict[str, Any]] | None = None,
    reopen_project: Callable[[], Any] | None = None,
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
                    requests.append({"method": request.method.upper(), "path": path})

            def record_response(response: Any) -> None:
                path = urlparse(response.url).path
                if path not in set(EDIT_ENDPOINTS.values()):
                    return
                row: dict[str, Any] = {"path": path, "status": response.status}
                try:
                    payload = response.json()
                    if isinstance(payload, dict):
                        if isinstance(payload.get("outcome"), str):
                            row["outcome"] = payload["outcome"]
                        if isinstance(payload.get("error"), dict):
                            row["error_code"] = payload["error"].get("code")
                except BaseException:
                    pass
                responses.append(row)

            page.on("request", record_request)
            page.on("response", record_response)
            page.on(
                "console",
                lambda message: console_errors.append(message.text)
                if message.type == "error" else None,
            )
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            checks["fixture_states"] = _check_fixture_states(page, base_url, requests)
            checks["compact_critical_fact_visibility"] = _check_compact_fact_visibility(
                page, base_url
            )
            checks["standard_fixture_reflow"] = _check_standard_fixture_reflow(
                page, base_url
            )
            fixture_requests = list(requests)
            _assert(fixture_requests == [], f"R4 fixtures made requests: {fixture_requests}")
            checks["fixture_zero_requests"] = {"ok": True, "requests": fixture_requests}

            if not fixtures_only:
                _assert(scientific_snapshot is not None, "real R4 gate lacks scientific snapshot")
                _assert(reopen_project is not None, "real R4 gate lacks reopen callback")
                checks["real_event_edit_flow"] = _check_real_flow(
                    page,
                    base_url,
                    requests,
                    responses,
                    scientific_snapshot,
                    reopen_project,
                )
                checks["real_api_traffic"] = {
                    "ok": True,
                    "requests": requests,
                    "responses": responses,
                    "counts": {
                        kind: _request_count(requests, kind) for kind in EDIT_ENDPOINTS
                    },
                }
            unexpected_console_errors = [
                message for message in console_errors
                if not message.startswith("Failed to load resource:")
            ]
            _assert(not page_errors, f"R4 browser page errors: {page_errors}")
            _assert(
                not unexpected_console_errors,
                f"R4 application console errors: {unexpected_console_errors}",
            )
            checks["browser_runtime_errors"] = {
                "ok": True,
                "page_errors": page_errors,
                "application_console_errors": unexpected_console_errors,
                "expected_http_failure_console_messages": [
                    message for message in console_errors
                    if message.startswith("Failed to load resource:")
                ],
            }
        finally:
            context.close()
            browser.close()
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", help="already-running server; valid with --fixtures-only")
    parser.add_argument("--fixtures-only", action="store_true")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        default=REPOSITORY / "build/qa/ux-r4-event-edit.json",
    )
    args = parser.parse_args(argv)

    report: dict[str, Any] = {
        "schema": "ms-event-studio-ux-r4-event-edit-qa-v1",
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
            raise ValueError("real R4 QA requires its internally managed project")
        base_url = args.base_url
        snapshot: Callable[[], dict[str, Any]] | None = None
        reopen: Callable[[], Any] | None = None
        if not base_url:
            from ms_event_studio.web_app import WebSession, create_http_server

            temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
            root = Path(temporary.name)
            session = None
            if args.fixtures_only:
                server = create_http_server(recent_path=root / "recent.json")
            else:
                source, project = _create_r4_scientific_project(root)
                source_before = _file_fingerprint(source)
                session = WebSession(root / "recent.json")
                registered = session.register_path("project_open", project.project_dir)
                project_token = registered["selection_token"]
                session.open_project(project_token)
                server = create_http_server(session=session)
                snapshot = lambda: _scientific_snapshot(project.project_dir)
                reopen = lambda: session.open_project(project_token)
            server.start()
            base_url = server.url

        report["server_mode"] = "external" if args.base_url else "ephemeral-loopback"
        report["checks"] = run_gate(
            base_url=base_url,
            fixtures_only=args.fixtures_only,
            headed=args.headed,
            scientific_snapshot=snapshot,
            reopen_project=reopen,
        )
        if source is not None and source_before is not None:
            source_after = _file_fingerprint(source)
            _assert(source_after == source_before, "R4 gate changed its raw scientific source")
            report["checks"]["real_source_read_only"] = {
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
