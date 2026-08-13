from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from _fixtures import spectrum_lines, write_ms_file
from ms_event_studio.demo import create_guided_source
from ms_event_studio.project import CreateProjectRequest, create_project
from ms_event_studio.web_app import WebSession


def _create_project(root: Path, *, duration_min: int):
    if duration_min == 2:
        source = root / "short-source.txt"
        create_guided_source(source)
    else:
        count = duration_min * 600 + 1
        peaks = {300: 1_000.0, 6_300: 1_200.0}
        source = write_ms_file(
            root / "long-source.txt",
            [
                spectrum_lines(
                    index,
                    index + 1,
                    f"{index / 600.0:.12f}",
                    intensities=[0.0, peaks.get(index, 0.0), 10.0, 0.0],
                )
                for index in range(count)
            ],
        )
    return create_project(
        CreateProjectRequest(
            source_path=source,
            project_dir=root / "project",
            display_name="Workspace viewport contract",
            analysis_start_min="0",
            analysis_end_min=str(duration_min),
        )
    )


def _open_session(root: Path, project) -> WebSession:
    session = WebSession(root / "recent.json")
    selection = session.register_path("project_open", project.project_dir)
    session.open_project(selection["selection_token"])
    return session


def _viewport(workspace: dict) -> tuple[Decimal, Decimal]:
    viewport = workspace["window"]["viewport"]
    return Decimal(viewport["start_min"]), Decimal(viewport["end_min"])


class WorkspaceViewportContractTest(unittest.TestCase):
    def test_short_project_defaults_to_its_complete_analysis_range(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            project = _create_project(root, duration_min=2)
            with _open_session(root, project) as session:
                workspace = session.workspace()

            self.assertEqual(_viewport(workspace), (Decimal("0"), Decimal("2")))

    def test_long_project_defaults_to_ten_minutes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            project = _create_project(root, duration_min=11)
            with _open_session(root, project) as session:
                workspace = session.workspace()

            self.assertEqual(_viewport(workspace), (Decimal("0"), Decimal("10")))
            self.assertEqual(len(workspace["events"]), 2)
            self.assertEqual(len(workspace["window"]["event_overlay"]), 1)

    def test_selecting_an_out_of_view_event_pans_minimally_and_keeps_it_visible(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            project = _create_project(root, duration_min=11)
            with _open_session(root, project) as session:
                initial = session.workspace()
                late = max(initial["events"], key=lambda event: event["apex_time_min"])
                selected = session.workspace({"selected_event_token": late["event_token"]})

            start, end = _viewport(selected)
            apex = Decimal(str(selected["selection"]["event"]["apex_time_min"]))
            self.assertEqual((start, end), (Decimal("0.5"), Decimal("10.5")))
            self.assertLessEqual(start, apex)
            self.assertLessEqual(apex, end)
            self.assertIn(
                selected["selection"]["event"]["event_token"],
                {event["event_token"] for event in selected["window"]["event_overlay"]},
            )

    def test_review_auto_advance_pans_to_the_next_unreviewed_event(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            project = _create_project(root, duration_min=11)
            with _open_session(root, project) as session:
                initial = session.workspace()
                advanced = session.review_decision(
                    {
                        "action_token": initial["selection"]["event"]["action_token"],
                        "decision": "keep",
                        "note": "viewport auto-advance contract",
                    }
                )["workspace"]

            start, end = _viewport(advanced)
            apex = Decimal(str(advanced["selection"]["event"]["apex_time_min"]))
            self.assertEqual((start, end), (Decimal("0.5"), Decimal("10.5")))
            self.assertLessEqual(start, apex)
            self.assertLessEqual(apex, end)

    def test_explicit_window_with_selection_at_closed_end_does_not_drift(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            project = _create_project(root, duration_min=11)
            with _open_session(root, project) as session:
                initial = session.workspace()
                late = max(initial["events"], key=lambda event: event["apex_time_min"])
                selected = session.workspace(
                    {
                        "start_min": "0.5",
                        "end_min": "10.5",
                        "selected_event_token": late["event_token"],
                    }
                )
                last_window = session.workspace(
                    {
                        "start_min": "1",
                        "end_min": "11",
                        "selected_event_token": None,
                    }
                )

            self.assertEqual(_viewport(selected), (Decimal("0.5"), Decimal("10.5")))
            self.assertEqual(
                Decimal(str(selected["selection"]["event"]["apex_time_min"])),
                Decimal("10.5"),
            )
            self.assertEqual(_viewport(last_window), (Decimal("1"), Decimal("11")))

    def test_explicit_window_only_pan_is_not_undone_for_the_current_selection(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            project = _create_project(root, duration_min=11)
            with _open_session(root, project) as session:
                initial = session.workspace()
                self.assertEqual(
                    Decimal(str(initial["selection"]["event"]["apex_time_min"])),
                    Decimal("0.5"),
                )
                panned = session.workspace(
                    {
                        "start_min": "1",
                        "end_min": "11",
                        "selected_event_token": None,
                    }
                )

            self.assertEqual(_viewport(panned), (Decimal("1"), Decimal("11")))
            self.assertEqual(
                Decimal(str(panned["selection"]["event"]["apex_time_min"])),
                Decimal("0.5"),
            )


if __name__ == "__main__":
    unittest.main()
