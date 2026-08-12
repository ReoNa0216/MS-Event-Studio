from __future__ import annotations

from pathlib import Path
import unittest

from scripts.capture_ui_matrix import fixture_id_from_path, load_and_validate_matrix
from scripts.run_ux_r4_event_edit_qa import (
    EDIT_ENDPOINTS,
    FORBIDDEN_DOM_DATA,
    R4_FIXTURES,
    R4_STANDARD_VIEWPORTS,
)
from scripts.run_ux_r4_narrow_support_qa import (
    KEYBOARD_CANDIDATE_SEC,
    MAX_CANONICAL_SUPPORT_SEC,
    MIN_POINTER_HIT_CSS_PX,
    ORIGINAL_APEX_SEC,
    VIEWPORTS as R4_NARROW_VIEWPORTS,
)
from scripts.run_ux_r2_r3_workbench_qa import (
    GEOMETRY_FIXTURES,
    R2_FIXTURES,
    R3_FIXTURES,
    SAFE_GAP_CSS_PX,
    _minimum_inset,
)
from scripts.run_ux_r6_accessibility_qa import (
    EQUIVALENT_ZOOM_ROWS,
    MIN_LONG_PROJECT_NAME,
    R6_FIXTURES,
    R6_VIEWPORTS,
)


REPOSITORY = Path(__file__).resolve().parents[1]


class UxR2R3QaContractTest(unittest.TestCase):
    def test_review_and_geometry_screenshot_rows_are_real_browser_fixtures(self):
        matrix = load_and_validate_matrix(REPOSITORY / "qa/screenshot_matrix.json")
        rows = {row["id"]: row for row in matrix["scenarios"]}
        expected = {
            "review-no-selection": ("UX-R2", "review-no-selection"),
            "review-unreviewed-auto": ("UX-R3", "review-unreviewed-auto"),
            "review-accepted-auto": ("UX-R3", "review-accepted-auto"),
            "review-rejected-auto": ("UX-R3", "review-rejected-auto"),
            "review-pending-auto": ("UX-R3", "review-pending-auto"),
            "review-manual": ("UX-R3", "review-manual"),
            "save-in-progress": ("UX-R3", "save-in-progress"),
            "save-failed": ("UX-R3", "save-failed"),
            "chart-highest-peak": ("UX-R2", "review-highest"),
            "chart-edge-peak": ("UX-R2", "review-edge"),
            "chart-dense-peaks": ("UX-R2", "review-dense"),
        }
        for scenario, (stage, fixture) in expected.items():
            with self.subTest(scenario=scenario):
                row = rows[scenario]
                self.assertEqual(row["stage"], stage)
                self.assertEqual(row["automation"], "browser")
                self.assertEqual(row.get("fixture", scenario), fixture)
                self.assertEqual(fixture_id_from_path(row["path"]), fixture)

    def test_workbench_gate_fixture_sets_match_the_frozen_r2_contract(self):
        self.assertEqual(
            set(R2_FIXTURES),
            {
                "review-no-selection",
                "review-unreviewed-auto",
                "review-accepted-auto",
                "review-rejected-auto",
                "review-pending-auto",
                "review-manual",
                "review-highest",
                "review-edge",
                "review-dense",
            },
        )
        self.assertEqual(set(GEOMETRY_FIXTURES), {"review-highest", "review-edge", "review-dense"})
        self.assertEqual(set(R3_FIXTURES), {"save-in-progress", "save-failed"})

    def test_geometry_inset_uses_all_four_content_edges(self):
        content = {"left": 10.0, "top": 20.0, "right": 110.0, "bottom": 120.0}
        safe = {"left": 14.0, "top": 24.0, "right": 106.0, "bottom": 116.0}
        top_violation = {"left": 20.0, "top": 22.0, "right": 90.0, "bottom": 100.0}
        self.assertEqual(_minimum_inset(content, safe), SAFE_GAP_CSS_PX)
        self.assertEqual(_minimum_inset(content, top_violation), 2.0)

    def test_r5_range_export_rows_are_canonical_browser_fixtures(self):
        from scripts.run_ux_r5_range_export_qa import R5_FIXTURES, R5_STANDARD_VIEWPORTS

        matrix = load_and_validate_matrix(REPOSITORY / "qa/screenshot_matrix.json")
        rows = {row["id"]: row for row in matrix["scenarios"]}
        self.assertEqual(
            set(R5_FIXTURES),
            {
                "range-input",
                "range-calculating",
                "range-preview",
                "range-applying",
                "range-error",
                "export-review-results",
                "export-audit-package",
                "exporting",
                "export-error",
            },
        )
        for scenario in R5_FIXTURES:
            with self.subTest(scenario=scenario):
                self.assertEqual(rows[scenario]["stage"], "UX-R5")
                self.assertEqual(rows[scenario]["automation"], "browser")
                self.assertEqual(fixture_id_from_path(rows[scenario]["path"]), scenario)
        self.assertEqual(
            {(row["width"], row["height"]) for row in R5_STANDARD_VIEWPORTS},
            {(960, 640), (1366, 768), (1920, 1080)},
        )

    def test_r4_screenshot_rows_are_canonical_browser_fixtures(self):
        matrix = load_and_validate_matrix(REPOSITORY / "qa/screenshot_matrix.json")
        rows = {row["id"]: row for row in matrix["scenarios"]}
        self.assertEqual(
            set(R4_FIXTURES),
            {"add-aim", "add-preview", "adjust-aim", "adjust-preview", "edit-out-of-range"},
        )
        for fixture in R4_FIXTURES:
            with self.subTest(fixture=fixture):
                self.assertEqual(rows[fixture]["stage"], "UX-R4")
                self.assertEqual(rows[fixture]["automation"], "browser")
                self.assertEqual(fixture_id_from_path(rows[fixture]["path"]), fixture)

    def test_r4_api_and_dom_guard_sets_are_closed(self):
        self.assertEqual(
            EDIT_ENDPOINTS,
            {
                "aim": "/api/event-edits/aim",
                "preview": "/api/event-edits/preview",
                "apply": "/api/event-edits/apply",
                "cancel": "/api/event-edits/cancel",
            },
        )
        for private_name in ("event-id", "revision", "scan-row-index", "schema", "sqlite"):
            self.assertIn(private_name, FORBIDDEN_DOM_DATA)

    def test_r4_narrow_support_gate_keeps_science_and_interaction_separate(self):
        self.assertEqual(
            {(row["width"], row["height"]) for row in R4_NARROW_VIEWPORTS},
            {(960, 640), (1366, 768), (1440, 900), (1920, 1080)},
        )
        self.assertLess(MAX_CANONICAL_SUPPORT_SEC, 0.25)
        self.assertGreaterEqual(MIN_POINTER_HIT_CSS_PX, 12.0)
        self.assertGreater(KEYBOARD_CANDIDATE_SEC, ORIGINAL_APEX_SEC)
        self.assertLess(KEYBOARD_CANDIDATE_SEC - ORIGINAL_APEX_SEC, MAX_CANONICAL_SUPPORT_SEC)

    def test_r4_reflow_gate_uses_the_standard_screenshot_viewports(self):
        self.assertEqual(
            {(row["width"], row["height"]) for row in R4_STANDARD_VIEWPORTS},
            {(960, 640), (1366, 768), (1920, 1080)},
        )

    def test_r5_api_and_artifact_contract_sets_are_closed(self):
        from scripts.run_ux_r5_range_export_qa import (
            AUDIT_FILES,
            EXPORT_ENDPOINTS,
            FORBIDDEN_DOM_DATA as R5_FORBIDDEN_DOM_DATA,
            HUMAN_COLUMNS,
            RANGE_ENDPOINTS,
        )

        self.assertEqual(
            RANGE_ENDPOINTS,
            {
                "preview": "/api/range-changes/preview",
                "apply": "/api/range-changes/apply",
                "cancel": "/api/range-changes/cancel",
            },
        )
        self.assertEqual(
            EXPORT_ENDPOINTS,
            {
                "review_results": "/api/exports/review-results",
                "audit_package": "/api/exports/audit-package",
            },
        )
        self.assertEqual(
            HUMAN_COLUMNS,
            (
                "EventID",
                "scan_id",
                "scan_start_time",
                "apex_intensity",
                "review_status",
                "source",
            ),
        )
        self.assertEqual(AUDIT_FILES, ("checksums.sha256", "events.parquet", "manifest.json"))
        for private_name in ("preview-token", "job-id", "target-token", "source-path"):
            self.assertIn(private_name, R5_FORBIDDEN_DOM_DATA)

    def test_r6_rows_are_frozen_browser_fixtures(self):
        matrix = load_and_validate_matrix(REPOSITORY / "qa/screenshot_matrix.json")
        rows = {row["id"]: row for row in matrix["scenarios"]}
        self.assertEqual(set(R6_FIXTURES), {"undo-empty", "undo-redo-ready", "long-chinese-copy"})
        for fixture in R6_FIXTURES:
            with self.subTest(fixture=fixture):
                self.assertEqual(rows[fixture]["automation"], "browser")
                self.assertEqual(fixture_id_from_path(rows[fixture]["path"]), fixture)
        self.assertEqual(
            {(row["width"], row["height"]) for row in R6_VIEWPORTS},
            {(960, 640), (1366, 768), (1920, 1080)},
        )
        self.assertEqual({row["scale_percent"] for row in EQUIVALENT_ZOOM_ROWS}, {125, 150, 200})
        self.assertGreaterEqual(MIN_LONG_PROJECT_NAME, 107)


if __name__ == "__main__":
    unittest.main()
