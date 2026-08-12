"""Validate or capture the standard browser screenshot matrix.

Native Windows DPI and macOS Retina rows remain separately recorded because a
Chromium viewport is not evidence for native WebView scaling.  ``--require-all``
is the pre-UAT gate: it refuses to run while any configured browser or native
sample is still marked planned.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

try:
    from lint_ui_copy import FORBIDDEN_UI_TERMS
except ModuleNotFoundError:  # Imported as ``scripts.capture_ui_matrix`` in tests.
    from scripts.lint_ui_copy import FORBIDDEN_UI_TERMS


REPOSITORY = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


EXPECTED_VIEWPORTS = {(960, 640), (1366, 768), (1920, 1080)}
EXPECTED_WINDOWS_SCALES = {100, 125, 150, 200}
REQUIRED_SCENARIOS = {
    "welcome",
    "create-idle",
    "create-running",
    "create-cancelling",
    "create-cancelled",
    "create-error",
    "create-ready",
    "open",
    "review-no-selection",
    "review-unreviewed-auto",
    "review-accepted-auto",
    "review-rejected-auto",
    "review-pending-auto",
    "review-manual",
    "save-in-progress",
    "save-failed",
    "add-aim",
    "add-preview",
    "adjust-aim",
    "adjust-preview",
    "edit-out-of-range",
    "undo-empty",
    "undo-redo-ready",
    "range-input",
    "range-calculating",
    "range-preview",
    "range-applying",
    "range-error",
    "export-review-results",
    "export-audit-package",
    "exporting",
    "export-error",
    "chart-highest-peak",
    "chart-edge-peak",
    "chart-dense-peaks",
    "long-chinese-copy",
}
UX_R2_R3_SCENARIOS = {
    "review-no-selection",
    "review-unreviewed-auto",
    "review-accepted-auto",
    "review-rejected-auto",
    "review-pending-auto",
    "review-manual",
    "save-in-progress",
    "save-failed",
}
UX_R2_GEOMETRY_SCENARIOS = {
    "chart-highest-peak",
    "chart-edge-peak",
    "chart-dense-peaks",
}
UX_R2_R3_BROWSER_SCENARIOS = UX_R2_R3_SCENARIOS | UX_R2_GEOMETRY_SCENARIOS
UX_R4_BROWSER_SCENARIOS = {
    "add-aim",
    "add-preview",
    "adjust-aim",
    "adjust-preview",
    "edit-out-of-range",
    "undo-empty",
    "undo-redo-ready",
}
UX_R5_BROWSER_SCENARIOS = {
    "range-input",
    "range-calculating",
    "range-preview",
    "range-applying",
    "range-error",
    "export-review-results",
    "export-audit-package",
    "exporting",
    "export-error",
}
UX_R6_BROWSER_SCENARIOS = {"long-chinese-copy"}
REQUIRED_BROWSER_SCENARIOS = (
    UX_R2_R3_BROWSER_SCENARIOS
    | UX_R4_BROWSER_SCENARIOS
    | UX_R5_BROWSER_SCENARIOS
    | UX_R6_BROWSER_SCENARIOS
)


def fixture_id_from_path(path: object) -> str | None:
    """Return the sole screenshot fixture encoded by a matrix path."""

    if not isinstance(path, str) or not path:
        return None
    parsed = urlparse(path)
    values = parse_qs(parsed.query, keep_blank_values=True).get("fixture", [])
    if parsed.path not in {"", "/"} or len(values) != 1 or not values[0]:
        return None
    return values[0]


def load_and_validate_matrix(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if payload.get("schema") != "ms-event-studio-screenshot-matrix-v1":
        errors.append("unexpected matrix schema")
    if payload.get("renderer") != "pywebview-html-css-svg":
        errors.append("matrix renderer must be the Phase 2R WebView renderer")
    browser = payload.get("browser")
    if not isinstance(browser, dict):
        errors.append("browser settings are missing")
        browser = {}
    viewports = browser.get("viewports", [])
    actual_viewports = {
        (row.get("width"), row.get("height")) for row in viewports if isinstance(row, dict)
    }
    if actual_viewports != EXPECTED_VIEWPORTS:
        errors.append("browser viewports must be 960x640, 1366x768, and 1920x1080")
    scenarios = payload.get("scenarios", [])
    scenario_ids = [row.get("id") for row in scenarios if isinstance(row, dict)]
    if len(scenario_ids) != len(set(scenario_ids)):
        errors.append("scenario ids must be unique")
    missing_scenarios = sorted(REQUIRED_SCENARIOS - set(scenario_ids))
    if missing_scenarios:
        errors.append("missing scenarios: " + ", ".join(missing_scenarios))
    for row in scenarios:
        if not isinstance(row, dict) or row.get("automation") not in {"browser", "planned"}:
            errors.append("every scenario must declare browser or planned automation")
            break
        declared_fixture = row.get("fixture", row.get("id"))
        if row.get("automation") == "browser" and fixture_id_from_path(
            row.get("path")
        ) != declared_fixture:
            errors.append(
                f"browser scenario {row.get('id')!r} must use its declared fixture path"
            )
    scenario_by_id = {
        row.get("id"): row for row in scenarios if isinstance(row, dict) and row.get("id")
    }
    inactive_required = sorted(
        scenario_id
        for scenario_id in REQUIRED_BROWSER_SCENARIOS
        if scenario_by_id.get(scenario_id, {}).get("automation") != "browser"
    )
    if inactive_required:
        errors.append(
            "implemented UX scenarios must be browser fixtures: "
            + ", ".join(inactive_required)
        )
    native = payload.get("native_samples", {})
    windows = native.get("windows", []) if isinstance(native, dict) else []
    if {row.get("scale_percent") for row in windows if isinstance(row, dict)} != EXPECTED_WINDOWS_SCALES:
        errors.append("native Windows samples must include 100/125/150/200%")
    macos = native.get("macos", []) if isinstance(native, dict) else []
    if not any(row.get("scale") == "retina-native" for row in macos if isinstance(row, dict)):
        errors.append("native macOS samples must include Retina")
    if errors:
        raise ValueError("; ".join(errors))
    return payload


def incomplete_rows(matrix: dict[str, Any]) -> list[str]:
    rows = [
        f"scenario:{row['id']}"
        for row in matrix["scenarios"]
        if row.get("automation") == "planned"
    ]
    for platform_name, samples in matrix["native_samples"].items():
        for sample in samples:
            if sample.get("status") != "captured":
                scale = sample.get("scale_percent", sample.get("scale"))
                rows.append(f"native:{platform_name}:{scale}")
    return rows


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", value.casefold()).strip("-")


def _wait_for_ready(page: Any, *, timeout: int = 20_000) -> None:
    """Poll the read-only hook without page-side eval rejected by strict CSP."""

    deadline = time.monotonic() + timeout / 1_000
    while time.monotonic() < deadline:
        try:
            if page.evaluate(
                "window.__MS_EVENT_STUDIO__?.getState?.().ready === true"
            ):
                return
        except BaseException:
            pass
        time.sleep(0.02)
    raise RuntimeError("frontend did not become ready before screenshot capture")


def capture_browser_rows(
    matrix: dict[str, Any],
    *,
    base_url: str,
    output: Path,
    stages: set[str] | None = None,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required for capture: pip install -e .[qa] && "
            "playwright install chromium"
        ) from exc

    output.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    automated = [
        row
        for row in matrix["scenarios"]
        if row["automation"] == "browser"
        and (stages is None or row.get("stage") in stages)
    ]
    if not automated:
        selected = "all" if stages is None else ", ".join(sorted(stages))
        raise ValueError(f"no browser screenshot rows selected for stages: {selected}")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for viewport in matrix["browser"]["viewports"]:
                context = browser.new_context(
                    viewport={"width": viewport["width"], "height": viewport["height"]},
                    color_scheme=matrix["browser"]["color_scheme"],
                    locale=matrix["browser"]["locale"],
                    device_scale_factor=1,
                )
                try:
                    for scenario in automated:
                        page = context.new_page()
                        page.goto(urljoin(base_url.rstrip("/") + "/", scenario["path"].lstrip("/")))
                        _wait_for_ready(page)
                        expected_fixture = scenario.get(
                            "fixture", fixture_id_from_path(scenario["path"])
                        )
                        fixture_state = page.evaluate(
                            "window.__MS_EVENT_STUDIO__?.getState?.() ?? null"
                        )
                        if (
                            not isinstance(fixture_state, dict)
                            or fixture_state.get("ready") is not True
                            or fixture_state.get("fixture") != expected_fixture
                        ):
                            raise RuntimeError(
                                f"fixture identity gate failed for {scenario['id']}: "
                                f"expected {expected_fixture!r}, got {fixture_state!r}"
                            )
                        audit = page.evaluate(
                            """(terms) => {
                              const named = el => (el.getAttribute('aria-label') || el.textContent || '').trim();
                              const ids = Array.from(document.querySelectorAll('[id]')).map(el => el.id);
                              const duplicateIds = [...new Set(ids.filter((id, i) => ids.indexOf(id) !== i))];
                              const unnamedButtons = Array.from(document.querySelectorAll('button')).filter(el => !named(el)).length;
                              const unlabeledInputs = Array.from(document.querySelectorAll('input, select, textarea')).filter(el => {
                                const id = el.id;
                                return !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby') && !(id && document.querySelector(`label[for="${CSS.escape(id)}"]`));
                              }).length;
                              const visibleCopy = document.body.innerText + '\\n' + Array.from(document.querySelectorAll('[aria-label],[title],[placeholder]')).map(el => [el.getAttribute('aria-label'), el.getAttribute('title'), el.getAttribute('placeholder')].filter(Boolean).join(' ')).join('\\n');
                              return {
                                duplicateIds,
                                unnamedButtons,
                                unlabeledInputs,
                                horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
                                forbiddenTerms: terms.filter(term => visibleCopy.toLocaleLowerCase().includes(term.toLocaleLowerCase()))
                              };
                            }""",
                            list(FORBIDDEN_UI_TERMS),
                        )
                        if (
                            audit["duplicateIds"]
                            or audit["unnamedButtons"]
                            or audit["unlabeledInputs"]
                            or audit["horizontalOverflow"]
                            or audit["forbiddenTerms"]
                        ):
                            raise RuntimeError(f"DOM/a11y gate failed for {scenario['id']}: {audit}")
                        name = f"{_slug(scenario['id'])}--{viewport['width']}x{viewport['height']}.png"
                        screenshot = output / name
                        page.screenshot(path=str(screenshot), full_page=False, animations="disabled")
                        rows.append(
                            {
                                "scenario": scenario["id"],
                                "fixture": fixture_state.get("fixture"),
                                "viewport": f"{viewport['width']}x{viewport['height']}",
                                "path": screenshot.name,
                                "dom_audit": audit,
                            }
                        )
                        page.close()
                finally:
                    context.close()
        finally:
            browser.close()
    report = {
        "schema": "ms-event-studio-browser-screenshots-v1",
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        ),
        "base_url": base_url,
        "stages": sorted(stages) if stages is not None else "all-browser-stages",
        "rows": rows,
        "native_samples_are_separate": True,
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    repository = REPOSITORY
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--matrix",
        type=Path,
        default=repository / "qa/screenshot_matrix.json",
    )
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--require-all", action="store_true")
    parser.add_argument("--base-url")
    parser.add_argument(
        "--stage",
        action="append",
        help="capture one matrix stage (for example UX-R2); repeat to select several",
    )
    parser.add_argument("--output", type=Path, default=repository / "build/qa/screenshots")
    args = parser.parse_args(argv)

    matrix = load_and_validate_matrix(args.matrix)
    incomplete = incomplete_rows(matrix)
    if args.list:
        for row in matrix["scenarios"]:
            print(f"{row['stage']}\t{row['automation']}\t{row['id']}")
        for item in incomplete:
            print(f"PENDING\t{item}")
    if args.require_all and incomplete:
        parser.error(
            "pre-UAT screenshot matrix is incomplete: " + ", ".join(incomplete)
        )
    if args.validate_only or args.list:
        print(
            f"matrix valid: {len(matrix['scenarios'])} scenarios, "
            f"{len(matrix['browser']['viewports'])} browser viewports"
        )
        return 0
    server = None
    recent_root = None
    base_url = args.base_url
    try:
        if not base_url:
            from ms_event_studio.web_app import create_http_server

            recent_root = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
            server = create_http_server(
                recent_path=Path(recent_root.name) / "recent_projects.json"
            )
            server.start()
            base_url = server.url
        report = capture_browser_rows(
            matrix,
            base_url=base_url,
            output=args.output,
            stages=set(args.stage) if args.stage else None,
        )
    finally:
        if server is not None:
            server.stop()
        if recent_root is not None:
            recent_root.cleanup()
    print(json.dumps({"rows": len(report["rows"]), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
