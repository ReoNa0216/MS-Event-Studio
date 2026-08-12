from __future__ import annotations

import unittest

from ms_event_studio.reconcile import propose_reconciliation


def old_event(
    event_id: str,
    auto_id: str,
    *,
    scan_id: str,
    spectrum_index: int,
    scan_row_index: int,
    time_ns: int,
    left_ns: int,
    right_ns: int,
    interval_sec: float,
):
    return {
        "event_id": event_id,
        "auto_event_id": auto_id,
        "origin": "automatic",
        "current_auto_scan_id": scan_id,
        "current_auto_spectrum_index": spectrum_index,
        "current_auto_scan_row_index": scan_row_index,
        "current_auto_apex_time_ns": time_ns,
        "current_auto_left_time_ns": left_ns,
        "current_auto_right_time_ns": right_ns,
        "current_auto_local_scan_interval_sec": interval_sec,
    }


def new_event(
    auto_id: str,
    *,
    scan_id: str,
    spectrum_index: int,
    scan_row_index: int,
    time_ns: int,
    left_ns: int,
    right_ns: int,
    interval_sec: float,
):
    return {
        "auto_event_id": auto_id,
        "scan_id": scan_id,
        "spectrum_index": spectrum_index,
        "scan_row_index": scan_row_index,
        "scan_time_ns": time_ns,
        "left_time_ns": left_ns,
        "right_time_ns": right_ns,
        "local_scan_interval_sec": interval_sec,
    }


class ReconciliationContractTest(unittest.TestCase):
    def test_exact_physical_identity_reuses_event_id_independent_of_order(self):
        old = [
            old_event(
                "EV_a",
                "AE_old_a",
                scan_id="7",
                spectrum_index=5,
                scan_row_index=6,
                time_ns=1_000_000_000,
                left_ns=900_000_000,
                right_ns=1_100_000_000,
                interval_sec=0.1,
            ),
            old_event(
                "EV_b",
                "AE_old_b",
                scan_id="8",
                spectrum_index=6,
                scan_row_index=7,
                time_ns=2_000_000_000,
                left_ns=1_900_000_000,
                right_ns=2_100_000_000,
                interval_sec=0.1,
            ),
        ]
        new = [
            new_event(
                "AE_new_b",
                scan_id="8",
                spectrum_index=6,
                scan_row_index=7,
                time_ns=2_000_000_000,
                left_ns=1_900_000_000,
                right_ns=2_100_000_000,
                interval_sec=0.1,
            ),
            new_event(
                "AE_new_a",
                scan_id="7",
                spectrum_index=5,
                scan_row_index=6,
                time_ns=1_000_000_000,
                left_ns=900_000_000,
                right_ns=1_100_000_000,
                interval_sec=0.1,
            ),
        ]
        first = propose_reconciliation(old, new)
        second = propose_reconciliation(list(reversed(old)), list(reversed(new)))
        projection = lambda plan: [
            (row.event_id, row.new_auto_event_id, row.method) for row in plan.mappings
        ]
        self.assertEqual(projection(first), projection(second))
        self.assertEqual(
            projection(first),
            [("EV_a", "AE_new_a", "exact_scan_identity"), ("EV_b", "AE_new_b", "exact_scan_identity")],
        )
        self.assertEqual(first.stale_event_ids, ())
        self.assertEqual(first.unmatched_new_auto_event_ids, ())

    def test_mutual_unique_nearest_support_can_be_proposed(self):
        old = [
            old_event(
                "EV_a",
                "AE_old",
                scan_id="old",
                spectrum_index=1,
                scan_row_index=1,
                time_ns=1_000_000_000,
                left_ns=900_000_000,
                right_ns=1_100_000_000,
                interval_sec=0.1,
            )
        ]
        new = [
            new_event(
                "AE_new",
                scan_id="new",
                spectrum_index=2,
                scan_row_index=2,
                time_ns=1_150_000_000,
                left_ns=1_050_000_000,
                right_ns=1_250_000_000,
                interval_sec=0.1,
            )
        ]
        plan = propose_reconciliation(old, new)
        self.assertEqual(len(plan.mappings), 1)
        self.assertEqual(plan.mappings[0].method, "mutual_unique_nearest_support")
        self.assertTrue(plan.mappings[0].requires_confirmation)
        self.assertAlmostEqual(plan.mappings[0].distance_sec, 0.15)

    def test_tie_is_ambiguous_and_old_review_stays_stale(self):
        old = [
            old_event(
                "EV_a",
                "AE_old",
                scan_id="old",
                spectrum_index=1,
                scan_row_index=1,
                time_ns=1_000_000_000,
                left_ns=800_000_000,
                right_ns=1_200_000_000,
                interval_sec=0.1,
            )
        ]
        new = [
            new_event(
                "AE_left",
                scan_id="left",
                spectrum_index=2,
                scan_row_index=2,
                time_ns=950_000_000,
                left_ns=850_000_000,
                right_ns=1_050_000_000,
                interval_sec=0.1,
            ),
            new_event(
                "AE_right",
                scan_id="right",
                spectrum_index=3,
                scan_row_index=3,
                time_ns=1_050_000_000,
                left_ns=950_000_000,
                right_ns=1_150_000_000,
                interval_sec=0.1,
            ),
        ]
        plan = propose_reconciliation(old, new)
        self.assertEqual(plan.mappings, ())
        self.assertEqual(plan.stale_event_ids, ("EV_a",))
        self.assertEqual(plan.ambiguous_event_ids, ("EV_a",))
        self.assertEqual(set(plan.unmatched_new_auto_event_ids), {"AE_left", "AE_right"})


if __name__ == "__main__":
    unittest.main()
