"""Standalone Playwright gates for the UX-R2/R3 review workbench.

This script is deliberately not imported by the ordinary unit-test suite.  It
starts an ephemeral loopback service unless ``--base-url`` is supplied and
writes machine-readable evidence.  UX-R2 is a read-only fixture gate.  UX-R3
is kept as a separate stage so review persistence is never inferred from a
browser-only fixture.

The browser viewport and geometry checks use CSS pixels.  They are not native
Windows DPI or macOS Retina evidence.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Callable
from urllib.parse import urlencode, urlparse


REPOSITORY = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


READY_EXPRESSION = "window.__MS_EVENT_STUDIO__?.getState?.().ready === true"
R2_FIXTURES = (
    "review-no-selection",
    "review-unreviewed-auto",
    "review-accepted-auto",
    "review-rejected-auto",
    "review-pending-auto",
    "review-manual",
    "review-highest",
    "review-edge",
    "review-dense",
)
R3_FIXTURES = ("save-in-progress", "save-failed")
GEOMETRY_FIXTURES = ("review-highest", "review-edge", "review-dense")
STATUS_FIXTURES = {
    "review-unreviewed-auto": ("unreviewed", "automatic", None),
    "review-accepted-auto": ("accepted", "automatic", "review-accept"),
    "review-rejected-auto": ("rejected", "automatic", "review-reject"),
    "review-pending-auto": ("pending", "automatic", "review-pending"),
    "review-manual": ("accepted", "manual_adjusted", "review-accept"),
}
FILTERS = ("all", "unreviewed", "accepted", "rejected", "pending")
DECISION_CONTROLS = ("review-accept", "review-reject", "review-pending")
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SAFE_GAP_CSS_PX = 4.0
_NO_ARGUMENT = object()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _file_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _create_r3_scientific_project(root: Path) -> tuple[Path, Any]:
    """Create a tiny real project with an independently restorable apex.

    Two automatic events remain unreviewed so first-unreviewed selection and
    auto-advance are meaningful.  The third automatic event is accepted and
    adjusted from scan 900 to the real local maximum at scan 910.  This lets
    the browser gate prove the restore endpoint preserves a nontrivial review
    status without reading the large production source.
    """

    from ms_event_studio.project import CreateProjectRequest, create_project
    from ms_event_studio.window_service import ProjectWindowService

    scan_count = 1201
    pc34_mz = 760.5851
    qc782_mz = 782.5616
    signal = [
        1000.0 * math.exp(-0.5 * ((index - 900.0) / 30.0) ** 2)
        for index in range(scan_count)
    ]
    # A small local maximum inside the immutable support is deliberately
    # below the automatic detector threshold but remains real scan evidence.
    signal[909] -= 10.0
    signal[910] += 10.0
    signal[911] -= 10.0
    signal[300] = 1200.0
    signal[600] = 1100.0

    source_path = root / "r3-review-source.txt"
    with source_path.open("w", encoding="ascii", newline="\n") as handle:
        handle.write(f"spectrumList ({scan_count} spectra)\n")
        for index, intensity in enumerate(signal):
            time_min = index / 600.0
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
                f"  cvParam: scan start time, {time_min:.12f}, minute",
                "  cvParam: m/z array, m/z",
                f"  binary: [4] 100 {pc34_mz} {qc782_mz} 900",
                "  cvParam: intensity array, number of detector counts",
                "  binary: [4] " + " ".join(f"{value:.15g}" for value in values),
                "",
            )
            handle.write("\n".join(lines))

    project = create_project(
        CreateProjectRequest(
            source_path=source_path,
            project_dir=root / "r3-review-project",
            display_name="浏览器审阅门禁项目",
            analysis_start_min="0",
            analysis_end_min="2",
        )
    )
    with ProjectWindowService.open(project.project_dir) as service:
        events = service.all_events()
        if len(events) != 3:
            raise AssertionError(f"R3 scientific fixture detected {len(events)} events, expected 3")
        event = min(
            events,
            key=lambda row: abs(int(row["current_spectrum_index"]) - 900),
        )
        accepted = service.review_store.set_status(
            event["event_id"],
            "accepted",
            expected_revision=int(event["revision"]),
            actor="qa-fixture",
            session_id="qa-fixture",
            reason="prepare restorable accepted automatic event",
        )
        adjusted = service.review_store.adjust_apex(
            event["event_id"],
            click_time_sec=91.0,
            scans=service.scans,
            analysis_start_ns=service.analysis_start_ns,
            analysis_end_ns=service.analysis_end_ns,
            expected_revision=int(accepted["revision"]),
            actor="qa-fixture",
            session_id="qa-fixture",
            reason="move to adjacent real local maximum",
        )
        if (
            int(adjusted["current_spectrum_index"]) != 910
            or adjusted.get("origin") != "manual_adjusted"
            or adjusted.get("status") != "accepted"
        ):
            raise AssertionError("R3 scientific fixture did not preserve its intended apex state")
    return source_path, project


def _assert(condition: Any, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _qa(name: str) -> str:
    return f'[data-qa="{name}"]'


def _wait_for_eval(
    page: Any,
    expression: str,
    *,
    arg: Any = _NO_ARGUMENT,
    timeout: int = 5_000,
) -> Any:
    """Poll through the automation channel without page-side string eval.

    Playwright's ``wait_for_function`` implements polling with ``eval`` in the
    document and is correctly rejected by the normal page's strict CSP.  A
    direct DevTools evaluation does not require weakening the application CSP.
    """

    deadline = time.monotonic() + timeout / 1_000
    last_value: Any = None
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            last_value = (
                page.evaluate(expression)
                if arg is _NO_ARGUMENT
                else page.evaluate(expression, arg)
            )
            if last_value:
                return last_value
            last_error = None
        except BaseException as exc:
            last_error = exc
        time.sleep(0.02)
    detail = f"; last error: {last_error}" if last_error is not None else f"; last value: {last_value!r}"
    raise AssertionError(f"timed out after {timeout} ms waiting for {expression!r}{detail}")


def _hook_state(page: Any) -> dict[str, Any]:
    state = page.evaluate("window.__MS_EVENT_STUDIO__?.getState?.() ?? null")
    _assert(isinstance(state, dict), "frontend smoke hook did not return an object")
    return state


def _workbench_state(page: Any) -> dict[str, Any]:
    state = _hook_state(page)
    workbench = state.get("workbench")
    _assert(isinstance(workbench, dict), "smoke hook did not expose workbench state")
    return workbench


def _goto_fixture(page: Any, base_url: str, fixture: str) -> dict[str, Any]:
    query = urlencode({"fixture": fixture})
    page.goto(f"{base_url.rstrip('/')}?{query}")
    _wait_for_eval(page, READY_EXPRESSION, timeout=20_000)
    _wait_for_eval(
        page,
        "fixture => window.__MS_EVENT_STUDIO__?.getState?.().fixture === fixture",
        arg=fixture,
        timeout=20_000,
    )
    state = _hook_state(page)
    _assert(state.get("ready") is True, f"{fixture}: frontend was not ready")
    _assert(state.get("fixture") == fixture, f"{fixture}: fixture identity was lost")
    if fixture.startswith("review-") or fixture.startswith("save-"):
        _assert(isinstance(state.get("workbench"), dict), f"{fixture}: workbench hook is absent")
        page.locator(_qa("workbench")).wait_for(state="visible", timeout=20_000)
    return state


def _wait_workbench_field(page: Any, field: str, expected: Any, *, timeout: int = 3_000) -> None:
    _wait_for_eval(
        page,
        "arg => window.__MS_EVENT_STUDIO__?.getState?.().workbench?.[arg.field] === arg.expected",
        arg={"field": field, "expected": expected},
        timeout=timeout,
    )


def _selected_semantics(locator: Any) -> dict[str, str | None]:
    names = ("aria-checked", "aria-pressed", "aria-selected", "aria-current")
    return {name: locator.get_attribute(name) for name in names}


def _is_semantically_selected(locator: Any) -> bool:
    values = _selected_semantics(locator)
    return any(
        value is not None and value.casefold() not in {"", "false", "none"}
        for value in values.values()
    )


def _event_rows(page: Any) -> list[dict[str, Any]]:
    return page.eval_on_selector_all(
        _qa("event-row"),
        """rows => rows.map((row, index) => ({
          index,
          status: row.dataset.eventStatus || null,
          selected: row.getAttribute('aria-selected'),
          current: row.getAttribute('aria-current'),
          disabled: row.matches(':disabled') || row.getAttribute('aria-disabled') === 'true',
          tag: row.tagName.toLowerCase(),
          tabIndex: row.tabIndex,
          text: (row.textContent || '').trim()
        }))""",
    )


def _storage_evidence(page: Any) -> dict[str, Any]:
    return page.evaluate(
        """async () => ({
          localStorageKeys: Object.keys(localStorage),
          sessionStorageKeys: Object.keys(sessionStorage),
          cookie: document.cookie,
          indexedDatabases: typeof indexedDB.databases === 'function'
            ? (await indexedDB.databases()).map(row => row.name || '')
            : [],
          cacheKeys: typeof caches === 'undefined' ? [] : await caches.keys()
        })"""
    )


def _assert_no_browser_persistence(storage: dict[str, Any]) -> None:
    _assert(storage["localStorageKeys"] == [], "fixture wrote localStorage")
    _assert(storage["sessionStorageKeys"] == [], "fixture wrote sessionStorage")
    _assert(storage["cookie"] == "", "fixture wrote a browser cookie")
    _assert(storage["indexedDatabases"] == [], "fixture wrote IndexedDB")
    _assert(storage["cacheKeys"] == [], "fixture wrote Cache Storage")


def _check_fixture_readiness(page: Any, base_url: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for fixture in R2_FIXTURES:
        state = _goto_fixture(page, base_url, fixture)
        workbench = state["workbench"]
        selected = workbench.get("selectedEventKey")
        if fixture == "review-no-selection":
            _assert(selected in {None, ""}, "no-selection fixture selected an event")
        else:
            _assert(isinstance(selected, str) and selected, f"{fixture}: selected key is absent")
        _assert(
            isinstance(workbench.get("viewport"), dict),
            f"{fixture}: viewport evidence is absent from the read-only hook",
        )
        rows.append(
            {
                "fixture": fixture,
                "selected": bool(selected),
                "status": workbench.get("status"),
                "source": workbench.get("source"),
                "event_index": workbench.get("eventIndex"),
                "event_count": workbench.get("eventCount"),
                "viewport": workbench.get("viewport"),
            }
        )
    return {"ok": True, "fixtures": rows}


def _check_initial_selection_and_status(page: Any, base_url: str) -> dict[str, Any]:
    _goto_fixture(page, base_url, "review-unreviewed-auto")
    rows = _event_rows(page)
    _assert(len(rows) >= 3, "workbench needs at least three event rows for navigation QA")
    _assert(all(row["status"] in {"unreviewed", "accepted", "rejected", "pending"} for row in rows),
            "event rows must expose a closed data-event-status value")
    first_unreviewed = next((row["index"] for row in rows if row["status"] == "unreviewed"), None)
    selected_rows = [
        row for row in rows
        if row["selected"] == "true" or (row["current"] or "").casefold() not in {"", "false", "none"}
    ]
    _assert(first_unreviewed is not None, "fixture has no unreviewed event")
    _assert(len(selected_rows) == 1, f"expected exactly one selected event row, got {selected_rows}")
    _assert(selected_rows[0]["index"] == first_unreviewed,
            "opening a project did not select the first unreviewed event")

    states: list[dict[str, Any]] = []
    for fixture, (status, source, active_control) in STATUS_FIXTURES.items():
        _goto_fixture(page, base_url, fixture)
        workbench = _workbench_state(page)
        _assert(workbench.get("status") == status, f"{fixture}: wrong status")
        _assert(workbench.get("source") == source, f"{fixture}: wrong source")
        selected_controls = [
            name for name in DECISION_CONTROLS
            if _is_semantically_selected(page.locator(_qa(name)))
        ]
        expected_controls = [] if active_control is None else [active_control]
        _assert(selected_controls == expected_controls,
                f"{fixture}: segmented state {selected_controls}, expected {expected_controls}")
        states.append(
            {
                "fixture": fixture,
                "status": status,
                "source": source,
                "selected_controls": selected_controls,
            }
        )

    segmented = page.locator(_qa("review-segmented"))
    role = segmented.get_attribute("role")
    _assert(role in {"group", "radiogroup"}, "review segmented control lacks group semantics")
    accessible_name = segmented.get_attribute("aria-label") or segmented.get_attribute("aria-labelledby")
    _assert(bool(accessible_name), "review segmented control has no accessible name")
    return {
        "ok": True,
        "first_unreviewed_index": first_unreviewed,
        "selected_row": selected_rows[0],
        "status_fixtures": states,
        "segmented_role": role,
    }


def _check_selection_and_navigation(page: Any, base_url: str) -> dict[str, Any]:
    _goto_fixture(page, base_url, "review-unreviewed-auto")
    rows = page.locator(_qa("event-row"))
    _assert(rows.count() >= 4, "selection QA needs four event rows")

    initial = _workbench_state(page)
    click_target = 2 if initial.get("eventIndex") != 2 else 3
    rows.nth(click_target).click()
    _wait_workbench_field(page, "eventIndex", click_target)
    after_click = _workbench_state(page)
    _assert(after_click.get("selectedEventKey") != initial.get("selectedEventKey"),
            "mouse event selection did not change the selected key")

    keyboard_target = 3 if click_target != 3 else 2
    rows.nth(keyboard_target).focus()
    page.keyboard.press("Enter")
    _wait_workbench_field(page, "eventIndex", keyboard_target)
    after_keyboard = _workbench_state(page)
    _assert(after_keyboard.get("selectedEventKey") != after_click.get("selectedEventKey"),
            "keyboard event selection did not change the selected key")

    middle = 2
    rows.nth(middle).click()
    _wait_workbench_field(page, "eventIndex", middle)
    next_button = page.locator(_qa("next-event"))
    previous_button = page.locator(_qa("previous-event"))
    _assert(next_button.is_enabled(), "next-event control is disabled away from the boundary")
    next_button.click()
    _wait_workbench_field(page, "eventIndex", middle + 1)
    _assert(previous_button.is_enabled(), "previous-event control is disabled away from the boundary")
    previous_button.focus()
    page.keyboard.press("Enter")
    _wait_workbench_field(page, "eventIndex", middle)

    return {
        "ok": True,
        "initial_index": initial.get("eventIndex"),
        "mouse_index": after_click.get("eventIndex"),
        "keyboard_index": after_keyboard.get("eventIndex"),
        "previous_next_keyboard": True,
    }


def _check_view_controls(page: Any, base_url: str) -> dict[str, Any]:
    _goto_fixture(page, base_url, "review-unreviewed-auto")
    filter_control = page.locator(_qa("event-filter"))
    _assert(filter_control.count() == 1, "event filter select is missing or duplicated")
    filter_results: list[dict[str, Any]] = []
    for index, value in enumerate(FILTERS):
        option = page.locator(f'{_qa("event-filter")} option[data-qa="filter-{value}"]')
        _assert(option.count() == 1, f"{value} filter option lacks its stable QA selector")
        if value == "unreviewed":
            filter_control.focus()
            page.keyboard.press("Home")
            page.keyboard.press("ArrowDown")
        else:
            filter_control.select_option(value)
        _wait_workbench_field(page, "filter", value)
        _assert(filter_control.input_value() == value,
                f"{value} filter value disagrees with hook state")
        filter_results.append({"filter": value, "select_value": filter_control.input_value()})

    for value in ("linear", "log"):
        control = page.locator(_qa(f"scale-{value}"))
        control.click()
        _wait_workbench_field(page, "scale", value)
        _assert(_is_semantically_selected(control), f"{value} scale lacks selected semantics")

    labels = page.locator(_qa("toggle-labels"))
    before_labels = bool(_workbench_state(page).get("labels"))
    labels.focus()
    page.keyboard.press("Space")
    _wait_workbench_field(page, "labels", not before_labels)
    _assert(labels.get_attribute("aria-pressed") == str(not before_labels).lower(),
            "label toggle aria-pressed disagrees with hook state")

    return {
        "ok": True,
        "filters": filter_results,
        "scales": ["linear", "log"],
        "labels_before": before_labels,
        "labels_after": not before_labels,
    }


def _check_evidence_layout(page: Any, base_url: str) -> dict[str, Any]:
    page.set_viewport_size({"width": 1920, "height": 1080})
    _goto_fixture(page, base_url, "review-unreviewed-auto")
    core = page.locator(_qa("core-evidence"))
    _assert(core.is_visible(), "core evidence is not visible")
    bounds = core.bounding_box()
    _assert(bounds is not None, "core evidence has no layout box")
    _assert(bounds["x"] >= 0 and bounds["y"] >= 0,
            "core evidence starts outside the CSS viewport")
    _assert(bounds["x"] + bounds["width"] <= 1920.5,
            "core evidence overflows the CSS viewport horizontally")
    _assert(bounds["y"] + bounds["height"] <= 1080.5,
            "core evidence requires page scrolling at the 1920x1080 CSS viewport")

    toggle = page.locator(_qa("evidence-toggle"))
    details = page.locator(_qa("more-evidence"))
    before = bool(_workbench_state(page).get("moreEvidenceExpanded"))
    _assert(toggle.get_attribute("aria-expanded") == str(before).lower(),
            "evidence toggle aria-expanded disagrees with hook state")
    toggle.click()
    _wait_workbench_field(page, "moreEvidenceExpanded", not before)
    _assert(toggle.get_attribute("aria-expanded") == str(not before).lower(),
            "evidence toggle did not update aria-expanded")
    _assert(details.is_visible() is (not before),
            "evidence details visibility disagrees with expanded state")

    return {
        "ok": True,
        "viewport_css_pixels": {"width": 1920, "height": 1080},
        "core_evidence_bounds": bounds,
        "expanded_before": before,
        "expanded_after": not before,
        "native_dpi_evidence": False,
    }


def _check_marker_hover_focus_callout(
    page: Any,
    base_url: str,
    write_count: Callable[[], int],
) -> dict[str, Any]:
    page.set_viewport_size({"width": 1366, "height": 768})
    _goto_fixture(page, base_url, "review-unreviewed-auto")
    before_state = _workbench_state(page)
    before_writes = write_count()
    markers = page.locator(_qa("plot-marker"))
    labeled_keys = set(
        page.locator(_qa("plot-label")).evaluate_all(
            "labels => labels.map(label => label.dataset.eventKey)"
        )
    )
    target_index = next(
        (
            index
            for index in range(markers.count())
            if markers.nth(index).get_attribute("data-event-key") not in labeled_keys
        ),
        None,
    )
    _assert(target_index is not None, "fixture has no initially unlabeled marker for hover QA")
    target = markers.nth(target_index)
    target_key = target.get_attribute("data-event-key")
    _assert(bool(target_key), "hover target lacks its opaque event key")

    callout_expression = (
        "key => Array.from(document.querySelectorAll('[data-qa=\"plot-label\"]'))"
        ".some(label => label.dataset.eventKey === key)"
    )
    target.hover()
    _wait_for_eval(page, callout_expression, arg=target_key)
    after_hover = _workbench_state(page)
    _assert(after_hover.get("selectedEventKey") == before_state.get("selectedEventKey"),
            "hovering an event marker changed selection")

    page.mouse.move(1, 1)
    _wait_for_eval(page, f"key => !({callout_expression})(key)", arg=target_key)
    target.focus()
    _wait_for_eval(page, callout_expression, arg=target_key)
    _assert(
        target.evaluate("element => document.activeElement === element"),
        "plot marker cannot receive keyboard focus",
    )
    after_focus = _workbench_state(page)
    _assert(after_focus.get("selectedEventKey") == before_state.get("selectedEventKey"),
            "focusing an event marker changed selection")

    visible_codes = page.locator(f'{_qa("plot-svg")} text').evaluate_all(
        "nodes => nodes.map(node => (node.textContent || '').trim())"
        ".filter(text => /^(U|A|R|P)$/.test(text))"
    )
    _assert(visible_codes == [], f"plot repeats U/A/R/P letter codes: {visible_codes}")
    legend_states = page.locator(f'{_qa("plot-legend")} [data-event-status]').evaluate_all(
        "nodes => nodes.map(node => node.dataset.eventStatus)"
    )
    _assert(legend_states == ["unreviewed", "accepted", "rejected", "pending"],
            f"legend does not expose the four canonical shape/status entries: {legend_states}")
    _assert(
        page.locator("#labelLayer").evaluate(
            "element => getComputedStyle(element).pointerEvents === 'none'"
        ),
        "label layer can intercept marker pointer interaction",
    )
    _assert(write_count() == before_writes, "marker hover/focus issued a write request")
    return {
        "ok": True,
        "hover_callout": True,
        "focus_callout": True,
        "selection_unchanged": True,
        "visible_letter_codes": visible_codes,
        "legend_states": legend_states,
        "label_layer_pointer_events": "none",
        "writes": 0,
    }


def _rect_audit(page: Any) -> dict[str, Any]:
    return page.evaluate(
        r"""selectors => {
          const svg = document.querySelector(selectors.svg);
          const content = document.querySelector(selectors.content);
          if (!svg || !content) return {missing: {svg: !svg, content: !content}};
          const rect = element => {
            const box = element.getBoundingClientRect();
            return {left: box.left, top: box.top, right: box.right, bottom: box.bottom,
                    width: box.width, height: box.height};
          };
          const visible = element => {
            const style = getComputedStyle(element);
            const box = element.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden'
              && Number(style.opacity || 1) !== 0 && box.width > 0 && box.height > 0;
          };
          const contentRect = rect(content);
          const items = {};
          for (const [name, selector] of Object.entries(selectors.items)) {
            items[name] = Array.from(svg.querySelectorAll(selector)).filter(visible).map(element => ({
              rect: rect(element),
              declaredBox: ['labelLeft', 'labelTop', 'labelRight', 'labelBottom']
                .every(key => Number.isFinite(Number(element.dataset[key])))
                ? {
                    left: Number(element.dataset.labelLeft),
                    top: Number(element.dataset.labelTop),
                    right: Number(element.dataset.labelRight),
                    bottom: Number(element.dataset.labelBottom)
                  }
                : null,
              svgBox: (() => {
                try {
                  const box = element.getBBox();
                  let owner = element;
                  let translateX = 0;
                  let translateY = 0;
                  while (owner && owner !== svg) {
                    const transform = owner.getAttribute?.('transform') || '';
                    const match = transform.match(/^translate\(\s*(-?[\d.]+)(?:[ ,]+(-?[\d.]+))?\s*\)$/);
                    if (match) {
                      translateX += Number(match[1]);
                      translateY += Number(match[2] || 0);
                    }
                    owner = owner.parentElement;
                  }
                  const left = box.x + translateX;
                  const top = box.y + translateY;
                  const right = left + box.width;
                  const bottom = top + box.height;
                  return {x: left, y: top, width: right - left, height: bottom - top,
                          left, top, right, bottom};
                } catch (_error) {
                  return null;
                }
              })(),
              clipped: Boolean(element.closest('[clip-path]')),
              tag: element.tagName.toLowerCase()
            }));
          }
          const labels = items.label || [];
          const markerHits = Array.from(svg.querySelectorAll(
            '[data-qa="plot-marker"] .event-marker-hit'
          )).filter(visible);
          const overlaps = [];
          for (let left = 0; left < labels.length; left += 1) {
            for (let right = left + 1; right < labels.length; right += 1) {
              const a = labels[left].declaredBox || labels[left].svgBox || labels[left].rect;
              const b = labels[right].declaredBox || labels[right].svgBox || labels[right].rect;
              const width = Math.min(a.right, b.right) - Math.max(a.left, b.left);
              const height = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
              if (width > 1 && height > 1) overlaps.push({left, right, width, height});
            }
          }
          const legend = items.legend?.[0]?.svgBox || null;
          const labelLegendOverlaps = legend
            ? labels.map((label, index) => {
                const box = label.declaredBox || label.svgBox;
                if (!box) return null;
                const width = Math.min(box.right, legend.right) - Math.max(box.left, legend.left);
                const height = Math.min(box.bottom, legend.bottom) - Math.max(box.top, legend.top);
                return width > -4 && height > -4 ? {index, width, height} : null;
              }).filter(Boolean)
            : [{error: 'legend SVG bbox unavailable'}];
          return {
            svg: rect(svg),
            content: contentRect,
            clipPathCount: svg.querySelectorAll('clipPath').length,
            markerHitCount: markerHits.length,
            unclippedMarkerHitCount: markerHits.filter(element => !element.closest('[clip-path]')).length,
            items,
            labelOverlaps: overlaps,
            labelLegendOverlaps
          };
        }""",
        {
            "svg": _qa("plot-svg"),
            "content": _qa("plot-content"),
            "items": {
                "marker": f'{_qa("plot-marker")} .event-marker-shape',
                "label": _qa("plot-label"),
                "legend": _qa("plot-legend"),
            },
        },
    )


def _minimum_inset(content: dict[str, float], rect: dict[str, float]) -> float:
    return min(
        rect["left"] - content["left"],
        rect["top"] - content["top"],
        content["right"] - rect["right"],
        content["bottom"] - rect["bottom"],
    )


def _minimum_svg_inset(rect: dict[str, float]) -> float:
    return min(
        rect["left"] - 62.0,
        rect["top"] - 26.0,
        980.0 - rect["right"],
        542.0 - rect["bottom"],
    )


def _click_plot_outside_content(page: Any) -> dict[str, float]:
    audit = _rect_audit(page)
    svg = audit["svg"]
    content = audit["content"]
    candidates = (
        {"x": (svg["left"] + svg["right"]) / 2, "y": (svg["top"] + content["top"]) / 2},
        {"x": (svg["left"] + content["left"]) / 2, "y": (content["top"] + content["bottom"]) / 2},
        {"x": (content["right"] + svg["right"]) / 2, "y": (content["top"] + content["bottom"]) / 2},
        {"x": (svg["left"] + svg["right"]) / 2, "y": (content["bottom"] + svg["bottom"]) / 2},
    )
    for point in candidates:
        inside_svg = svg["left"] + 1 <= point["x"] <= svg["right"] - 1 and svg["top"] + 1 <= point["y"] <= svg["bottom"] - 1
        outside_content = not (
            content["left"] <= point["x"] <= content["right"]
            and content["top"] <= point["y"] <= content["bottom"]
        )
        if inside_svg and outside_content:
            page.mouse.click(point["x"], point["y"])
            return point
    raise AssertionError("plot exposes no clickable SVG margin outside its content rectangle")


def _check_plot_geometry(
    page: Any,
    base_url: str,
    write_count: Callable[[], int],
) -> dict[str, Any]:
    viewports = ((960, 640), (1366, 768), (1920, 1080))
    results: list[dict[str, Any]] = []
    for width, height in viewports:
        page.set_viewport_size({"width": width, "height": height})
        for fixture in GEOMETRY_FIXTURES:
            label = f"{fixture}@{width}x{height}"
            _goto_fixture(page, base_url, fixture)
            before_state = _workbench_state(page)
            before_writes = write_count()
            audit = _rect_audit(page)
            _assert(not audit.get("missing"), f"{label}: plot SVG/content rectangle is missing")
            _assert(audit["clipPathCount"] >= 1, f"{label}: SVG has no clipPath")
            _assert(audit["items"]["marker"], f"{label}: no visible plot marker")
            _assert(audit["items"]["legend"], f"{label}: no visible plot legend")
            _assert(audit["markerHitCount"] == len(audit["items"]["marker"]),
                    f"{label}: each visible marker must have one transparent hit target")
            _assert(audit["unclippedMarkerHitCount"] == 0,
                    f"{label}: a transparent marker hit target is not clipped to plot content")
            _assert(all(item["clipped"] for item in audit["items"]["marker"]),
                    f"{label}: an event marker is outside a clipped overlay layer")
            _assert(all(item["clipped"] for item in audit["items"]["label"]),
                    f"{label}: an event label is outside a clipped overlay layer")
            insets: dict[str, list[float]] = {}
            svg_insets: dict[str, list[float]] = {}
            for item_type, items in audit["items"].items():
                values = [_minimum_inset(audit["content"], item["rect"]) for item in items]
                original_values = [
                    _minimum_svg_inset(item.get("declaredBox") or item["svgBox"])
                    for item in items
                    if item.get("declaredBox") is not None or item.get("svgBox") is not None
                ]
                insets[item_type] = values
                svg_insets[item_type] = original_values
                _assert(all(value >= SAFE_GAP_CSS_PX - 0.01 for value in values),
                        f"{label}: {item_type} violates the 4 rendered CSS px "
                        f"content-boundary gap: {values}")
                _assert(all(value >= SAFE_GAP_CSS_PX - 0.01 for value in original_values),
                        f"{label}: unclipped {item_type} geometry violates the 4 SVG-unit "
                        f"content-boundary gap: {original_values}")
            _assert(audit["labelOverlaps"] == [],
                    f"{label}: visible callout boxes collide: {audit['labelOverlaps']}")
            _assert(audit["labelLegendOverlaps"] == [],
                    f"{label}: callout and legend bboxes collide or lack 4 px separation: "
                    f"{audit['labelLegendOverlaps']}")

            margin_point = _click_plot_outside_content(page)
            legend_bounds = page.locator(_qa("plot-legend")).first.bounding_box()
            _assert(legend_bounds is not None, f"{label}: plot legend has no layout box")
            # The legend is explanatory SVG, not a control.  Send a real
            # pointer at its rendered coordinates without requiring
            # Playwright actionability.
            page.mouse.click(
                legend_bounds["x"] + min(2.0, legend_bounds["width"] / 2),
                legend_bounds["y"] + min(2.0, legend_bounds["height"] / 2),
            )
            after_state = _workbench_state(page)
            _assert(after_state.get("selectedEventKey") == before_state.get("selectedEventKey"),
                    f"{label}: plot margin/legend click changed event selection")
            _assert(write_count() == before_writes,
                    f"{label}: plot margin/legend click issued a write request")
            results.append(
                {
                    "fixture": fixture,
                    "viewport_css_pixels": {"width": width, "height": height},
                    "clip_path_count": audit["clipPathCount"],
                    "transparent_hit_targets": audit["markerHitCount"],
                    "visible_counts": {key: len(value) for key, value in audit["items"].items()},
                    "minimum_insets_css_px": {
                        key: min(value) if value else None for key, value in insets.items()
                    },
                    "minimum_unclipped_insets_svg_units": {
                        key: min(value) if value else None for key, value in svg_insets.items()
                    },
                    "label_overlaps": audit["labelOverlaps"],
                    "label_legend_overlaps": audit["labelLegendOverlaps"],
                    "outside_content_click": margin_point,
                    "writes": write_count() - before_writes,
                }
            )
    return {
        "ok": True,
        "safe_gap_css_px": SAFE_GAP_CSS_PX,
        "viewports_css_pixels": [
            {"width": width, "height": height} for width, height in viewports
        ],
        "native_dpi_evidence": False,
        "fixtures": results,
    }


def _check_r2_disabled_write_placeholders(page: Any, base_url: str) -> dict[str, Any]:
    _goto_fixture(page, base_url, "review-unreviewed-auto")
    controls = [*DECISION_CONTROLS, "review-clear", "undo", "redo"]
    results: dict[str, bool] = {}
    for name in controls:
        control = page.locator(_qa(name))
        _assert(control.count() == 1, f"R2 placeholder {name} is missing or duplicated")
        disabled = control.is_disabled() or control.get_attribute("aria-disabled") == "true"
        _assert(disabled, f"R2 placeholder {name} must remain disabled until UX-R3")
        results[name] = disabled
    return {"ok": True, "disabled": results}


def _check_r3_fixture_states(page: Any, base_url: str) -> dict[str, Any]:
    """TDD gate for the UX-R3 loading/error contract.

    These checks intentionally fail until the R3 fixtures and frontend write
    flow exist.  They must not be used as evidence of backend persistence.
    """

    rows: list[dict[str, Any]] = []
    for fixture, expected_saving in (("save-in-progress", True), ("save-failed", False)):
        _goto_fixture(page, base_url, fixture)
        workbench = _workbench_state(page)
        _assert(workbench.get("saving") is expected_saving, f"{fixture}: wrong saving state")
        surface = page.locator(_qa("workbench"))
        _assert(surface.get_attribute("aria-busy") == str(expected_saving).lower(),
                f"{fixture}: aria-busy disagrees with saving state")
        if expected_saving:
            for name in (*DECISION_CONTROLS, "review-clear", "review-note", "undo", "redo"):
                control = page.locator(_qa(name))
                _assert(control.is_disabled() or control.get_attribute("aria-disabled") == "true",
                        f"{fixture}: {name} remains operable while saving")
        else:
            alert = page.locator('[role="alert"]:visible')
            _assert(alert.count() >= 1, "save-failed lacks a visible accessible error")
            _assert(bool(alert.first.inner_text().strip()), "save-failed error is empty")
        rows.append({"fixture": fixture, "saving": expected_saving})
    return {"ok": True, "fixtures": rows, "persistence_proven": False}


def _goto_real_workspace(page: Any, base_url: str) -> dict[str, Any]:
    page.goto(base_url)
    _wait_for_eval(page, READY_EXPRESSION, timeout=20_000)
    _wait_for_eval(
        page,
        "window.__MS_EVENT_STUDIO__?.getState?.().workbench != null",
        timeout=20_000,
    )
    state = _hook_state(page)
    _assert(state.get("fixture") in {None, ""}, "real-project gate accidentally loaded a fixture")
    _assert(state.get("view") == "project", "real project did not open in the workbench")
    return state["workbench"]


def _workspace_review_counts(page: Any) -> dict[str, int]:
    review = page.evaluate(
        """async () => {
          const response = await fetch('/api/workspace', {
            headers: {Accept: 'application/json'}, cache: 'no-store', credentials: 'same-origin'
          });
          if (!response.ok) throw new Error(`workspace ${response.status}`);
          const payload = await response.json();
          return payload.review;
        }"""
    )
    _assert(isinstance(review, dict), "real workspace did not return review counts")
    return {key: int(value) for key, value in review.items()}


def _select_real_event(page: Any, index: int) -> dict[str, Any]:
    rows = page.locator(_qa("event-row"))
    _assert(0 <= index < rows.count(), f"real event row {index} is unavailable")
    rows.nth(index).click()
    _wait_workbench_field(page, "eventIndex", index, timeout=5_000)
    return _workbench_state(page)


def _check_review_decisions_and_clear(page: Any) -> dict[str, Any]:
    scenarios = (
        (0, "review-accept", "accepted"),
        (1, "review-reject", "rejected"),
        (2, "review-pending", "pending"),
    )
    results: list[dict[str, Any]] = []
    for index, control_name, expected_status in scenarios:
        selected = _select_real_event(page, index)
        selected_key = selected.get("selectedEventKey")
        page.locator(_qa(control_name)).click()
        _wait_for_eval(
            page,
            "arg => { const workbench = window.__MS_EVENT_STUDIO__?.getState?.().workbench; "
            "return workbench?.saving === false && "
            "Array.from(document.querySelectorAll('[data-qa=\"event-row\"]')).some(row => "
            "row.dataset.eventKey === arg.key && row.dataset.eventStatus === arg.status); }",
            arg={"key": selected_key, "status": expected_status},
            timeout=5_000,
        )
        results.append({"event_index": index, "status": expected_status})

    selected = _select_real_event(page, 1)
    _assert(selected.get("status") == "rejected", "clear gate did not reselect rejected event")
    clear = page.locator(_qa("review-clear"))
    _assert(clear.is_enabled(), "clear review is disabled for a reviewed event")
    clear.click()
    _wait_for_eval(
        page,
        "window.__MS_EVENT_STUDIO__?.getState?.().workbench?.saving === false "
        "&& window.__MS_EVENT_STUDIO__?.getState?.().workbench?.status === 'unreviewed'",
        timeout=5_000,
    )
    return {"ok": True, "decisions": results, "clear_status": "unreviewed"}


def _check_restore_automatic_status_preserved(
    page: Any,
    write_count: Callable[[], int],
) -> dict[str, Any]:
    restore = page.locator(_qa("restore-automatic"))
    _assert(restore.count() == 1, "restore automatic apex control is missing or duplicated")
    candidate_index: int | None = None
    for index in range(page.locator(_qa("event-row")).count()):
        candidate = _select_real_event(page, index)
        if candidate.get("source") == "manual_adjusted":
            candidate_index = index
            break
    _assert(candidate_index is not None, "real project has no manually adjusted automatic event")
    _assert(restore.is_visible(), "manual adjustment does not reveal restore automatic apex")
    before = _workbench_state(page)
    before_status = before.get("status")
    before_key = before.get("selectedEventKey")
    before_writes = write_count()
    _assert(restore.is_enabled(), "restore automatic apex is visible but disabled")
    restore.click()
    _wait_for_eval(
        page,
        "window.__MS_EVENT_STUDIO__?.getState?.().workbench?.saving === false "
        "&& window.__MS_EVENT_STUDIO__?.getState?.().workbench?.source === 'automatic'",
        timeout=5_000,
    )
    after = _workbench_state(page)
    _assert(after.get("status") == before_status,
            "restoring the automatic apex changed the review status")
    _assert(after.get("selectedEventKey") == before_key,
            "restoring the automatic apex changed event selection")
    _assert(write_count() == before_writes + 1,
            "restoring the automatic apex did not issue exactly one write request")
    _assert(not restore.is_visible(),
            "restore automatic apex remains visible after returning to automatic evidence")
    return {
        "ok": True,
        "status": "verified",
        "event_index": candidate_index,
        "review_status_before": before_status,
        "review_status_after": after.get("status"),
        "source_before": before.get("source"),
        "source_after": after.get("source"),
        "selection_preserved": True,
        "writes": 1,
    }


def _check_note_shortcut_isolation(
    page: Any,
    write_count: Callable[[], int],
) -> dict[str, Any]:
    note = page.locator(_qa("review-note"))
    _assert(note.is_enabled(), "review note is not enabled in the R3 workbench")
    before_state = _workbench_state(page)
    before_writes = write_count()
    note.focus()
    text = "aRpU"
    page.keyboard.type(text)
    _assert(note.input_value() == text, "single-letter note input was intercepted by shortcuts")
    after_state = _workbench_state(page)
    _assert(after_state.get("selectedEventKey") == before_state.get("selectedEventKey"),
            "single-letter note input changed event selection")
    _assert(after_state.get("status") == before_state.get("status"),
            "single-letter note input changed review status")
    _assert(write_count() == before_writes, "single-letter note input issued a write request")
    return {"ok": True, "typed": text, "writes": 0}


def _check_saving_disabled_and_release(
    page: Any,
    write_count: Callable[[], int],
) -> dict[str, Any]:
    held: list[Any] = []

    def hold_decision(route: Any) -> None:
        if held:
            route.continue_()
        else:
            held.append(route)

    page.route("**/api/review/decision", hold_decision)
    initial = _workbench_state(page)
    _assert(initial.get("status") == "unreviewed", "saving gate must start on an unreviewed event")
    before_writes = write_count()
    page.locator(_qa("review-accept")).click()
    _wait_for_eval(
        page,
        "window.__MS_EVENT_STUDIO__?.getState?.().workbench?.saving === true",
        timeout=3_000,
    )
    _assert(held, "decision request was not held for loading-state inspection")
    busy = page.locator(_qa("workbench"))
    _assert(busy.get_attribute("aria-busy") == "true", "saving workbench lacks aria-busy=true")
    controls = (*DECISION_CONTROLS, "review-clear", "review-note", "undo", "redo")
    disabled: dict[str, bool] = {}
    for name in controls:
        control = page.locator(_qa(name))
        value = control.is_disabled() or control.get_attribute("aria-disabled") == "true"
        _assert(value, f"{name} remains operable while review is saving")
        disabled[name] = value

    held[0].continue_()
    _wait_for_eval(
        page,
        "arg => { const workbench = window.__MS_EVENT_STUDIO__?.getState?.().workbench; "
        "return workbench?.saving === false && workbench?.selectedEventKey !== arg; }",
        arg=initial.get("selectedEventKey"),
        timeout=5_000,
    )
    page.unroute("**/api/review/decision", hold_decision)
    _assert(write_count() == before_writes + 1, "saving check did not issue exactly one decision")
    return {
        "ok": True,
        "aria_busy": True,
        "disabled": disabled,
        "auto_advanced": True,
        "writes": 1,
    }


def _click_and_measure_auto_advance(page: Any) -> dict[str, Any]:
    initial = _workbench_state(page)
    _assert(initial.get("status") == "unreviewed", "latency gate must start on an unreviewed event")
    page.evaluate("window.__uxR3DecisionStarted = performance.now()")
    page.locator(_qa("review-accept")).click()
    _wait_for_eval(
        page,
        "arg => { const workbench = window.__MS_EVENT_STUDIO__?.getState?.().workbench; "
        "return workbench?.saving === false && workbench?.selectedEventKey !== arg; }",
        arg=initial.get("selectedEventKey"),
        timeout=5_000,
    )
    elapsed = float(page.evaluate("performance.now() - window.__uxR3DecisionStarted"))
    _assert(elapsed < 250.0, f"review success and auto-advance took {elapsed:.1f} ms")
    return {
        "ok": True,
        "elapsed_ms": round(elapsed, 3),
        "limit_ms": 250,
        "selected_key_changed": True,
    }


def _check_undo_redo(page: Any) -> dict[str, Any]:
    page.locator(_qa("undo")).click()
    _wait_for_eval(
        page,
        "window.__MS_EVENT_STUDIO__?.getState?.().workbench?.status === 'unreviewed' "
        "&& window.__MS_EVENT_STUDIO__?.getState?.().workbench?.canRedo === true",
        timeout=5_000,
    )
    undone = _workbench_state(page)
    page.locator(_qa("redo")).click()
    _wait_for_eval(
        page,
        "window.__MS_EVENT_STUDIO__?.getState?.().workbench?.status === 'accepted' "
        "&& window.__MS_EVENT_STUDIO__?.getState?.().workbench?.saving === false",
        timeout=5_000,
    )
    redone = _workbench_state(page)
    return {
        "ok": True,
        "undo_status": undone.get("status"),
        "undo_can_redo": undone.get("canRedo"),
        "redo_status": redone.get("status"),
    }


def _check_conflict_rollback(page: Any) -> dict[str, Any]:
    before = _workbench_state(page)
    before_rows = _event_rows(page)

    def conflict(route: Any) -> None:
        route.fulfill(
            status=409,
            content_type="application/json; charset=utf-8",
            body=json.dumps(
                {
                    "error": {
                        "code": "review_conflict",
                        "message": "项目已在另一个窗口更新，请重新加载后再试。",
                    }
                },
                ensure_ascii=False,
            ),
        )

    page.route("**/api/review/decision", conflict)
    page.locator(_qa("review-reject")).click()
    _wait_for_eval(
        page,
        "window.__MS_EVENT_STUDIO__?.getState?.().workbench?.saving === false",
        timeout=5_000,
    )
    page.unroute("**/api/review/decision", conflict)
    after = _workbench_state(page)
    after_rows = _event_rows(page)
    _assert(after.get("selectedEventKey") == before.get("selectedEventKey"),
            "409 conflict did not restore the selected event")
    _assert(after.get("status") == before.get("status"),
            "409 conflict did not restore the previous review status")
    _assert(after_rows == before_rows, "409 conflict did not restore exact event-row state")
    alert = page.locator(_qa("review-error"))
    _assert(alert.count() == 1 and alert.is_visible(), "409 conflict lacks a visible review error")
    _assert(alert.get_attribute("role") == "alert", "review error is not announced as an alert")
    _assert(bool(alert.inner_text().strip()), "review error has no actionable message")
    _assert(after.get("saveState") == "error", "409 conflict is absent from the smoke hook")
    return {
        "ok": True,
        "selected_restored": True,
        "status_restored": after.get("status"),
        "rows_restored": True,
        "save_state": after.get("saveState"),
        "alert": alert.inner_text().strip(),
    }


def _check_real_review_flow(
    page: Any,
    base_url: str,
    *,
    reopen_project: Callable[[], Any],
    write_count: Callable[[], int],
) -> dict[str, Any]:
    initial = _goto_real_workspace(page, base_url)
    _assert(initial.get("status") == "unreviewed", "real project did not select an unreviewed event")
    _assert(initial.get("eventIndex") == 0, "real project did not select its first unreviewed event")
    initial_counts = _workspace_review_counts(page)
    _assert(initial_counts.get("unreviewed", 0) >= 2, "real project lacks auto-advance coverage")

    shortcut = _check_note_shortcut_isolation(page, write_count)
    saving = _check_saving_disabled_and_release(page, write_count)

    # Restore the initial state after the deliberately held loading request so
    # the click-to-next timing sample is not contaminated by QA interception.
    page.locator(_qa("undo")).click()
    _wait_for_eval(
        page,
        "arg => { const workbench = window.__MS_EVENT_STUDIO__?.getState?.().workbench; "
        "return workbench?.saving === false && workbench?.canRedo === true "
        "&& workbench?.selectedEventKey === arg; }",
        arg=initial.get("selectedEventKey"),
        timeout=5_000,
    )
    latency = _click_and_measure_auto_advance(page)
    history = _check_undo_redo(page)
    rollback = _check_conflict_rollback(page)
    decisions_and_clear = _check_review_decisions_and_clear(page)
    restore = _check_restore_automatic_status_preserved(page, write_count)
    committed_counts = _workspace_review_counts(page)
    _assert(committed_counts.get("accepted") == initial_counts.get("accepted", 0),
            "accepted total should replace the adjusted accepted event with event 1")
    _assert(committed_counts.get("rejected") == initial_counts.get("rejected", 0),
            "clear review did not remove the rejected decision")
    _assert(committed_counts.get("pending") == initial_counts.get("pending", 0) + 1,
            "pending decision was not committed")

    reopen_project()
    reopened = _goto_real_workspace(page, base_url)
    reopened_counts = _workspace_review_counts(page)
    _assert(reopened_counts == committed_counts,
            "review counts changed after reopening the scientific project")
    _assert(reopened_counts.get("accepted") == initial_counts.get("accepted", 0),
            "accepted review did not persist across reopen")
    return {
        "ok": True,
        "initial_first_unreviewed": True,
        "note_shortcut_isolation": shortcut,
        "saving_disabled": saving,
        "auto_advance": latency,
        "history": history,
        "failure_rollback": rollback,
        "u_a_r_p_and_clear": decisions_and_clear,
        "restore_automatic_apex": restore,
        "review_counts": {
            "initial": initial_counts,
            "committed": committed_counts,
            "reopened": reopened_counts,
        },
        "reopen_created_new_opaque_key_space": (
            reopened.get("selectedEventKey") != initial.get("selectedEventKey")
        ),
        "persistence_proven": True,
    }


def run_gate(
    *,
    base_url: str,
    stage: str = "r2",
    headed: bool = False,
    reopen_project: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required: pip install -e .[qa] && playwright install chromium"
        ) from exc

    checks: dict[str, Any] = {}
    requests: list[dict[str, str]] = []
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
                parsed = urlparse(request.url)
                if parsed.path.startswith("/api/"):
                    requests.append(
                        {
                            "page": str(page.url),
                            "method": request.method.upper(),
                            "path": parsed.path,
                        }
                    )

            page.on("request", record_request)

            checks["fixture_readiness"] = _check_fixture_readiness(page, base_url)
            checks["initial_selection_and_segmented_state"] = _check_initial_selection_and_status(
                page, base_url
            )
            checks["mouse_keyboard_and_previous_next"] = _check_selection_and_navigation(
                page, base_url
            )
            checks["filter_scale_and_labels"] = _check_view_controls(page, base_url)
            checks["core_and_collapsible_evidence"] = _check_evidence_layout(page, base_url)
            checks["marker_hover_focus_and_shape_legend"] = _check_marker_hover_focus_callout(
                page,
                base_url,
                lambda: sum(row["method"] in WRITE_METHODS for row in requests),
            )
            if stage == "r2":
                checks["r2_write_placeholders"] = _check_r2_disabled_write_placeholders(
                    page, base_url
                )
            checks["plot_geometry_and_zero_write_margins"] = _check_plot_geometry(
                page, base_url, lambda: sum(row["method"] in WRITE_METHODS for row in requests)
            )

            storage = _storage_evidence(page)
            _assert_no_browser_persistence(storage)
            writes = [row for row in requests if row["method"] in WRITE_METHODS]
            _assert(writes == [], f"R2 fixtures issued write requests: {writes}")
            checks["r2_zero_writes_and_browser_persistence"] = {
                "ok": True,
                # Freeze fixture-only evidence before the real R3 workspace
                # appends its intentional API traffic to the shared recorder.
                "api_requests": list(requests),
                "write_requests": writes,
                "storage": storage,
            }

            if stage in {"r3", "all"}:
                checks["r3_loading_and_error_fixtures"] = _check_r3_fixture_states(page, base_url)
                _assert(
                    reopen_project is not None,
                    "R3 requires an internally managed temporary scientific project",
                )
                checks["r3_real_review_flow"] = _check_real_review_flow(
                    page,
                    base_url,
                    reopen_project=reopen_project,
                    write_count=lambda: sum(
                        row["method"] in WRITE_METHODS for row in requests
                    ),
                )
                checks["r3_api_requests"] = {
                    "ok": True,
                    "requests": requests,
                    "write_count": sum(row["method"] in WRITE_METHODS for row in requests),
                }
        finally:
            context.close()
            browser.close()
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", help="optional already-running source/package server")
    parser.add_argument(
        "--stage",
        choices=("r2", "r3", "all"),
        default="r2",
        help="r2 checks fixture browsing; r3/all also exercise a temporary real project",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPOSITORY / "build/qa/ux-r2-r3-workbench.json",
    )
    parser.add_argument("--headed", action="store_true", help="show Chromium for local debugging")
    args = parser.parse_args(argv)

    report: dict[str, Any] = {
        "schema": "ms-event-studio-ux-r2-r3-workbench-qa-v1",
        "started_at": _utc_now(),
        "requested_stage": args.stage,
        "browser_viewport_is_css_pixels": True,
        "native_dpi_evidence": False,
        "status": "error",
    }
    server = None
    recent_root = None
    source_path: Path | None = None
    source_before: dict[str, Any] | None = None
    reopen_project: Callable[[], Any] | None = None
    try:
        base_url = args.base_url
        if base_url and args.stage in {"r3", "all"}:
            raise ValueError(
                "R3 must use the internally managed temporary project; --base-url is R2-only"
            )
        if not base_url:
            recent_root = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
            root = Path(recent_root.name)
            if args.stage in {"r3", "all"}:
                from ms_event_studio.web_app import WebSession, create_http_server

                source_path, project = _create_r3_scientific_project(root)
                session = WebSession(root / "recent_projects.json")
                selection = session.register_path("project_open", project.project_dir)
                project_token = selection["selection_token"]
                session.open_project(project_token)
                server = create_http_server(session=session)
                reopen_project = lambda: session.open_project(project_token)
                source_before = _file_fingerprint(source_path)
            else:
                from ms_event_studio.web_app import create_http_server

                server = create_http_server(
                    recent_path=root / "recent_projects.json"
                )
            server.start()
            base_url = server.url
        report["server_mode"] = "external" if args.base_url else "ephemeral-loopback"
        report["checks"] = run_gate(
            base_url=base_url,
            stage=args.stage,
            headed=args.headed,
            reopen_project=reopen_project,
        )
        if source_path is not None and source_before is not None:
            source_after = _file_fingerprint(source_path)
            _assert(source_after == source_before, "real review gate changed the raw MS source")
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
        if recent_root is not None:
            recent_root.cleanup()
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
