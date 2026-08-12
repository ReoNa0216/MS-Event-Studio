"""Standalone browser QA gate for UX-R6 responsive copy and accessibility.

This gate intentionally stays outside the ordinary unittest suite because it
launches Chromium.  Its zoom rows are CSS/browser equivalents only; native
Windows WebView DPI and macOS Retina remain separate UX-R7 evidence.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any
from urllib.parse import urlparse


REPOSITORY = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

try:
    from capture_ui_matrix import EXPECTED_VIEWPORTS, load_and_validate_matrix
    from lint_ui_copy import FORBIDDEN_UI_TERMS
except ModuleNotFoundError:
    from scripts.capture_ui_matrix import EXPECTED_VIEWPORTS, load_and_validate_matrix
    from scripts.lint_ui_copy import FORBIDDEN_UI_TERMS


R6_FIXTURES = ("undo-empty", "undo-redo-ready", "long-chinese-copy")
R6_VIEWPORTS = (
    {"width": 960, "height": 640},
    {"width": 1366, "height": 768},
    {"width": 1920, "height": 1080},
)
EQUIVALENT_ZOOM_ROWS = (
    {"scale_percent": 125, "viewport": {"width": 1093, "height": 614}},
    {"scale_percent": 150, "viewport": {"width": 911, "height": 512}},
    {"scale_percent": 200, "viewport": {"width": 683, "height": 384}},
)
MIN_LONG_PROJECT_NAME = 107
READY_EXPRESSION = "window.__MS_EVENT_STUDIO__?.getState?.().ready === true"
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
FOCUS_SELECTORS = (
    '[data-qa="previous-event"]',
    '[data-qa="next-event"]',
    '[data-qa="review-accept"]',
    '[data-qa="review-reject"]',
    '[data-qa="review-pending"]',
    '[data-qa="evidence-toggle"]',
    '[data-qa="review-note"]',
    '[data-qa="event-filter"]',
    '[data-qa="undo"]',
    '[data-qa="redo"]',
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _assert(condition: Any, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _wait_ready(page: Any, fixture: str, *, timeout: int = 20_000) -> dict[str, Any]:
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        try:
            state = page.evaluate("window.__MS_EVENT_STUDIO__?.getState?.() ?? null")
            if (
                isinstance(state, dict)
                and state.get("ready") is True
                and state.get("fixture") == fixture
                and isinstance(state.get("workbench"), dict)
            ):
                return state
        except BaseException:
            pass
        time.sleep(0.02)
    raise AssertionError(f"{fixture}: frontend fixture did not become ready")


def _goto_fixture(page: Any, base_url: str, fixture: str) -> dict[str, Any]:
    page.goto(f"{base_url.rstrip('/')}?fixture={fixture}")
    return _wait_ready(page, fixture)


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


def _assert_no_storage(evidence: dict[str, Any]) -> None:
    _assert(evidence["localStorageKeys"] == [], "fixture wrote localStorage")
    _assert(evidence["sessionStorageKeys"] == [], "fixture wrote sessionStorage")
    _assert(evidence["cookie"] == "", "fixture wrote a cookie")
    _assert(evidence["indexedDatabases"] == [], "fixture wrote IndexedDB")
    _assert(evidence["cacheKeys"] == [], "fixture wrote Cache Storage")


def _semantic_control(page: Any, qa: str) -> dict[str, Any]:
    locator = page.locator(f'[data-qa="{qa}"]')
    _assert(locator.count() == 1, f"{qa}: expected one control")
    return locator.evaluate(
        """node => ({
          disabled: Boolean(node.disabled),
          ariaDisabled: node.getAttribute('aria-disabled'),
          visible: Boolean(node.getClientRects().length),
          tabIndex: node.tabIndex,
          label: (node.getAttribute('aria-label') || node.textContent || '').trim()
        })"""
    )


def _check_history_fixture(page: Any, fixture: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
    state = page.evaluate("window.__MS_EVENT_STUDIO__.getState()")
    workbench = state["workbench"]
    expected = fixture == "undo-redo-ready"
    _assert(workbench.get("canUndo") is expected, f"{fixture}: canUndo mismatch")
    _assert(workbench.get("canRedo") is expected, f"{fixture}: canRedo mismatch")
    undo = _semantic_control(page, "undo")
    redo = _semantic_control(page, "redo")
    for name, evidence in (("undo", undo), ("redo", redo)):
        _assert(evidence["visible"], f"{fixture}: {name} is not visible")
        _assert(bool(evidence["label"]), f"{fixture}: {name} has no accessible name")
        _assert(evidence["disabled"] is (not expected), f"{fixture}: {name} disabled mismatch")
        if expected:
            _assert(evidence["tabIndex"] >= 0, f"{fixture}: enabled {name} is not keyboard focusable")

    before = len(requests)
    if expected:
        page.locator('[data-qa="undo"]').click()
        page.locator('[data-qa="redo"]').focus()
        page.keyboard.press("Enter")
    _assert(len(requests) == before, f"{fixture}: history fixture issued an API request")
    return {"canUndo": workbench["canUndo"], "canRedo": workbench["canRedo"], "undo": undo, "redo": redo}


def _geometry(page: Any) -> dict[str, Any]:
    return page.evaluate(
        """() => {
          const visible = node => {
            if (!node || !node.getClientRects().length) return false;
            const style = getComputedStyle(node);
            return style.visibility !== 'hidden' && style.display !== 'none';
          };
          const metrics = node => {
            const box = node.getBoundingClientRect();
            const style = getComputedStyle(node);
            return {
              tag: node.tagName.toLowerCase(),
              qa: node.dataset.qa || null,
              text: (node.innerText || node.textContent || '').trim(),
              left: box.left, top: box.top, right: box.right, bottom: box.bottom,
              clientWidth: node.clientWidth, scrollWidth: node.scrollWidth,
              clientHeight: node.clientHeight, scrollHeight: node.scrollHeight,
              overflowX: style.overflowX, overflowY: style.overflowY,
              whiteSpace: style.whiteSpace, textOverflow: style.textOverflow,
            };
          };
          const header = document.querySelector('.app-header');
          const project = document.querySelector('[data-qa="project-name"]');
          const critical = [
            project,
            document.querySelector('[data-qa="review-error"]:not([hidden])'),
            document.querySelector('[data-qa="core-evidence"]'),
            document.querySelector('[data-qa="more-evidence"]:not([hidden])'),
            document.querySelector('[data-qa="review-note"]'),
            document.querySelector('[data-qa="export-target-name"]'),
            document.querySelector('[data-qa="export-result"]:not([hidden])'),
            document.querySelector('#exportResultMessage'),
            document.querySelector('#exportNote')
          ].filter(visible).map(metrics);
          return {
            viewport: { width: innerWidth, height: innerHeight },
            documentOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
            bodyOverflow: document.body.scrollWidth > document.body.clientWidth + 1,
            header: metrics(header),
            project: metrics(project),
            critical,
          };
        }"""
    )


def _assert_geometry(evidence: dict[str, Any], label: str) -> None:
    _assert(not evidence["documentOverflow"], f"{label}: document has horizontal overflow")
    _assert(not evidence["bodyOverflow"], f"{label}: body has horizontal overflow")
    viewport = evidence["viewport"]
    project = evidence["project"]
    header = evidence["header"]
    _assert(project["left"] >= -0.5 and project["right"] <= viewport["width"] + 0.5, f"{label}: project name escapes viewport")
    _assert(project["top"] >= header["top"] - 0.5 and project["bottom"] <= header["bottom"] + 0.5, f"{label}: project name escapes header")
    _assert(project["scrollWidth"] <= project["clientWidth"] + 1, f"{label}: project name clips horizontally")
    _assert(project["scrollHeight"] <= project["clientHeight"] + 1, f"{label}: project name clips vertically")
    for row in evidence["critical"]:
        _assert(row["left"] >= -0.5 and row["right"] <= viewport["width"] + 0.5, f"{label}: {row['qa']} escapes viewport")
        _assert(row["scrollWidth"] <= row["clientWidth"] + 1, f"{label}: {row['qa']} clips horizontally")
        if row["tag"] not in {"textarea"}:
            _assert(row["scrollHeight"] <= row["clientHeight"] + 1, f"{label}: {row['qa']} clips vertically")


def _assert_safe_dom(page: Any) -> dict[str, Any]:
    audit = page.evaluate(
        """arg => {
          const root = document.querySelector('[data-qa="workbench"]') || document.body;
          const visibleText = root.innerText || '';
          const attributes = Array.from(root.querySelectorAll('[aria-label],[title],[placeholder]'))
            .flatMap(node => ['aria-label','title','placeholder'].map(name => node.getAttribute(name)).filter(Boolean))
            .join('\\n');
          const copy = `${visibleText}\\n${attributes}`;
          const forbiddenCopy = arg.terms.filter(term => copy.toLowerCase().includes(term.toLowerCase()));
          const forbiddenData = [];
          for (const node of root.querySelectorAll('*')) {
            for (const name of node.getAttributeNames()) {
              if (name.startsWith('data-') && arg.dataNames.includes(name.slice(5).toLowerCase())) forbiddenData.push(name);
            }
          }
          return { forbiddenCopy: [...new Set(forbiddenCopy)], forbiddenData: [...new Set(forbiddenData)] };
        }""",
        {"terms": sorted(FORBIDDEN_UI_TERMS), "dataNames": list(FORBIDDEN_DOM_DATA)},
    )
    _assert(audit["forbiddenCopy"] == [], f"R6 DOM exposes forbidden copy: {audit}")
    _assert(audit["forbiddenData"] == [], f"R6 DOM exposes private data: {audit}")
    return audit


def _check_long_copy(page: Any, label: str) -> dict[str, Any]:
    project = page.locator('[data-qa="project-name"]').inner_text().strip()
    _assert(len(project) >= MIN_LONG_PROJECT_NAME, f"{label}: project name is shorter than {MIN_LONG_PROJECT_NAME}")
    _assert(bool(re.search(r"[\u3400-\u9fff]", project)), f"{label}: project name lacks CJK")
    _assert(bool(re.search(r"[A-Za-z]", project)), f"{label}: project name lacks ASCII Latin")
    error = page.locator('[data-qa="review-error"]')
    note = page.locator('[data-qa="review-note"]')
    quality = page.locator("#qualityNotes")
    _assert(error.is_visible() and len(error.inner_text().strip()) >= 40, f"{label}: long safe error is absent")
    _assert(len(note.get_attribute("placeholder") or "") >= 30, f"{label}: long note placeholder is absent")
    _assert(len(note.input_value()) >= 35, f"{label}: long note value is absent")
    _assert(quality.is_visible() and len(quality.inner_text().strip()) >= 60, f"{label}: long evidence notes are absent")
    return {
        "projectNameLength": len(project),
        "errorLength": len(error.inner_text().strip()),
        "placeholderLength": len(note.get_attribute("placeholder") or ""),
        "noteLength": len(note.input_value()),
        "qualityNotesLength": len(quality.inner_text().strip()),
    }


def _parse_rgb(value: str) -> tuple[float, float, float, float]:
    match = re.fullmatch(r"rgba?\(([^)]+)\)", value.replace(",", " ").replace("/", " "))
    if not match:
        raise AssertionError(f"unsupported computed color {value!r}")
    parts = [part for part in match.group(1).split() if part]
    _assert(len(parts) in {3, 4}, f"malformed computed color {value!r}")
    channels = tuple(float(part.rstrip("%")) / (100 if "%" in part else 255) for part in parts[:3])
    alpha = float(parts[3].rstrip("%")) / (100 if len(parts) == 4 and "%" in parts[3] else 1) if len(parts) == 4 else 1.0
    return channels[0], channels[1], channels[2], alpha


def _luminance(rgb: tuple[float, float, float]) -> float:
    linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in rgb]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(foreground: str, background: str) -> float:
    fg = _parse_rgb(foreground)
    bg = _parse_rgb(background)
    blended = tuple(fg[index] * fg[3] + bg[index] * (1 - fg[3]) for index in range(3))
    high, low = sorted((_luminance(blended), _luminance(bg[:3])), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _check_contrast(page: Any) -> dict[str, Any]:
    samples = page.evaluate(
        """() => {
          const selectors = [
            '[data-qa="project-name"]', '#selectedOrigin', '#reviewError',
            '#qualityNotes li', '.interaction-hint', '.review-note-section small',
            '#exportResultTitle', '#exportResultMessage', '#exportTargetName',
            '.selection-readout__label', '#exportDescription', '#exportTargetTitle + p'
          ];
          const opaqueBackground = node => {
            for (let current = node; current; current = current.parentElement) {
              const value = getComputedStyle(current).backgroundColor;
              if (value && !value.endsWith(', 0)') && value !== 'rgba(0, 0, 0, 0)') return value;
            }
            return 'rgb(255, 255, 255)';
          };
          return selectors.flatMap(selector => Array.from(document.querySelectorAll(selector))).filter(node => (
            node.getClientRects().length && (node.innerText || node.textContent || '').trim()
          )).map(node => {
            const style = getComputedStyle(node);
            return { selector: node.id ? `#${node.id}` : node.className || node.tagName, text: (node.innerText || node.textContent || '').trim(), color: style.color, background: opaqueBackground(node), fontSize: parseFloat(style.fontSize), fontWeight: parseInt(style.fontWeight, 10) || 400 };
          });
        }"""
    )
    _assert(samples, "contrast gate found no visible text samples")
    results = []
    for sample in samples:
        ratio = _contrast(sample["color"], sample["background"])
        large = sample["fontSize"] >= 24 or (sample["fontSize"] >= 18.66 and sample["fontWeight"] >= 700)
        required = 3.0 if large else 4.5
        _assert(ratio + 0.01 >= required, f"contrast {ratio:.2f} < {required} for {sample['selector']}: {sample['text']!r}")
        results.append({**sample, "ratio": round(ratio, 3), "required": required})
    return {"samples": results, "minimumRatio": min(row["ratio"] for row in results)}


def _tab_cycle(page: Any, *, modal_only: bool) -> list[dict[str, Any]]:
    page.evaluate("document.activeElement?.blur?.()")
    visits: list[dict[str, Any]] = []
    seen: set[str] = set()
    first_key: str | None = None
    for _ in range(120):
        page.keyboard.press("Tab")
        evidence = page.evaluate(
            """() => {
              const node = document.activeElement;
              if (!node) return null;
              const style = getComputedStyle(node);
              const candidates = Array.from(document.querySelectorAll('a[href],button,input,select,textarea,summary,[tabindex]'));
              const dialog = document.querySelector('dialog[open]');
              return {
                id: node.id || '', qa: node.dataset?.qa || '', tag: node.tagName.toLowerCase(),
                key: `${node.tagName.toLowerCase()}#${node.id || ''}[${node.dataset?.qa || ''}]@${candidates.indexOf(node)}`,
                disabled: Boolean(node.disabled), focusVisible: node.matches(':focus-visible'),
                outlineStyle: style.outlineStyle, outlineWidth: parseFloat(style.outlineWidth) || 0,
                left: node.getBoundingClientRect().left, top: node.getBoundingClientRect().top,
                insideModal: Boolean(dialog?.contains(node)), modalOpen: Boolean(dialog)
              };
            }"""
        )
        _assert(evidence is not None, "Tab produced no active element")
        if evidence["tag"] == "body":
            continue
        key = evidence["key"]
        if first_key is None:
            first_key = key
        elif key == first_key:
            break
        _assert(key not in seen, f"Tab cycle repeated {key} before returning to its start")
        seen.add(key)
        if modal_only:
            _assert(evidence["modalOpen"] and evidence["insideModal"], f"modal Tab focus escaped to {key}")
        else:
            _assert(not evidence["modalOpen"], f"background Tab audit ran while a modal remained open: {key}")
        _assert(not evidence["disabled"], f"disabled control entered Tab order: {key}")
        _assert(evidence["focusVisible"], f"Tab focus is not visible: {key}")
        _assert(evidence["outlineStyle"] not in {"", "none"} and evidence["outlineWidth"] >= 2, f"Tab focus lacks >=2px outline: {key}")
        visits.append(evidence)
    _assert(len(visits) >= 2, "Tab cycle did not expose at least two keyboard targets")
    _assert(first_key is not None and page.evaluate(
        """key => { const node = document.activeElement; const candidates = Array.from(document.querySelectorAll('a[href],button,input,select,textarea,summary,[tabindex]')); return node && `${node.tagName.toLowerCase()}#${node.id || ''}[${node.dataset?.qa || ''}]@${candidates.indexOf(node)}` === key; }""",
        first_key,
    ), "Tab order did not cycle back to its first control")
    return visits


def _check_tab_order(page: Any) -> dict[str, Any]:
    modal = page.locator("dialog[open]")
    modal_order: list[dict[str, Any]] = []
    if modal.count():
        modal_order = _tab_cycle(page, modal_only=True)
        page.keyboard.press("Escape")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and page.locator("dialog[open]").count():
            time.sleep(0.02)
        _assert(page.locator("dialog[open]").count() == 0, "Escape did not close the completed fixture dialog")

    visits = _tab_cycle(page, modal_only=False)
    order = [row["qa"] or row["id"] for row in visits]
    for selector in FOCUS_SELECTORS:
        node = page.locator(selector)
        if node.count() != 1 or not node.is_visible() or node.is_disabled():
            continue
        qa = selector.split('"')[1]
        _assert(qa in order, f"enabled visible control {qa} was skipped by Tab")
    positions = {name: order.index(name) for name in order}
    for left, right in (("previous-event", "next-event"), ("review-accept", "review-reject"), ("review-reject", "review-pending"), ("undo", "redo")):
        if left in positions and right in positions:
            _assert(positions[left] < positions[right], f"Tab order reversed: {left} after {right}")
    return {
        "modalOrder": [row["qa"] or row["id"] for row in modal_order],
        "modalCount": len(modal_order),
        "backgroundOrder": order,
        "backgroundCount": len(visits),
        "focusContainedWhileModalOpen": True,
    }


def run_gate(*, base_url: str, report_path: Path) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required: pip install -e .[qa] && playwright install chromium") from exc

    started = _utc_now()
    requests: list[dict[str, Any]] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    checks: dict[str, Any] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 960, "height": 640}, locale="zh-CN", color_scheme="light")
        page = context.new_page()
        page.on("request", lambda request: requests.append({"method": request.method, "path": urlparse(request.url).path}) if "/api/" in request.url else None)
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        try:
            fixture_rows = []
            for fixture in R6_FIXTURES:
                before = len(requests)
                state = _goto_fixture(page, base_url, fixture)
                row: dict[str, Any] = {"fixture": fixture, "domSafety": _assert_safe_dom(page)}
                if fixture.startswith("undo-"):
                    row["history"] = _check_history_fixture(page, fixture, requests)
                else:
                    row["copy"] = _check_long_copy(page, fixture)
                    row["contrast"] = _check_contrast(page)
                    row["tabOrder"] = _check_tab_order(page)
                _assert(len(requests) == before, f"{fixture}: fixture made an API request")
                _assert_no_storage(_storage_evidence(page))
                row["state"] = {"fixture": state["fixture"], "canUndo": state["workbench"]["canUndo"], "canRedo": state["workbench"]["canRedo"]}
                fixture_rows.append(row)
            checks["fixture_contract"] = {"ok": True, "rows": fixture_rows, "apiRequests": 0, "browserPersistence": 0}

            geometry_rows = []
            for viewport in R6_VIEWPORTS:
                page.set_viewport_size(viewport)
                for fixture in R6_FIXTURES:
                    before = len(requests)
                    _goto_fixture(page, base_url, fixture)
                    evidence = _geometry(page)
                    _assert_geometry(evidence, f"{fixture}@{viewport['width']}x{viewport['height']}")
                    if fixture == "long-chinese-copy":
                        _check_long_copy(page, fixture)
                    _assert(len(requests) == before, f"{fixture}@{viewport['width']}: fixture made a request")
                    geometry_rows.append({"fixture": fixture, "viewport": viewport, "audit": evidence})
            checks["standard_reflow"] = {"ok": True, "rows": geometry_rows, "browserCssPixels": True}

            zoom_rows = []
            for zoom in EQUIVALENT_ZOOM_ROWS:
                page.set_viewport_size(zoom["viewport"])
                before = len(requests)
                _goto_fixture(page, base_url, "long-chinese-copy")
                evidence = _geometry(page)
                label = f"long-chinese-copy@equivalent-{zoom['scale_percent']}%"
                _assert_geometry(evidence, label)
                _check_long_copy(page, label)
                _assert(len(requests) == before, f"{label}: fixture made a request")
                zoom_rows.append({**zoom, "audit": evidence, "nativeDpiEvidence": False})
            checks["equivalent_zoom_reflow"] = {
                "ok": True,
                "rows": zoom_rows,
                "nativeDpiEvidence": False,
                "note": "Inverse CSS viewport reflow proxy only; not native WebView DPI evidence.",
            }

            _assert(console_errors == [], f"application console errors: {console_errors}")
            _assert(page_errors == [], f"page errors: {page_errors}")
            checks["runtime_errors"] = {"ok": True, "consoleErrors": console_errors, "pageErrors": page_errors}
        finally:
            context.close()
            browser.close()

    report = {
        "schema": "ms-event-studio-ux-r6-accessibility-qa-v1",
        "started_at": started,
        "finished_at": _utc_now(),
        "status": "ok",
        "server_mode": "ephemeral-loopback" if "127.0.0.1" in base_url else "external",
        "browser_viewport_is_css_pixels": True,
        "native_dpi_evidence": False,
        "checks": checks,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url")
    parser.add_argument("--report", type=Path, default=REPOSITORY / "build/qa/ux-r6-accessibility.json")
    args = parser.parse_args(argv)
    matrix = load_and_validate_matrix(REPOSITORY / "qa/screenshot_matrix.json")
    by_id = {row["id"]: row for row in matrix["scenarios"]}
    for fixture in R6_FIXTURES:
        _assert(by_id[fixture].get("automation") == "browser", f"{fixture}: matrix row is not browser")
        _assert(by_id[fixture].get("path") == f"/?fixture={fixture}", f"{fixture}: matrix path mismatch")
    _assert({(row["width"], row["height"]) for row in R6_VIEWPORTS} == EXPECTED_VIEWPORTS, "R6 viewports drifted")

    server = None
    recent_root = None
    try:
        base_url = args.base_url
        if not base_url:
            from ms_event_studio.web_app import create_http_server

            recent_root = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
            server = create_http_server(recent_path=Path(recent_root.name) / "recent_projects.json")
            server.start()
            base_url = server.url
        report = run_gate(base_url=base_url, report_path=args.report)
    finally:
        if server is not None:
            server.stop()
        if recent_root is not None:
            recent_root.cleanup()
    print(json.dumps({"status": report["status"], "report": str(args.report)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
