from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from _fixtures import detector_scan
from ms_event_studio.errors import ReviewConflict, SnapError
from ms_event_studio.review import ReviewStore


def automatic_rows():
    return [
        {
            "auto_event_id": "AE_" + "1" * 64,
            "generation_id": "GEN_" + "2" * 64,
            "scan_id": "100003",
            "scan_row_index": 3,
            "spectrum_index": 3,
            "scan_time_ns": 300_000_000,
            "apex_time_sec": 0.3,
            "left_sec": 0.2,
            "right_sec": 0.4,
            "apex_intensity": 1000.0,
        }
    ]


class ReviewStoreContractTest(unittest.TestCase):
    def test_status_revision_restore_and_append_only_audit(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = Path(tmp) / "review.sqlite"
            store = ReviewStore.create(
                db,
                project_id="project-1",
                generation_id="GEN_" + "2" * 64,
                automatic_events=automatic_rows(),
            )
            event = store.list_events()[0]
            self.assertEqual(event["status"], "unreviewed")
            accepted = store.set_status(
                event["event_id"],
                "accepted",
                expected_revision=0,
                actor="tester",
                session_id="s1",
                reason="real peak",
            )
            self.assertEqual(accepted["revision"], 1)
            with self.assertRaises(ReviewConflict):
                store.set_status(
                    event["event_id"],
                    "rejected",
                    expected_revision=0,
                    actor="tester",
                    session_id="s1",
                )
            restored = store.restore(
                event["event_id"],
                expected_revision=1,
                actor="tester",
                session_id="s1",
            )
            self.assertEqual(restored["status"], "unreviewed")
            self.assertEqual(len(store.audit_events()), 2)
            store.close()

            conn = sqlite3.connect(db)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM audit_events")
            conn.close()

    def test_undo_redo_survive_reopen(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = Path(tmp) / "review.sqlite"
            store = ReviewStore.create(
                db,
                project_id="project-1",
                generation_id="GEN_" + "2" * 64,
                automatic_events=automatic_rows(),
            )
            event = store.list_events()[0]
            store.set_status(
                event["event_id"],
                "accepted",
                expected_revision=0,
                actor="tester",
                session_id="s1",
            )
            store.undo(actor="tester", session_id="s1")
            store.close()

            reopened = ReviewStore.open(db, project_id="project-1")
            self.assertEqual(reopened.list_events()[0]["status"], "unreviewed")
            reopened.redo(actor="tester", session_id="s2")
            self.assertEqual(reopened.list_events()[0]["status"], "accepted")
            self.assertEqual([row["action"] for row in reopened.audit_events()], ["set_status", "undo", "redo"])
            reopened.close()

    def test_bulk_status_is_atomic_and_one_undo_redo_step(self):
        rows = automatic_rows()
        second = dict(rows[0])
        second.update(
            auto_event_id="AE_" + "3" * 64,
            scan_id="100004",
            scan_row_index=4,
            spectrum_index=4,
            scan_time_ns=400_000_000,
            apex_time_sec=0.4,
            left_sec=0.35,
            right_sec=0.45,
        )
        rows.append(second)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = ReviewStore.create(
                Path(tmp) / "review.sqlite",
                project_id="project-1",
                generation_id="GEN_" + "2" * 64,
                automatic_events=rows,
            )
            events = store.list_events()
            with self.assertRaises(ReviewConflict):
                store.set_status_bulk(
                    [
                        (events[0]["event_id"], events[0]["revision"]),
                        (events[1]["event_id"], events[1]["revision"] + 1),
                    ],
                    "accepted",
                    actor="tester",
                    session_id="s1",
                )
            self.assertEqual(
                [event["status"] for event in store.list_events()],
                ["unreviewed", "unreviewed"],
            )
            updated = store.set_status_bulk(
                [(event["event_id"], event["revision"]) for event in events],
                "accepted",
                actor="tester",
                session_id="s1",
                expected_active_revisions={
                    event["event_id"]: event["revision"] for event in events
                },
            )
            self.assertEqual([event["status"] for event in updated], ["accepted", "accepted"])
            self.assertEqual([event["status"] for event in store.list_events()], ["accepted", "accepted"])

            store.undo(actor="tester", session_id="s1")
            self.assertEqual([event["status"] for event in store.list_events()], ["unreviewed", "unreviewed"])
            self.assertEqual(store.history_state(), {"can_undo": False, "can_redo": True})

            store.redo(actor="tester", session_id="s1")
            self.assertEqual([event["status"] for event in store.list_events()], ["accepted", "accepted"])
            self.assertEqual(store.history_state(), {"can_undo": True, "can_redo": False})
            self.assertEqual(
                [row["action"] for row in store.audit_events()],
                ["bulk_set_status", "bulk_set_status", "undo", "undo", "redo", "redo"],
            )

    def test_bulk_status_rejects_a_changed_non_candidate_event(self):
        rows = automatic_rows()
        second = dict(rows[0])
        second.update(
            auto_event_id="AE_" + "3" * 64,
            scan_id="100004",
            scan_row_index=4,
            spectrum_index=4,
            scan_time_ns=900_000_000,
            apex_time_sec=0.9,
            left_sec=0.8,
            right_sec=1.0,
        )
        rows.append(second)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = ReviewStore.create(
                Path(tmp) / "review.sqlite",
                project_id="project-1",
                generation_id="GEN_" + "2" * 64,
                automatic_events=rows,
            )
            before = store.list_events()
            store.set_status(
                before[1]["event_id"],
                "pending",
                expected_revision=before[1]["revision"],
                actor="other-window",
                session_id="s2",
            )
            with self.assertRaises(ReviewConflict):
                store.set_status_bulk(
                    [(before[0]["event_id"], before[0]["revision"])],
                    "accepted",
                    actor="tester",
                    session_id="s1",
                    expected_active_revisions={
                        event["event_id"]: event["revision"] for event in before
                    },
                )
            current = store.list_events()
            self.assertEqual(current[0]["status"], "unreviewed")
            self.assertEqual(current[1]["status"], "pending")

    def test_manual_add_snaps_to_real_local_peak_and_defaults_accepted(self):
        signal = np.zeros(20)
        signal[10] = 500.0
        scan = detector_scan(signal)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = ReviewStore.create(
                Path(tmp) / "review.sqlite",
                project_id="project-1",
                generation_id="GEN_" + "2" * 64,
                automatic_events=[],
            )
            added = store.add_event(
                click_time_sec=1.08,
                scans=scan,
                analysis_start_ns=0,
                analysis_end_ns=2_000_000_000,
                actor="tester",
                session_id="s1",
                reason="missed peak",
            )
            self.assertEqual(added["current_scan_id"], "100010")
            self.assertEqual(added["status"], "accepted")
            self.assertEqual(added["origin"], "manual_added")
            self.assertAlmostEqual(added["snap_offset_sec"], -0.08)
            with self.assertRaises(SnapError):
                store.add_event(
                    click_time_sec=1.8,
                    scans=scan,
                    analysis_start_ns=0,
                    analysis_end_ns=2_000_000_000,
                    actor="tester",
                    session_id="s1",
                )
            store.close()

    def test_automatic_adjust_cannot_leave_immutable_support(self):
        signal = np.zeros(20)
        signal[[3, 10]] = [1000.0, 900.0]
        scan = detector_scan(signal)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = ReviewStore.create(
                Path(tmp) / "review.sqlite",
                project_id="project-1",
                generation_id="GEN_" + "2" * 64,
                automatic_events=automatic_rows(),
            )
            event = store.list_events()[0]
            with self.assertRaisesRegex(SnapError, "support"):
                store.adjust_apex(
                    event["event_id"],
                    click_time_sec=1.0,
                    scans=scan,
                    analysis_start_ns=0,
                    analysis_end_ns=2_000_000_000,
                    expected_revision=0,
                    actor="tester",
                    session_id="s1",
                    reason="wrong peak",
                )
            store.close()

    def test_manual_adjust_cannot_leave_closed_analysis_range(self):
        signal = np.zeros(20)
        signal[[10, 13]] = [500.0, 600.0]
        scan = detector_scan(signal)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = ReviewStore.create(
                Path(tmp) / "review.sqlite",
                project_id="project-1",
                generation_id="GEN_" + "2" * 64,
                automatic_events=[],
            )
            added = store.add_event(
                click_time_sec=1.0,
                scans=scan,
                analysis_start_ns=0,
                analysis_end_ns=1_100_000_000,
                actor="tester",
                session_id="s1",
            )
            with self.assertRaisesRegex(SnapError, "analysis range"):
                store.adjust_apex(
                    added["event_id"],
                    click_time_sec=1.3,
                    scans=scan,
                    analysis_start_ns=0,
                    analysis_end_ns=1_100_000_000,
                    expected_revision=0,
                    actor="tester",
                    session_id="s1",
                )
            store.close()


if __name__ == "__main__":
    unittest.main()
