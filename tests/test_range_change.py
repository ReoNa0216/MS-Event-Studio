from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from _fixtures import spectrum_lines, write_ms_file
from ms_event_studio.errors import ProjectValidationError
from ms_event_studio.export import export_human_csv
from ms_event_studio.paths import resolve_project_path
from ms_event_studio.project import CreateProjectRequest, create_project, open_project
from ms_event_studio.range_change import apply_range_change, preview_range_change
from ms_event_studio.review import ReviewStore


def make_source(path: Path) -> Path:
    signal = np.zeros(1201)
    signal[[300, 600, 900]] = [1000.0, 1500.0, 1200.0]
    return write_ms_file(
        path,
        [
            spectrum_lines(
                index,
                index + 1,
                f"{index / 600.0:.12f}",
                intensities=[0.0, float(signal[index]), 10.0, 0.0],
            )
            for index in range(len(signal))
        ],
    )


def review_store(project) -> ReviewStore:
    path = resolve_project_path(project.project_dir, project.manifest["review"]["path"])
    return ReviewStore.open(path, project_id=project.manifest["project_id"])


class RangeChangeContractTest(unittest.TestCase):
    def test_preview_is_read_only_and_apply_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            project = create_project(
                CreateProjectRequest(
                    source_path=make_source(root / "source.txt"),
                    project_dir=root / "project",
                    display_name="Range preview",
                    analysis_start_min="0",
                    analysis_end_min="2",
                )
            )
            manifest_path = project.project_dir / "ms_event_project.json"
            before = manifest_path.read_bytes()
            preview = preview_range_change(project.project_dir, "0.75", "2")
            self.assertEqual(manifest_path.read_bytes(), before)
            self.assertNotEqual(preview.old_generation_id, preview.new_generation_id)
            self.assertGreaterEqual(len(preview.plan.stale_event_ids), 1)
            with self.assertRaisesRegex(ProjectValidationError, "explicit confirmation"):
                apply_range_change(
                    preview,
                    confirmed=False,
                    actor="tester",
                    session_id="s1",
                    reason="narrow range",
                )
            self.assertEqual(manifest_path.read_bytes(), before)

    def test_confirmed_change_preserves_ids_reviews_history_and_excludes_stale_export(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            project = create_project(
                CreateProjectRequest(
                    source_path=make_source(root / "source.txt"),
                    project_dir=root / "project",
                    display_name="Range apply",
                    analysis_start_min="0",
                    analysis_end_min="2",
                )
            )
            store = review_store(project)
            old_review_path = store.path
            old_events = store.list_events()
            first = old_events[0]
            accepted = store.set_status(
                first["event_id"],
                "accepted",
                expected_revision=first["revision"],
                actor="tester",
                session_id="s1",
            )
            store.close()

            preview = preview_range_change(project.project_dir, "0.75", "2")
            changed = apply_range_change(
                preview,
                confirmed=True,
                actor="tester",
                session_id="s2",
                reason="confirmed range change",
            )
            reopened = open_project(changed.project_dir)
            self.assertEqual(reopened.manifest["generation_id"], preview.new_generation_id)
            self.assertEqual(reopened.manifest["analysis_range"]["start_ns"], 45_000_000_000)
            self.assertEqual(len(reopened.manifest["generation_history"]), 1)
            history = reopened.manifest["generation_history"][0]
            self.assertEqual(history["generation_id"], preview.old_generation_id)
            self.assertNotEqual(
                history["review_database"]["path"],
                project.manifest["review"]["path"],
            )
            for role in ("automatic_events", "detector_protocol", "review_database"):
                self.assertTrue((reopened.project_dir / history[role]["path"]).is_file())

            # A second application that opened the old generation before the
            # switch can still write that obsolete path. The immutable archived
            # copy, and therefore the new project, must remain valid.
            stale_store = ReviewStore.open(
                old_review_path,
                project_id=project.manifest["project_id"],
            )
            stale_row = stale_store.list_events()[-1]
            stale_store.set_status(
                stale_row["event_id"],
                "pending",
                expected_revision=stale_row["revision"],
                actor="stale-window",
                session_id="old",
            )
            stale_store.close()
            reopened = open_project(changed.project_dir)

            store = review_store(reopened)
            states = store.list_events()
            stale = [row for row in states if row.get("generation_state") == "stale"]
            active = [row for row in states if row.get("generation_state") != "stale"]
            self.assertTrue(stale)
            self.assertIn(accepted["event_id"], {row["event_id"] for row in stale})
            mapped_ids = {mapping.event_id for mapping in preview.plan.mappings}
            self.assertTrue(mapped_ids.intersection({row["event_id"] for row in active}))
            self.assertEqual(store.audit_events()[-1]["action"], "recalculate_analysis_range")

            output = root / "accepted.csv"
            result = export_human_csv(
                states,
                output,
                analysis_start_ns=45_000_000_000,
                analysis_end_ns=120_000_000_000,
            )
            self.assertEqual(result.row_count, 0)
            store.close()

    def test_review_write_after_preview_invalidates_apply(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            project = create_project(
                CreateProjectRequest(
                    source_path=make_source(root / "source.txt"),
                    project_dir=root / "project",
                    display_name="Stale preview",
                    analysis_start_min="0",
                    analysis_end_min="2",
                )
            )
            preview = preview_range_change(project.project_dir, "0.75", "2")
            store = review_store(project)
            row = store.list_events()[0]
            store.set_status(
                row["event_id"],
                "pending",
                expected_revision=row["revision"],
                actor="other",
                session_id="other",
            )
            store.close()
            with self.assertRaisesRegex(ProjectValidationError, "preview is stale"):
                apply_range_change(
                    preview,
                    confirmed=True,
                    actor="tester",
                    session_id="s1",
                )

    def test_mutated_detection_after_preview_is_rejected(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            project = create_project(
                CreateProjectRequest(
                    source_path=make_source(root / "source.txt"),
                    project_dir=root / "project",
                    display_name="Mutated preview",
                    analysis_start_min="0",
                    analysis_end_min="2",
                )
            )
            preview = preview_range_change(project.project_dir, "0.75", "2")
            preview.detection.events.loc[
                preview.detection.events.index[0], "apex_intensity"
            ] += 1.0
            with self.assertRaisesRegex(ProjectValidationError, "payload changed"):
                apply_range_change(
                    preview,
                    confirmed=True,
                    actor="tester",
                    session_id="s1",
                )

    def test_failed_post_switch_validation_rolls_back_manifest_and_orphan_activation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            project = create_project(
                CreateProjectRequest(
                    source_path=make_source(root / "source.txt"),
                    project_dir=root / "project",
                    display_name="Rollback",
                    analysis_start_min="0",
                    analysis_end_min="2",
                )
            )
            preview = preview_range_change(project.project_dir, "0.75", "2")
            manifest_path = project.project_dir / "ms_event_project.json"
            before = manifest_path.read_bytes()
            with patch(
                "ms_event_studio.range_change.open_project",
                side_effect=[project, ProjectValidationError("injected post-switch failure")],
            ):
                with self.assertRaisesRegex(ProjectValidationError, "injected"):
                    apply_range_change(
                        preview,
                        confirmed=True,
                        actor="tester",
                        session_id="s1",
                    )
            self.assertEqual(manifest_path.read_bytes(), before)
            activations = list((project.project_dir / "generations").glob("**/ACT_*"))
            self.assertEqual(activations, [])


if __name__ == "__main__":
    unittest.main()
