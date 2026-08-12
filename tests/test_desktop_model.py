from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ms_event_studio.desktop_model import (
    CreationState,
    OptimisticReviewModel,
    PlotTransform,
    RecentProjects,
    Viewport,
    evidence_lines,
    event_visual_encoding,
    filter_events,
    keyboard_command,
)
from ms_event_studio.desktop import (
    FILTER_LABELS,
    FILTER_VALUES,
    SCALE_LABELS,
    SCALE_VALUES,
)


def event(event_id: str, status: str, origin: str, time_ns: int = 100) -> dict:
    return {
        "event_id": event_id,
        "status": status,
        "origin": origin,
        "revision": 0,
        "current_apex_time_ns": time_ns,
    }


class DesktopModelContractTest(unittest.TestCase):
    def test_chinese_ui_labels_round_trip_to_internal_scientific_values(self):
        self.assertEqual(FILTER_VALUES[FILTER_LABELS["accepted"]], "accepted")
        self.assertEqual(FILTER_VALUES[FILTER_LABELS["manual_added"]], "manual_added")
        self.assertEqual(SCALE_VALUES[SCALE_LABELS["log1p"]], "log1p")
        for label in (*FILTER_LABELS.values(), *SCALE_LABELS.values()):
            self.assertTrue(any("\u4e00" <= character <= "\u9fff" for character in label))

    def test_viewport_clamps_pan_and_resize_to_closed_analysis_range(self):
        viewport = Viewport(analysis_start_ns=0, analysis_end_ns=1_000, start_ns=100, window_ns=300)
        self.assertEqual(viewport.pan(-10_000).start_ns, 0)
        self.assertEqual(viewport.pan(10_000).end_ns, 1_000)
        full = viewport.with_window(5_000)
        self.assertEqual((full.start_ns, full.end_ns), (0, 1_000))
        self.assertTrue(full.contains(1_000))

    def test_filter_is_visual_only_and_status_has_non_color_encoding(self):
        source = [
            event("a", "unreviewed", "automatic", 1),
            event("b", "accepted", "manual_added", 2),
            event("c", "rejected", "manual_adjusted", 3),
        ]
        accepted = filter_events(source, "accepted")
        manual = filter_events(source, "manual_added")
        self.assertEqual([row["event_id"] for row in accepted], ["b"])
        self.assertEqual([row["event_id"] for row in manual], ["b"])
        self.assertEqual(len(source), 3)
        with_stale = [*source, {**event("d", "accepted", "automatic", 4), "generation_state": "stale"}]
        self.assertEqual([row["event_id"] for row in filter_events(with_stale, "all")], ["a", "b", "c"])
        self.assertEqual([row["event_id"] for row in filter_events(with_stale, "stale")], ["d"])
        encodings = {status: event_visual_encoding(status, "automatic") for status in (
            "unreviewed", "accepted", "rejected", "pending"
        )}
        self.assertEqual(len({value.shape for value in encodings.values()}), 4)
        self.assertTrue(all(value.text_token for value in encodings.values()))

    def test_recent_projects_are_atomic_friendly_and_hide_internal_schema(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "recent.json"
            recent = RecentProjects(path, limit=2)
            recent.remember(Path(tmp) / "one", "Friendly one")
            recent.remember(Path(tmp) / "two", "Friendly two")
            recent.remember(Path(tmp) / "three", "Friendly three")
            rows = recent.load()
            self.assertEqual([row.display_name for row in rows], ["Friendly three", "Friendly two"])
            serialized = path.read_text(encoding="utf-8")
            self.assertNotIn("schema_version", serialized)
            path.write_text("broken", encoding="utf-8")
            self.assertEqual(recent.load(), [])

    def test_creation_state_reports_real_progress_and_cancellation(self):
        state = CreationState()
        state.start()
        state.update_progress(bytes_read=25, total_bytes=100, parsed_spectra=3)
        self.assertEqual(state.fraction, 0.25)
        self.assertTrue(state.running)
        state.cancel()
        self.assertTrue(state.cancel_requested)
        self.assertTrue(state.cancel_check())

    def test_optimistic_status_success_and_failure_rollback(self):
        model = OptimisticReviewModel([event("a", "unreviewed", "automatic")])
        token = model.begin_status("a", "accepted")
        self.assertEqual(model.event("a")["status"], "accepted")
        self.assertTrue(model.event("a")["write_pending"])
        committed = dict(model.event("a"), revision=1, write_pending=False)
        model.commit(token, committed)
        self.assertEqual(model.event("a")["revision"], 1)

        token = model.begin_status("a", "rejected")
        model.rollback(token, RuntimeError("disk full"))
        self.assertEqual(model.event("a")["status"], "accepted")
        self.assertEqual(model.last_error, "disk full")
        self.assertFalse(model.event("a").get("write_pending", False))

    def test_plot_transform_round_trip_and_evidence_contract(self):
        transform = PlotTransform(
            width=1000,
            height=500,
            start_ns=1_000,
            end_ns=2_000,
            maximum_signal=100.0,
            log_scale=False,
        )
        x = transform.x_for_time(1_625)
        self.assertLessEqual(abs(transform.time_for_x(x) - 1_625), 1)
        self.assertLess(transform.y_for_signal(100.0), transform.y_for_signal(0.0))
        lines = evidence_lines(
            {
                **event("a", "accepted", "manual_adjusted"),
                "current_scan_id": "42",
                "current_apex_time_sec": 1.25,
                "current_apex_intensity": 100.0,
                "snap_offset_sec": 0.02,
            },
            {
                "pc34_760_mz_at_max_intensity": 760.585,
                "pc34_760_ppm_error_at_max_intensity": -0.13,
                "qc_782_max_intensity": 5.0,
                "tic": 1e7,
            },
            {
                "peak_prominence": 80.0,
                "peak_width_sec": 0.3,
                "left_sec": 1.1,
                "right_sec": 1.4,
                "collision_risk_high": True,
            },
        )
        text = "\n".join(lines)
        for required in (
            "状态 / 来源",
            "已接受",
            "人工调整",
            "m/z",
            "ppm",
            "Prominence",
            "Width",
            "collision_risk_high",
        ):
            self.assertIn(required, text)

    def test_keyboard_map_exposes_review_and_navigation_without_mouse(self):
        self.assertEqual(keyboard_command("a", control=False), "accept")
        self.assertEqual(keyboard_command("Left", control=False), "previous_window")
        self.assertEqual(keyboard_command("z", control=True), "undo")
        self.assertEqual(keyboard_command("y", control=True), "redo")
        self.assertIsNone(keyboard_command("F12", control=False))
        self.assertIsNone(
            keyboard_command("a", control=False, focus_widget_class="TEntry")
        )
        self.assertIsNone(
            keyboard_command("Left", control=False, focus_widget_class="Text")
        )


if __name__ == "__main__":
    unittest.main()
