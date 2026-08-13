"""Real-browser regression gate for narrow automatic event supports in UX-R4.

The disposable project deliberately has a sub-pixel automatic support in the
normal two-minute view, while retaining a second real local maximum inside that
immutable support.  Adjustment must zoom to the local morphology, keep the
canonical scientific interval unchanged, and support both pointer and keyboard
selection directly on the plot.

Browser viewport sizes are CSS-pixel evidence only.  This script does not claim
native DPI coverage.
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
    from run_ux_r2_r3_workbench_qa import (
        _assert,
        _file_fingerprint,
        _qa,
        _wait_for_eval,
        _workbench_state,
    )
    from run_ux_r4_event_edit_qa import (
        EDIT_ENDPOINTS,
        _assert_committed_ui,
        _assert_edit_surface,
        _assert_focus,
        _assert_no_internal_dom_fields,
        _goto_real_workspace,
        _request_count,
        _scientific_snapshot,
        _select_event_index,
        _wait_edit_state,
    )
except ModuleNotFoundError:  # Imported as ``scripts.run_ux_r4_narrow_support_qa``.
    from scripts.run_ux_r2_r3_workbench_qa import (
        _assert,
        _file_fingerprint,
        _qa,
        _wait_for_eval,
        _workbench_state,
    )
    from scripts.run_ux_r4_event_edit_qa import (
        EDIT_ENDPOINTS,
        _assert_committed_ui,
        _assert_edit_surface,
        _assert_focus,
        _assert_no_internal_dom_fields,
        _goto_real_workspace,
        _request_count,
        _scientific_snapshot,
        _select_event_index,
        _wait_edit_state,
    )


VIEWPORTS = (
    {"width": 960, "height": 640},
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1920, "height": 1080},
)
ORIGINAL_APEX_SEC = 30.0
KEYBOARD_CANDIDATE_SEC = 30.01
MAX_CANONICAL_SUPPORT_SEC = 0.12
MIN_POINTER_HIT_CSS_PX = 12.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _scan_time_sec(index: int) -> float:
    """Compress a broad peak into a physically narrow, monotonic time region."""

    if index < 250:
        return 29.9 * index / 249.0
    if index <= 350:
        return 29.95 + (index - 250) * 0.001
    return 30.1 + (index - 351) * (89.9 / 849.0)


def _create_narrow_support_project(root: Path) -> tuple[Path, Any]:
    """Create three automatic events and a real in-support adjustment peak."""

    from ms_event_studio.project import CreateProjectRequest, create_project
    from ms_event_studio.window_service import ProjectWindowService

    scan_count = 1201
    pc34_mz = 760.5851
    qc782_mz = 782.5616
    signal = [
        1000.0 * math.exp(-0.5 * ((index - 300.0) / 30.0) ** 2)
        for index in range(scan_count)
    ]
    # The tiny shoulder is a genuine local maximum, but remains part of the
    # same broad automatic peak.  Irregular scan times compress both maxima
    # into a narrow physical support without changing detector/snap code.
    signal[309] -= 10.0
    signal[310] += 10.0
    signal[311] -= 10.0
    signal[600] = 1200.0
    signal[900] = 1100.0

    source = root / "r4-narrow-support-source.txt"
    with source.open("w", encoding="ascii", newline="\n") as handle:
        handle.write(f"spectrumList ({scan_count} spectra)\n")
        for index, intensity in enumerate(signal):
            values = (0.0, intensity, 10.0, 0.0)
            time_min = _scan_time_sec(index) / 60.0
            lines = (
                "spectrum:",
                f"  index: {index}",
                f"  id: scanId={200000 + index}",
                "  defaultArrayLength: 4",
                f"  cvParam: base peak m/z, {pc34_mz}",
                f"  cvParam: base peak intensity, {max(values):.15g}",
                "  cvParam: total ion current, 10000000, number of detector counts",
                "  cvParam: lowest observed m/z, 100",
                "  cvParam: highest observed m/z, 900",
                f"  cvParam: scan start time, {time_min:.15f}, minute",
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
            project_dir=root / "r4-narrow-support-project",
            display_name="窄峰交互回归项目",
            analysis_start_min="0",
            analysis_end_min="2",
        )
    )
    with ProjectWindowService.open(project.project_dir) as service:
        events = service.all_events()
        if len(events) != 3:
            raise AssertionError(
                f"narrow-support scientific fixture detected {len(events)} events, expected 3"
            )
        first = min(
            events,
            key=lambda row: abs(float(row["current_apex_time_sec"]) - ORIGINAL_APEX_SEC),
        )
        if abs(float(first["current_apex_time_sec"]) - ORIGINAL_APEX_SEC) > 1e-9:
            raise AssertionError("narrow-support fixture did not detect its primary peak")
        support_sec = float(first["original_right_sec"]) - float(first["original_left_sec"])
        if not (0.0 < support_sec <= MAX_CANONICAL_SUPPORT_SEC):
            raise AssertionError(
                f"automatic support is not narrow enough for the regression: {support_sec:.9g}s"
            )
        if not (
            float(first["original_left_sec"])
            <= KEYBOARD_CANDIDATE_SEC
            <= float(first["original_right_sec"])
        ):
            raise AssertionError("real secondary peak is outside the immutable automatic support")
        accepted = service.review_store.set_status(
            first["event_id"],
            "accepted",
            expected_revision=int(first["revision"]),
            actor="qa-fixture",
            session_id="qa-fixture",
            reason="prepare status-preserving narrow-support adjustment",
        )
        if accepted.get("status") != "accepted":
            raise AssertionError("could not prepare the narrow-support automatic event")
    return source, project


def _support_evidence(project_dir: Path) -> dict[str, float | str]:
    from ms_event_studio.window_service import ProjectWindowService

    with ProjectWindowService.open(project_dir) as service:
        first = min(
            service.all_events(),
            key=lambda row: abs(float(row["current_apex_time_sec"]) - ORIGINAL_APEX_SEC),
        )
        candidate = service.scans.iloc[310]
        return {
            "apex_time_sec": float(first["current_apex_time_sec"]),
            "support_start_sec": float(first["original_left_sec"]),
            "support_end_sec": float(first["original_right_sec"]),
            "support_width_sec": (
                float(first["original_right_sec"]) - float(first["original_left_sec"])
            ),
            "candidate_time_sec": float(candidate["scan_start_time_sec"]),
            "candidate_intensity": float(candidate["pc34_760_max_intensity"]),
            "status": str(first["status"]),
            "origin": str(first["origin"]),
        }


def _select_narrow_event(page: Any) -> dict[str, Any]:
    rows = page.locator(_qa("event-row"))
    _assert(rows.count() >= 3, "narrow-support project did not expose its automatic events")
    candidates: list[tuple[float, int]] = []
    for index in range(rows.count()):
        text = rows.nth(index).inner_text()
        # The first automatic event is chronological and intentionally near
        # 0.500 min; retaining this text check makes accidental ordering drift
        # visible without exposing any internal event identity.
        score = 0.0 if "0.500" in text else float(index + 1)
        candidates.append((score, index))
    target_index = min(candidates)[1]
    _select_event_index(page, target_index)
    # Selection is an async workspace/window request.  selectedEventKey may be
    # updated before the normalized selection evidence is rendered, so wait on
    # the public semantic state rather than treating the opaque key as a commit
    # barrier.
    _wait_for_eval(
        page,
        "arg => { const w = window.__MS_EVENT_STUDIO__?.getState?.().workbench; "
        "return w?.eventIndex === arg && w?.source === 'automatic' "
        "&& w?.status === 'accepted'; }",
        arg=target_index,
        timeout=5_000,
    )
    selected = _workbench_state(page)
    _assert(
        selected.get("source") == "automatic" and selected.get("status") == "accepted",
        "narrow-support gate did not select the accepted automatic event",
    )
    return selected


def _begin_adjust(page: Any, requests: list[dict[str, str]], *, keyboard: bool) -> dict[str, Any]:
    before = _request_count(requests, "aim")
    control = page.locator(_qa("adjust-apex"))
    _assert(control.count() == 1 and control.is_enabled(), "adjust action is unavailable")
    if keyboard:
        control.focus()
        page.keyboard.press("Enter")
    else:
        control.click()
    _wait_edit_state(page, "aiming", "adjust")
    _wait_for_eval(
        page,
        "window.__MS_EVENT_STUDIO__?.getState?.().workbench?.editTokenPresent === true",
        timeout=5_000,
    )
    _assert(_request_count(requests, "aim") == before + 1, "adjust aim was not issued once")
    surface = _assert_edit_surface(page, expected_state="aiming", expected_mode="adjust")
    if keyboard:
        surface["focus"] = _assert_focus(
            page,
            "plot-svg",
            "keyboard narrow-support adjust",
            require_visible=True,
        )
    return surface


def _range_contract(page: Any, allowed: dict[str, float]) -> dict[str, Any]:
    evidence = page.evaluate(
        """() => {
          const plot = document.querySelector('[data-qa="plot-svg"]');
          const readout = document.querySelector('[data-qa="edit-position-readout"]');
          const box = readout?.getBoundingClientRect?.();
          return {
            role: plot?.getAttribute('role') || '',
            tabindex: plot?.getAttribute('tabindex') || '',
            ariaLabel: plot?.getAttribute('aria-label') || '',
            readout: (readout?.textContent || '').trim(),
            readoutLive: readout?.getAttribute('aria-live') || '',
            visible: Boolean(box?.width && box?.height),
            focused: document.activeElement === plot,
          };
        }"""
    )
    hook = _workbench_state(page)
    keyboard_time = hook.get("editKeyboardTimeMin")
    start = float(allowed["startMin"])
    end = float(allowed["endMin"])
    step = max((end - start) / 100.0, math.ulp(max(1.0, abs(start), abs(end))) * 8.0)
    _assert(evidence["role"] == "group", "aiming plot lacks interactive group semantics")
    _assert(evidence["tabindex"] == "0", "aiming plot is not keyboard focusable")
    _assert(evidence["visible"], "keyboard aim readout is unavailable")
    _assert(evidence["readoutLive"] == "polite", "keyboard aim readout is not announced")
    _assert("左右方向键" in evidence["ariaLabel"] and "Enter" in evidence["ariaLabel"],
            "plot does not expose keyboard aiming instructions")
    _assert(isinstance(keyboard_time, (int, float)), "hook lacks the keyboard aim time")
    _assert(start <= float(keyboard_time) <= end,
            "keyboard aim is outside its canonical interval")
    _assert(evidence["readout"] == f"{float(keyboard_time):.6f} min",
            "visible aim readout and read-only hook disagree")
    return {
        **evidence,
        "min": start,
        "max": end,
        "step": step,
        "value": float(keyboard_time),
        "hook_value": keyboard_time,
    }


def _edit_geometry(page: Any, allowed: dict[str, float]) -> dict[str, Any]:
    result = page.evaluate(
        """() => {
          const box = selector => {
            const node = document.querySelector(selector);
            const rect = node?.getBoundingClientRect?.();
            return node && rect ? {
              left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom,
              width: rect.width, height: rect.height, hidden: node.hasAttribute('hidden'),
            } : null;
          };
          return {
            content: box('[data-qa="plot-content"]'),
            canonical: box('[data-qa="edit-allowed-range"]'),
            hit: box('[data-qa="edit-allowed-hit"]'),
          };
        }"""
    )
    content = result["content"]
    canonical = result["canonical"]
    hit = result["hit"]
    _assert(content and canonical and hit, "narrow edit geometry is incomplete")
    _assert(not canonical["hidden"] and not hit["hidden"], "narrow edit geometry is hidden")
    viewport = _workbench_state(page).get("viewport") or {}
    viewport_span = float(viewport.get("end_min", 0)) - float(viewport.get("start_min", 0))
    _assert(0.0 < viewport_span <= 0.020000000001,
            f"adjustment did not focus the local morphology: {viewport}")
    _assert(float(viewport["start_min"]) <= float(allowed["startMin"])
            and float(viewport["end_min"]) >= float(allowed["endMin"]),
            "focused viewport does not contain the canonical support")
    _assert(canonical["width"] >= MIN_POINTER_HIT_CSS_PX,
            f"focused canonical support remains too small to inspect: {canonical['width']}")
    _assert(
        hit["width"] >= MIN_POINTER_HIT_CSS_PX,
        f"transparent edit hit target is narrower than {MIN_POINTER_HIT_CSS_PX}px",
    )
    _assert(
        hit["width"] >= canonical["width"] - 1.0,
        "pointer target is narrower than the visible scientific interval",
    )
    canonical_center = (canonical["left"] + canonical["right"]) / 2.0
    hit_center = (hit["left"] + hit["right"]) / 2.0
    _assert(abs(canonical_center - hit_center) <= 1.0, "expanded hit is not centered on support")
    _assert(
        hit["left"] >= content["left"] - 0.5
        and hit["right"] <= content["right"] + 0.5
        and hit["top"] >= content["top"] - 0.5
        and hit["bottom"] <= content["bottom"] + 0.5,
        "expanded hit target escapes the clipped plot content",
    )
    ratio = (
        (KEYBOARD_CANDIDATE_SEC / 60.0 - float(allowed["startMin"]))
        / (float(allowed["endMin"]) - float(allowed["startMin"]))
    )
    _assert(0.0 <= ratio <= 1.0, "real candidate is outside the browser allowed interval")
    trace_segments = page.locator("#traceLayer .trace-line").get_attribute("d") or ""
    _assert(trace_segments.count("L") >= 8, "focused view does not show enough real curve samples")
    return {
        **result,
        "candidate_ratio": ratio,
        "viewport": viewport,
        "viewport_span_min": viewport_span,
        "trace_segments": trace_segments.count("L") + 1,
    }


def _cancel_active_edit(
    page: Any,
    requests: list[dict[str, str]],
    snapshot: Callable[[], dict[str, Any]],
    *,
    keyboard: bool,
) -> dict[str, Any]:
    before_science = snapshot()
    before = _request_count(requests, "cancel")
    if keyboard:
        page.keyboard.press("Escape")
    else:
        page.locator(_qa("edit-cancel")).click()
    _wait_edit_state(page, "selected", None)
    _assert(_request_count(requests, "cancel") == before + 1, "edit cancel was not issued once")
    _assert(snapshot() == before_science, "cancel changed scientific state")
    restored = _workbench_state(page).get("viewport") or {}
    _assert(math.isclose(float(restored.get("start_min", -1)), 0.0, abs_tol=1e-12)
            and math.isclose(float(restored.get("end_min", -1)), 2.0, abs_tol=1e-12),
            f"cancel did not restore the user's original viewport: {restored}")
    focus = _assert_focus(
        page,
        "adjust-apex",
        "keyboard Escape" if keyboard else "pointer cancel",
        require_visible=keyboard,
    )
    return {"ok": True, "focus": focus, "scientific_writes": 0}


def _mouse_candidate(
    page: Any,
    requests: list[dict[str, str]],
    snapshot: Callable[[], dict[str, Any]],
    viewport: dict[str, int],
) -> dict[str, Any]:
    before_science = snapshot()
    surface = _begin_adjust(page, requests, keyboard=False)
    allowed = surface["allowed_interval"]
    range_evidence = _range_contract(page, allowed)
    geometry = _edit_geometry(page, allowed)
    before_preview = _request_count(requests, "preview")
    hit = geometry["hit"]
    page.mouse.click(
        hit["left"] + geometry["candidate_ratio"] * hit["width"],
        hit["top"] + hit["height"] * 0.72,
    )
    _wait_edit_state(page, "preview", "adjust")
    _assert(
        _request_count(requests, "preview") == before_preview + 1,
        "mapped narrow hit did not issue exactly one preview",
    )
    candidate = _workbench_state(page).get("candidateTimeMin")
    _assert(
        isinstance(candidate, (int, float))
        and math.isclose(float(candidate), KEYBOARD_CANDIDATE_SEC / 60.0, abs_tol=1e-12),
        "mapped narrow hit did not snap to the intended real peak",
    )
    _assert(snapshot() == before_science, "mouse preview changed scientific state")
    focus = _assert_focus(page, "edit-apply", "mouse narrow preview", require_visible=False)
    cancel = _cancel_active_edit(page, requests, snapshot, keyboard=False)
    return {
        "ok": True,
        "viewport_css_pixels": viewport,
        "native_dpi_evidence": False,
        "canonical_support_css_px": geometry["canonical"]["width"],
        "pointer_hit_css_px": geometry["hit"]["width"],
        "focused_viewport": geometry["viewport"],
        "trace_segments": geometry["trace_segments"],
        "candidate_time_min": candidate,
        "range": range_evidence,
        "preview_focus": focus,
        "cancel": cancel,
    }


def _non_target_zero_preview(
    page: Any,
    requests: list[dict[str, str]],
    snapshot: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    before_science = snapshot()
    surface = _begin_adjust(page, requests, keyboard=False)
    geometry = _edit_geometry(page, surface["allowed_interval"])
    before = _request_count(requests, "preview")
    content = geometry["content"]
    hit = geometry["hit"]
    if hit["left"] - content["left"] >= content["right"] - hit["right"]:
        outside_hit = (hit["left"] - 5.0, content["top"] + 0.84 * content["height"])
    else:
        outside_hit = (hit["right"] + 5.0, content["top"] + 0.84 * content["height"])
    _assert(
        content["left"] < outside_hit[0] < content["right"],
        "could not place a non-target point inside plot content",
    )
    page.mouse.click(*outside_hit)
    time.sleep(0.08)

    axis = page.locator(".plot-axis text").first.bounding_box()
    legend = page.locator(_qa("plot-legend")).bounding_box()
    label = page.locator(_qa("plot-label")).first.bounding_box()
    _assert(axis and legend and label, "axis/legend/label geometry is incomplete")
    targets = {
        "expanded-hit-exterior": outside_hit,
        "axis": (axis["x"] + axis["width"] / 2.0, axis["y"] + axis["height"] / 2.0),
        "legend": (
            legend["x"] + legend["width"] / 2.0,
            legend["y"] + legend["height"] / 2.0,
        ),
        "label": (label["x"] + label["width"] / 2.0, label["y"] + label["height"] / 2.0),
    }
    # The exterior point was already exercised above.
    for label_name in ("axis", "legend", "label"):
        page.mouse.click(*targets[label_name])
        time.sleep(0.08)
    _assert(
        _request_count(requests, "preview") == before,
        "non-target curve area, axis, legend, or label issued a preview request",
    )
    _assert(_workbench_state(page).get("editState") == "aiming", "non-target left aim state")
    _assert(snapshot() == before_science, "non-target pointer input changed scientific state")
    cancel = _cancel_active_edit(page, requests, snapshot, keyboard=True)
    return {"ok": True, "targets": list(targets), "preview_requests": 0, "cancel": cancel}


def _keyboard_candidate_apply(
    page: Any,
    base_url: str,
    requests: list[dict[str, str]],
    snapshot: Callable[[], dict[str, Any]],
    reopen: Callable[[], Any],
) -> dict[str, Any]:
    initial = snapshot()
    # First prove the complete keyboard entry/Escape path while the event is
    # still automatic.  The subsequent keyboard-only pass applies the change.
    surface = _begin_adjust(page, requests, keyboard=True)
    allowed = surface["allowed_interval"]
    range_initial = _range_contract(page, allowed)
    _assert(
        math.isclose(range_initial["value"], ORIGINAL_APEX_SEC / 60.0, abs_tol=1e-12),
        "adjust keyboard default is not the current apex",
    )
    escape = _cancel_active_edit(page, requests, snapshot, keyboard=True)
    _select_narrow_event(page)
    surface = _begin_adjust(page, requests, keyboard=True)
    allowed = surface["allowed_interval"]
    range_initial = _range_contract(page, allowed)
    before_preview = _request_count(requests, "preview")
    control = page.locator(_qa("plot-svg"))
    step = float(range_initial["step"])
    count = round((KEYBOARD_CANDIDATE_SEC / 60.0 - range_initial["value"]) / step)
    _assert(0 < count <= 2000, f"keyboard candidate requires unreasonable steps: {count}")
    for _ in range(count):
        page.keyboard.press("ArrowRight")
    moved = _range_contract(page, allowed)
    _assert(
        abs(float(moved["value"]) - KEYBOARD_CANDIDATE_SEC / 60.0) <= step / 2.0 + 1e-12,
        "keyboard plot aiming cannot reach the real in-support candidate",
    )
    _assert(
        _request_count(requests, "preview") == before_preview,
        "arrow-key aiming issued a preview before Enter",
    )
    control.press("Enter")
    _wait_edit_state(page, "preview", "adjust")
    _assert(
        _request_count(requests, "preview") == before_preview + 1,
        "keyboard Enter did not issue exactly one preview",
    )
    candidate = _workbench_state(page).get("candidateTimeMin")
    _assert(
        isinstance(candidate, (int, float))
        and math.isclose(float(candidate), KEYBOARD_CANDIDATE_SEC / 60.0, abs_tol=1e-12),
        "keyboard preview did not snap to the real in-support peak",
    )
    _assert(snapshot() == initial, "keyboard preview changed scientific state")
    preview_focus = _assert_focus(
        page, "edit-apply", "keyboard narrow preview", require_visible=True
    )
    before_apply = _request_count(requests, "apply")
    page.keyboard.press("Enter")
    _wait_edit_state(page, "selected", None)
    _assert(_request_count(requests, "apply") == before_apply + 1, "keyboard apply was not issued once")
    _wait_for_eval(
        page,
        "() => { const w = window.__MS_EVENT_STUDIO__?.getState?.().workbench; "
        "return w?.source === 'manual_adjusted' && w?.status === 'accepted'; }",
        timeout=5_000,
    )
    committed = snapshot()
    _assert(committed["event_count"] == initial["event_count"], "keyboard adjust changed event count")
    _assert(committed["audit_count"] == initial["audit_count"] + 1, "keyboard apply lacks one audit")
    _assert(committed["audit_actions"][-1] == "adjust_apex", "keyboard apply wrote wrong action")
    completion_focus = _assert_committed_ui(
        page, "adjust-apex", "keyboard narrow-support apply", keyboard=True
    )
    # A real reopen creates a fresh opaque capability space and must retain the
    # scientific result; project paths remain outside browser evidence.
    reopen()
    _goto_real_workspace(page, base_url)
    reopened = snapshot()
    _assert(reopened == committed, "keyboard-adjusted apex changed after reopen")
    _assert(any(
        row["origin"] == "manual_adjusted"
        and row["status"] == "accepted"
        and math.isclose(row["apex_time_sec"], KEYBOARD_CANDIDATE_SEC, abs_tol=1e-9)
        for row in reopened["events"]
    ), "reopened project lacks the status-preserving narrow adjustment")
    return {
        "ok": True,
        "arrow_presses": count,
        "range_initial": range_initial,
        "range_candidate": moved,
        "candidate_time_min": candidate,
        "preview_focus": preview_focus,
        "completion_focus": completion_focus,
        "status_preserved": True,
        "reopen_persistent": True,
        "keyboard_escape": escape,
    }


def _direct_boundary_check(session: Any) -> dict[str, Any]:
    from ms_event_studio.web_app import WebBoundaryError

    workspace = session.workspace()
    event = min(
        workspace["events"],
        key=lambda row: abs(float(row["apex_time_min"]) - ORIGINAL_APEX_SEC / 60.0),
    )
    selected = session.workspace({"selected_event_token": event["event_token"]})
    aim = session.begin_event_edit(
        {"mode": "adjust", "action_token": selected["selection"]["event"]["action_token"]}
    )
    allowed = aim["allowed_interval"]
    error = None
    try:
        session.preview_event_edit(
            {
                "aim_token": aim["aim_token"],
                "click_time_min": float(allowed["end_min"]) + 0.000001,
            }
        )
    except WebBoundaryError as exc:
        error = {"code": exc.code, "status": exc.status.value}
    _assert(error is not None, "service accepted an out-of-range preview")
    _assert(error["code"] == "outside_allowed_interval", "service returned wrong boundary code")
    session.cancel_event_edit({"edit_token": aim["aim_token"]})
    return {"ok": True, "allowed_interval": allowed, "error": error}


def run_gate(
    *,
    base_url: str,
    headed: bool,
    snapshot: Callable[[], dict[str, Any]],
    reopen: Callable[[], Any],
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required: pip install -e .[qa] && playwright install chromium"
        ) from exc

    requests: list[dict[str, str]] = []
    page_errors: list[str] = []
    console_errors: list[str] = []
    rows: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        context = browser.new_context(
            viewport=VIEWPORTS[0],
            color_scheme="light",
            locale="zh-CN",
            device_scale_factor=1,
        )
        page = context.new_page()
        page.on(
            "request",
            lambda request: requests.append(
                {"method": request.method.upper(), "path": urlparse(request.url).path}
            )
            if urlparse(request.url).path in set(EDIT_ENDPOINTS.values())
            else None,
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        try:
            for viewport in VIEWPORTS:
                reopen()
                page.set_viewport_size(viewport)
                _goto_real_workspace(page, base_url)
                _select_narrow_event(page)
                pointer = _mouse_candidate(page, requests, snapshot, viewport)
                _select_narrow_event(page)
                pointer["non_targets"] = _non_target_zero_preview(page, requests, snapshot)
                rows.append(pointer)

            reopen()
            page.set_viewport_size({"width": 1440, "height": 900})
            _goto_real_workspace(page, base_url)
            _select_narrow_event(page)
            keyboard = _keyboard_candidate_apply(
                page, base_url, requests, snapshot, reopen
            )
            dom = _assert_no_internal_dom_fields(page)
            unexpected_console = [
                message
                for message in console_errors
                if not message.startswith("Failed to load resource:")
            ]
            _assert(not page_errors, f"narrow-support browser page errors: {page_errors}")
            _assert(not unexpected_console, f"narrow-support console errors: {unexpected_console}")
            return {
                "pointer_viewports": {"ok": True, "rows": rows},
                "keyboard_flow": keyboard,
                "dom_safety": dom,
                "runtime_errors": {
                    "ok": True,
                    "page_errors": page_errors,
                    "application_console_errors": unexpected_console,
                },
                "api_counts": {
                    kind: _request_count(requests, kind) for kind in EDIT_ENDPOINTS
                },
            }
        finally:
            context.close()
            browser.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headed", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        default=REPOSITORY / "build/qa/ux-r4-narrow-support.json",
    )
    args = parser.parse_args(argv)

    report: dict[str, Any] = {
        "schema": "ms-event-studio-ux-r4-narrow-support-qa-v1",
        "started_at": _utc_now(),
        "browser_viewports_are_css_pixels": True,
        "native_dpi_evidence": False,
        "status": "error",
    }
    server = None
    temporary = None
    session = None
    try:
        from ms_event_studio.web_app import WebSession, create_http_server

        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(temporary.name)
        source, project = _create_narrow_support_project(root)
        source_before = _file_fingerprint(source)
        session = WebSession(root / "recent.json")
        registered = session.register_path("project_open", project.project_dir)
        project_token = registered["selection_token"]
        session.open_project(project_token)
        report["scientific_fixture"] = _support_evidence(project.project_dir)
        report["checks"] = {
            "service_out_of_range_rejected": _direct_boundary_check(session),
        }
        session.open_project(project_token)
        server = create_http_server(session=session)
        server.start()
        browser_checks = run_gate(
            base_url=server.url,
            headed=args.headed,
            snapshot=lambda: _scientific_snapshot(project.project_dir),
            reopen=lambda: session.open_project(project_token),
        )
        report["checks"].update(browser_checks)
        source_after = _file_fingerprint(source)
        _assert(source_after == source_before, "narrow-support gate changed its raw source")
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
        elif session is not None:
            session.close()
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
