from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

from scripts.capture_ui_matrix import (
    EXPECTED_VIEWPORTS,
    EXPECTED_WINDOWS_SCALES,
    REQUIRED_SCENARIOS,
    UX_R2_R3_BROWSER_SCENARIOS,
    UX_R4_BROWSER_SCENARIOS,
    UX_R5_BROWSER_SCENARIOS,
    UX_R6_BROWSER_SCENARIOS,
    incomplete_rows,
    load_and_validate_matrix,
)
from scripts.lint_ui_copy import find_forbidden_terms, visible_html_copy


REPOSITORY = Path(__file__).resolve().parents[1]
WEB_ROOT = REPOSITORY / "src/ms_event_studio/web"


class StructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.start_tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.start_tags.append((tag.casefold(), dict(attrs)))


def relative_luminance(color: str) -> float:
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(first: str, second: str) -> float:
    high, low = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class UxR0R1QaContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.html_path = WEB_ROOT / "index.html"
        self.tokens_path = WEB_ROOT / "tokens.css"
        self.css_path = WEB_ROOT / "app.css"
        self.js_path = WEB_ROOT / "app.js"
        for path in (self.html_path, self.tokens_path, self.css_path, self.js_path):
            self.assertTrue(path.is_file(), f"missing Phase 2R Web asset: {path}")
        self.html = self.html_path.read_text(encoding="utf-8")
        self.tokens = self.tokens_path.read_text(encoding="utf-8")
        self.css = self.css_path.read_text(encoding="utf-8")
        self.js = self.js_path.read_text(encoding="utf-8")

    def test_document_has_chinese_landmarks_named_controls_and_live_feedback(self):
        parser = StructureParser()
        parser.feed(self.html)
        html_nodes = [attrs for tag, attrs in parser.start_tags if tag == "html"]
        self.assertEqual(len(html_nodes), 1)
        self.assertEqual(html_nodes[0].get("lang"), "zh-CN")
        viewport = [
            attrs
            for tag, attrs in parser.start_tags
            if tag == "meta" and attrs.get("name") == "viewport"
        ]
        self.assertEqual(len(viewport), 1)
        self.assertIn("width=device-width", viewport[0].get("content", ""))
        self.assertTrue(any(tag == "main" for tag, _attrs in parser.start_tags))
        self.assertTrue(
            any(
                attrs.get("role") == "status" and attrs.get("aria-live") in {"polite", "assertive"}
                for _tag, attrs in parser.start_tags
            ),
            "async progress/success/failure must have a live status region",
        )

        for match in re.finditer(r"<button\b(?P<attrs>[^>]*)>(?P<body>[\s\S]*?)</button>", self.html):
            attributes = match.group("attrs")
            visible = re.sub(r"<[^>]+>", "", match.group("body")).strip()
            self.assertTrue(visible or re.search(r"aria-label\s*=", attributes), match.group(0))

        ids = [attrs["id"] for _tag, attrs in parser.start_tags if attrs.get("id")]
        self.assertEqual(len(ids), len(set(ids)), "DOM ids must be unique")

    def test_static_visible_copy_uses_plain_chinese_and_forbids_backend_terms(self):
        copy = visible_html_copy(self.html)
        self.assertEqual(find_forbidden_terms(copy), [])
        self.assertIn("MS Event Studio", copy)
        self.assertRegex(copy, r"新建|创建")
        self.assertRegex(copy, r"打开")
        self.assertNotIn("localStorage", self.js)
        self.assertNotIn("sessionStorage", self.js)

    def test_shared_tokens_focus_and_motion_guards_are_present(self):
        for color in (
            "#f6f7f9",
            "#ffffff",
            "#1b1f27",
            "#667085",
            "#d7dce3",
            "#067647",
            "#b42318",
            "#b54708",
        ):
            self.assertIn(color, self.tokens.casefold())
        self.assertIn('<link rel="stylesheet" href="./tokens.css">', self.html)
        self.assertIn(":root", self.tokens)
        self.assertIn(":focus-visible", self.css)
        self.assertIn("prefers-reduced-motion", self.css)
        self.assertRegex(self.tokens, r"--font-family-[\w-]+\s*:\s*Arial")
        focus = re.search(r"--color-focus\s*:\s*(#[0-9a-fA-F]{6})", self.tokens)
        self.assertIsNotNone(focus)
        for background in ("#ffffff", "#f6f7f9", "#111827"):
            with self.subTest(focus_background=background):
                self.assertGreaterEqual(contrast_ratio(focus.group(1), background), 3.0)
        for foreground in ("#1b1f27", "#667085", "#067647", "#b42318", "#b54708"):
            with self.subTest(foreground=foreground):
                self.assertGreaterEqual(contrast_ratio(foreground, "#ffffff"), 4.5)
        scattered = set(
            color.casefold()
            for color in re.findall(r"#[0-9a-fA-F]{6}\b", self.css)
            if color.casefold()
            in {"#f6f7f9", "#ffffff", "#1b1f27", "#667085", "#d7dce3"}
        )
        self.assertEqual(scattered, set(), "core design colors belong only in tokens.css")

    def test_frontend_exposes_read_only_ready_and_state_smoke_hook(self):
        self.assertIn("__MS_EVENT_STUDIO__", self.js)
        self.assertRegex(self.js, r"\bready\b")
        self.assertRegex(self.js, r"\bgetState\b")
        self.assertRegex(self.js, r"aria-busy")

    def test_standard_matrix_is_complete_except_for_macos_retina(self):
        matrix = load_and_validate_matrix(REPOSITORY / "qa/screenshot_matrix.json")
        self.assertEqual(
            {(row["width"], row["height"]) for row in matrix["browser"]["viewports"]},
            EXPECTED_VIEWPORTS,
        )
        self.assertEqual(
            {row["scale_percent"] for row in matrix["native_samples"]["windows"]},
            EXPECTED_WINDOWS_SCALES,
        )
        self.assertTrue(REQUIRED_SCENARIOS.issubset({row["id"] for row in matrix["scenarios"]}))
        pending = incomplete_rows(matrix)
        self.assertNotIn("scenario:create-idle", pending)
        self.assertNotIn("scenario:create-running", pending)
        self.assertNotIn("scenario:create-cancelling", pending)
        self.assertNotIn("scenario:open", pending)
        for scenario_id in UX_R2_R3_BROWSER_SCENARIOS:
            with self.subTest(scenario_id=scenario_id):
                self.assertNotIn(f"scenario:{scenario_id}", pending)
        for scenario_id in UX_R4_BROWSER_SCENARIOS:
            with self.subTest(scenario_id=scenario_id):
                self.assertNotIn(f"scenario:{scenario_id}", pending)
        for scenario_id in UX_R5_BROWSER_SCENARIOS | UX_R6_BROWSER_SCENARIOS:
            with self.subTest(scenario_id=scenario_id):
                self.assertNotIn(f"scenario:{scenario_id}", pending)
        self.assertEqual(
            [row["automation"] for row in matrix["scenarios"]],
            ["browser"] * len(REQUIRED_SCENARIOS),
        )
        native_windows = {
            row["scale_percent"]: row for row in matrix["native_samples"]["windows"]
        }
        captured_evidence = {
            100: "build/qa/user-feedback-final-native-agent-4331a5b-foreground-run2/report.json",
            125: "build/qa/user-feedback-final-native-4331a5b-125pct/report.json",
            150: "build/qa/user-feedback-final-native-4331a5b-restored-150pct/report.json",
            200: "build/qa/user-feedback-final-native-4331a5b-200pct/report.json",
        }
        for scale, evidence in captured_evidence.items():
            with self.subTest(native_windows_scale=scale):
                self.assertEqual(native_windows[scale]["status"], "captured")
                self.assertEqual(native_windows[scale]["evidence"], evidence)
                self.assertNotIn(f"native:windows:{scale}", pending)
        self.assertEqual(pending, ["native:macos:retina-native"])

    def test_copy_linter_targets_visible_dom_not_private_api_fields(self):
        fixture = """
          <main aria-label="创建项目">欢迎</main>
          <script>const revision = payload.revision; const manifest = payload.manifest;</script>
        """
        self.assertEqual(find_forbidden_terms(visible_html_copy(fixture)), [])
        self.assertIn("revision", find_forbidden_terms("界面 revision"))

    def test_windows_native_gate_uses_physical_dpi_and_logical_outer_minimum(self):
        script = (REPOSITORY / "scripts/capture_windows_native_qa.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("MINIMUM_WINDOW_LOGICAL = (960, 640)", script)
        self.assertIn("GetDpiForWindow", script)
        self.assertIn("connect_over_cdp", script)
        self.assertIn("ImageGrab.grab", script)
        self.assertIn('"native_dpi_evidence": True', script)
        self.assertIn('"browser_emulation": False', script)
        self.assertIn('"logical_outer_minimum_preserved": True', script)
        self.assertIn("REQUIRED_STATE_ACTIONS", script)
        self.assertNotIn("device_scale_factor", script)


if __name__ == "__main__":
    unittest.main()
