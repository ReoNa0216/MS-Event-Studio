"""Standalone Playwright interaction gate for the UX-R1 Web shell."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any
from urllib.parse import urlparse


REPOSITORY = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

FIXTURE_STATES = {
    "welcome": {"modal": None, "analysis": "idle"},
    "create-idle": {"modal": "create", "analysis": "idle"},
    "create-running": {"modal": "create", "analysis": "running"},
    "create-cancelling": {"modal": "create", "analysis": "cancelling"},
    "create-cancelled": {"modal": "create", "analysis": "cancelled"},
    "create-error": {"modal": "create", "analysis": "error"},
    "create-ready": {"modal": "create", "analysis": "ready"},
    "open": {"modal": "open", "analysis": "idle"},
}
READY_EXPRESSION = "window.__MS_EVENT_STUDIO__?.getState?.().ready === true"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _assert(condition: Any, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _tab_to(page: Any, selector: str, *, maximum: int = 30) -> int:
    for count in range(maximum + 1):
        if page.evaluate(
            "selector => document.activeElement === document.querySelector(selector)",
            selector,
        ):
            return count
        page.keyboard.press("Tab")
    raise AssertionError(f"keyboard Tab did not reach {selector} within {maximum} presses")


def _focus_evidence(page: Any, selector: str) -> dict[str, Any]:
    return page.eval_on_selector(
        selector,
        """element => {
          const style = getComputedStyle(element);
          return {
            id: element.id,
            focused: document.activeElement === element,
            focusVisible: element.matches(':focus-visible'),
            outlineStyle: style.outlineStyle,
            outlineWidth: style.outlineWidth,
            outlineColor: style.outlineColor
          };
        }""",
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


def _wait_ready(page: Any) -> dict[str, Any]:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if page.evaluate(READY_EXPRESSION):
                break
        except BaseException:
            pass
        time.sleep(0.02)
    else:
        raise AssertionError("frontend did not become ready")
    state = page.evaluate("window.__MS_EVENT_STUDIO__.getState()")
    _assert(isinstance(state, dict) and state.get("ready") is True, "frontend did not become ready")
    return state


def _visible_enabled_buttons(page: Any, dialog_selector: str) -> list[dict[str, str]]:
    return page.eval_on_selector_all(
        f"{dialog_selector} button",
        """buttons => buttons.filter(button => {
          const style = getComputedStyle(button);
          const visible = !button.hidden && style.display !== 'none' && style.visibility !== 'hidden';
          return visible
            && !button.matches(':disabled')
            && button.getAttribute('aria-disabled') !== 'true';
        }).map(button => ({id: button.id, text: button.textContent.trim()}))""",
    )


def run_gate(*, base_url: str, headed: bool = False) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required: pip install -e .[qa] && playwright install chromium"
        ) from exc

    checks: dict[str, Any] = {}
    fixture_requests: list[dict[str, str]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        context = browser.new_context(
            viewport={"width": 960, "height": 640},
            color_scheme="light",
            locale="zh-CN",
            device_scale_factor=1,
        )
        try:
            page = context.new_page()

            def record_request(request: Any) -> None:
                parsed = urlparse(request.url)
                if parsed.path.startswith("/api/"):
                    fixture_requests.append(
                        {"fixture": str(page.url), "method": request.method, "path": parsed.path}
                    )

            page.on("request", record_request)

            page.goto(f"{base_url.rstrip('/')}?fixture=welcome")
            state = _wait_ready(page)
            _assert(state.get("fixture") == "welcome", "welcome fixture identity was lost")
            tab_count = _tab_to(page, "#welcomeCreate")
            welcome_focus = _focus_evidence(page, "#welcomeCreate")
            _assert(welcome_focus["focused"], "new-project action is not keyboard focusable")
            _assert(welcome_focus["focusVisible"], "new-project action has no keyboard focus state")
            _assert(welcome_focus["outlineStyle"] != "none", "keyboard focus outline is absent")
            page.keyboard.press("Enter")
            page.locator("#createDialog").wait_for(state="visible")
            _assert(
                page.evaluate("document.querySelector('#createDialog').open"),
                "Enter did not open the new-project modal",
            )
            page.keyboard.press("Escape")
            _assert(
                not page.evaluate("document.querySelector('#createDialog').open"),
                "Escape did not close the idle new-project modal",
            )

            _tab_to(page, "#welcomeOpen")
            page.keyboard.press("Enter")
            page.locator("#openDialog").wait_for(state="visible")
            _assert(
                page.evaluate("document.querySelector('#openDialog').open"),
                "Enter did not open the open-project modal",
            )
            page.keyboard.press("Escape")
            _assert(
                not page.evaluate("document.querySelector('#openDialog').open"),
                "Escape did not close the open-project modal",
            )

            page.locator("#welcomeCreate").click()
            page.locator("#createDialog").wait_for(state="visible")
            page.locator("#closeCreate").click()
            _assert(
                not page.evaluate("document.querySelector('#createDialog').open"),
                "visible close control did not close the new-project modal",
            )
            checks["welcome_keyboard_and_modals"] = {
                "ok": True,
                "tab_presses_to_new": tab_count,
                "focus": welcome_focus,
                "escape_create": True,
                "escape_open": True,
                "close_button": True,
            }

            fixture_results: list[dict[str, Any]] = []
            for fixture, expected in FIXTURE_STATES.items():
                page.goto(f"{base_url.rstrip('/')}?fixture={fixture}")
                fixture_state = _wait_ready(page)
                _assert(fixture_state.get("fixture") == fixture, f"{fixture}: wrong fixture state")
                _assert(fixture_state.get("modal") == expected["modal"], f"{fixture}: wrong modal")
                _assert(
                    fixture_state.get("analysis", {}).get("state") == expected["analysis"],
                    f"{fixture}: wrong analysis state",
                )
                fixture_results.append(
                    {
                        "id": fixture,
                        "ready": True,
                        "modal": fixture_state.get("modal"),
                        "analysis": fixture_state.get("analysis", {}).get("state"),
                    }
                )
            checks["fixture_readiness"] = {"ok": True, "fixtures": fixture_results}

            page.goto(f"{base_url.rstrip('/')}?fixture=create-running")
            _wait_ready(page)
            cancel = page.locator("#cancelAnalysis")
            _assert(cancel.is_visible() and cancel.is_enabled(), "running state lacks an enabled cancel action")
            running_tabs = _tab_to(page, "#cancelAnalysis")
            running_focus = _focus_evidence(page, "#cancelAnalysis")
            _assert(running_focus["focusVisible"], "running cancel action has no keyboard focus state")
            _assert(
                page.locator("#analysisCard").get_attribute("aria-busy") == "true",
                "running analysis is not exposed as busy",
            )
            checks["running_cancel_focus"] = {
                "ok": True,
                "tab_presses": running_tabs,
                "focus": running_focus,
                "aria_busy": True,
            }

            page.goto(f"{base_url.rstrip('/')}?fixture=create-cancelling")
            _wait_ready(page)
            enabled = _visible_enabled_buttons(page, "#createDialog")
            _assert(enabled == [], f"cancelling state exposes false actions: {enabled}")
            _assert(
                page.locator("#analysisCard").get_attribute("aria-busy") == "true",
                "cancelling analysis is not exposed as busy",
            )
            _assert(
                page.locator("#cancelAnalysis").get_attribute("disabled") is not None,
                "cancelling action remains operable",
            )
            checks["cancelling_has_no_false_actions"] = {
                "ok": True,
                "enabled_visible_buttons": enabled,
                "aria_busy": True,
            }

            storage = _storage_evidence(page)
            _assert(storage["localStorageKeys"] == [], "fixture wrote localStorage")
            _assert(storage["sessionStorageKeys"] == [], "fixture wrote sessionStorage")
            _assert(storage["cookie"] == "", "fixture wrote a browser cookie")
            _assert(storage["indexedDatabases"] == [], "fixture wrote IndexedDB")
            _assert(storage["cacheKeys"] == [], "fixture wrote Cache Storage")
            writes = [row for row in fixture_requests if row["method"].upper() != "GET"]
            _assert(writes == [], f"fixture issued write requests: {writes}")
            checks["no_fixture_writes_or_persistence"] = {
                "ok": True,
                "api_requests": fixture_requests,
                "write_requests": writes,
                "storage": storage,
            }
        finally:
            context.close()
            browser.close()
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", help="optional already-running source/package server")
    parser.add_argument(
        "--report",
        type=Path,
        default=REPOSITORY / "build/qa/ux-r1-browser-interaction.json",
    )
    parser.add_argument("--headed", action="store_true", help="show Chromium for local debugging")
    args = parser.parse_args(argv)

    report: dict[str, Any] = {
        "schema": "ms-event-studio-ux-r1-browser-qa-v1",
        "started_at": _utc_now(),
        "viewport": {"width": 960, "height": 640, "device_scale_factor": 1},
        "status": "error",
    }
    server = None
    recent_root = None
    try:
        base_url = args.base_url
        if not base_url:
            from ms_event_studio.web_app import create_http_server

            recent_root = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
            server = create_http_server(
                recent_path=Path(recent_root.name) / "recent_projects.json"
            )
            server.start()
            base_url = server.url
        report["server_mode"] = "external" if args.base_url else "ephemeral-loopback"
        report["checks"] = run_gate(base_url=base_url, headed=args.headed)
        report["status"] = "ok"
        return_code = 0
    except BaseException as exc:
        report["failure"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
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
