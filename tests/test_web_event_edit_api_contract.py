from __future__ import annotations

import hashlib
import http.client
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

import numpy as np

from ms_event_studio.web_app import WebBoundaryError, create_http_server
from ms_event_studio.window_service import ProjectWindowService
from test_web_review_api_contract import (
    assert_workspace_safe,
    create_adjustable_project,
    create_guided_project,
    open_session,
)


def fingerprint(path: Path) -> tuple[int, int, str]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()


def select_nearest(session, minute: float) -> dict[str, object]:
    workspace = session.workspace()
    event = min(workspace["events"], key=lambda row: abs(row["apex_time_min"] - minute))
    return session.workspace({"selected_event_token": event["event_token"]})


class EventEditCapabilityContractTest(unittest.TestCase):
    def test_add_apply_is_single_use_undoable_redoable_and_persistent(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source, project = create_guided_project(root)
            before_source = fingerprint(source)
            session = open_session(root, project)
            try:
                aim = session.begin_event_edit({"mode": "add"})
                self.assertEqual(
                    list(aim),
                    ["aim_token", "mode", "before", "allowed_interval"],
                )
                self.assertEqual(aim["mode"], "add")
                self.assertIsNone(aim["before"])
                self.assertEqual(aim["allowed_interval"], {"start_min": 0.0, "end_min": 2.0})
                preview = session.preview_event_edit(
                    {"aim_token": aim["aim_token"], "click_time_min": 0.75}
                )
                self.assertEqual(
                    list(preview),
                    ["preview_token", "mode", "candidate", "change", "allowed_interval"],
                )
                self.assertEqual(preview["candidate"], {
                    "time_min": 0.75,
                    "intensity": 80.0,
                    "offset_sec": 0.0,
                })
                self.assertIsNone(preview["change"]["before"])
                self.assertEqual(preview["change"]["after"], {
                    "time_min": 0.75,
                    "intensity": 80.0,
                })
                with self.assertRaises(WebBoundaryError) as replayed_aim:
                    session.preview_event_edit(
                        {"aim_token": aim["aim_token"], "click_time_min": 0.75}
                    )
                self.assertEqual(replayed_aim.exception.code, "stale_edit")

                applied = session.apply_event_edit(
                    {"preview_token": preview["preview_token"], "note": "补充遗漏信号"}
                )
                self.assertEqual(applied["outcome"], "applied")
                selected = applied["workspace"]["selection"]["event"]
                self.assertEqual(selected["origin"], "manual_added")
                self.assertEqual(selected["status"], "accepted")
                self.assertAlmostEqual(selected["apex_time_min"], 0.75)
                self.assertEqual(applied["workspace"]["review"]["total"], 4)
                with self.assertRaises(WebBoundaryError) as replayed_preview:
                    session.apply_event_edit(
                        {"preview_token": preview["preview_token"], "note": "重复"}
                    )
                self.assertEqual(replayed_preview.exception.code, "stale_edit")
                assert_workspace_safe(
                    self,
                    applied,
                    private_values=(str(root), str(source), str(project.project_dir)),
                )

                undone = session.undo_review({"note": "撤销补充"})["workspace"]
                self.assertEqual(undone["review"]["total"], 3)
                redone = session.redo_review({"note": "重做补充"})["workspace"]
                self.assertEqual(redone["review"]["total"], 4)
                manual = next(row for row in redone["events"] if row["origin"] == "manual_added")
                selected_manual = session.workspace(
                    {"selected_event_token": manual["event_token"]}
                )
                manual_aim = session.begin_event_edit(
                    {
                        "mode": "adjust",
                        "action_token": selected_manual["selection"]["event"]["action_token"],
                    }
                )
                self.assertEqual(
                    manual_aim["allowed_interval"],
                    {"start_min": 0.0, "end_min": 2.0},
                )
                session.cancel_event_edit({"edit_token": manual_aim["aim_token"]})
            finally:
                session.close()

            reopened = open_session(root, project)
            try:
                persisted = reopened.workspace()
                self.assertEqual(persisted["review"]["total"], 4)
                self.assertTrue(any(row["origin"] == "manual_added" for row in persisted["events"]))
                with ProjectWindowService.open(project.project_dir) as service:
                    actions = [row["action"] for row in service.review_store.audit_events()]
                self.assertEqual(actions[-3:], ["add_event", "undo", "redo"])
            finally:
                reopened.close()
            self.assertEqual(fingerprint(source), before_source)

    def test_cancel_and_existing_support_navigation_are_zero_write(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _source, project = create_guided_project(root)
            session = open_session(root, project)
            try:
                store = session._workspace._window_service.review_store
                self.assertEqual(store.audit_events(), [])

                aim = session.begin_event_edit({"mode": "add"})
                self.assertEqual(
                    session.cancel_event_edit({"edit_token": aim["aim_token"]}),
                    {"ok": True, "cancelled": True},
                )
                with self.assertRaises(WebBoundaryError) as repeated_cancel:
                    session.cancel_event_edit({"edit_token": aim["aim_token"]})
                self.assertEqual(repeated_cancel.exception.code, "stale_edit")

                aim = session.begin_event_edit({"mode": "add"})
                preview = session.preview_event_edit(
                    {"aim_token": aim["aim_token"], "click_time_min": 0.75}
                )
                session.cancel_event_edit({"edit_token": preview["preview_token"]})
                with self.assertRaises(WebBoundaryError):
                    session.apply_event_edit({"preview_token": preview["preview_token"]})

                aim = session.begin_event_edit({"mode": "add"})
                overlap = session.preview_event_edit(
                    {"aim_token": aim["aim_token"], "click_time_min": 0.5}
                )
                navigated = session.apply_event_edit(
                    {"preview_token": overlap["preview_token"], "note": "已有事件"}
                )
                self.assertEqual(navigated["outcome"], "navigate_existing")
                self.assertEqual(navigated["workspace"]["review"]["total"], 3)
                self.assertAlmostEqual(
                    navigated["workspace"]["selection"]["event"]["apex_time_min"],
                    0.5,
                )
                self.assertEqual(store.audit_events(), [])
            finally:
                session.close()

    def test_preview_rejects_gap_ambiguity_and_out_of_range_without_writes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _source, project = create_guided_project(root)
            session = open_session(root, project)
            try:
                service = session._workspace._window_service
                scans = service.scans
                original = scans.copy(deep=True)

                signal = np.zeros(len(scans), dtype=float)
                signal[[100, 102]] = 50.0
                regular_times = np.arange(len(scans), dtype=float) * 0.1
                scans["scan_start_time_sec"] = regular_times
                scans["scan_start_time_min"] = regular_times / 60.0
                scans["scan_time_ns"] = np.rint(
                    regular_times * 1_000_000_000
                ).astype("int64")
                scans["pc34_760_max_intensity"] = signal
                aim = session.begin_event_edit({"mode": "add"})
                with self.assertRaises(WebBoundaryError) as ambiguous:
                    session.preview_event_edit(
                        {
                            "aim_token": aim["aim_token"],
                            "click_time_min": "0.168333333333333333",
                        }
                    )
                self.assertEqual(ambiguous.exception.code, "ambiguous_candidate")

                scans.loc[:, :] = original
                times = np.arange(len(scans), dtype=float) * 0.1
                times[3:] += 0.7
                signal = np.zeros(len(scans), dtype=float)
                signal[3] = 50.0
                scans["scan_start_time_sec"] = times
                scans["scan_start_time_min"] = times / 60.0
                scans["scan_time_ns"] = np.rint(times * 1_000_000_000).astype("int64")
                scans["pc34_760_max_intensity"] = signal
                aim = session.begin_event_edit({"mode": "add"})
                with self.assertRaises(WebBoundaryError) as gap:
                    session.preview_event_edit(
                        {
                            "aim_token": aim["aim_token"],
                            "click_time_min": "0.014166666666666667",
                        }
                    )
                self.assertEqual(gap.exception.code, "scan_gap")

                aim = session.begin_event_edit({"mode": "add"})
                with self.assertRaises(WebBoundaryError) as outside:
                    session.preview_event_edit(
                        {"aim_token": aim["aim_token"], "click_time_min": -0.001}
                    )
                self.assertEqual(outside.exception.code, "outside_allowed_interval")
                self.assertEqual(service.review_store.audit_events(), [])
                self.assertEqual(len(service.review_store.list_events()), 3)
            finally:
                session.close()

    def test_preview_binds_the_candidate_and_rejects_changed_signal_without_write(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _source, project = create_guided_project(root)
            session = open_session(root, project)
            try:
                aim = session.begin_event_edit({"mode": "add"})
                preview = session.preview_event_edit(
                    {"aim_token": aim["aim_token"], "click_time_min": 0.75}
                )
                service = session._workspace._window_service
                candidate = service.scans[
                    service.scans["scan_start_time_sec"].astype(float) == 45.0
                ]
                self.assertEqual(len(candidate), 1)
                row_index = candidate.index[0]
                service.scans.loc[row_index, "pc34_760_max_intensity"] = 81.0
                with self.assertRaises(WebBoundaryError) as changed:
                    session.apply_event_edit(
                        {"preview_token": preview["preview_token"], "note": "不得套用旧预览"}
                    )
                self.assertEqual(changed.exception.code, "stale_preview")
                self.assertEqual(changed.exception.status.value, 409)
                with self.assertRaises(WebBoundaryError) as replay:
                    session.apply_event_edit({"preview_token": preview["preview_token"]})
                self.assertEqual(replay.exception.code, "stale_edit")
                self.assertEqual(service.review_store.audit_events(), [])
                self.assertEqual(len(service.review_store.list_events()), 3)
            finally:
                session.close()


class EventAdjustContractTest(unittest.TestCase):
    @staticmethod
    def automatic_fixture(root: Path):
        source, project = create_adjustable_project(root)
        with ProjectWindowService.open(project.project_dir) as service:
            restored = service.review_store.undo(
                actor="fixture",
                session_id="fixture",
                reason="prepare automatic event",
            )
            if restored is None or restored["origin"] != "automatic":
                raise AssertionError("adjustable fixture did not restore its automatic apex")
        return source, project

    def test_automatic_adjust_is_support_bounded_preserves_status_and_reopens(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _source, project = self.automatic_fixture(root)
            session = open_session(root, project)
            try:
                selected = select_nearest(session, 0.5)
                event = selected["selection"]["event"]
                self.assertEqual(event["status"], "accepted")
                aim = session.begin_event_edit(
                    {"mode": "adjust", "action_token": event["action_token"]}
                )
                self.assertEqual(aim["before"], {"time_min": 0.5, "intensity": 1000.0})
                self.assertLess(aim["allowed_interval"]["start_min"], 0.5)
                self.assertGreater(aim["allowed_interval"]["end_min"], 0.5)
                with self.assertRaises(WebBoundaryError) as outside:
                    session.preview_event_edit(
                        {
                            "aim_token": aim["aim_token"],
                            "click_time_min": aim["allowed_interval"]["end_min"] + 0.01,
                        }
                    )
                self.assertEqual(outside.exception.code, "outside_allowed_interval")
                session.cancel_event_edit({"edit_token": aim["aim_token"]})

                selected = select_nearest(session, 0.5)
                aim = session.begin_event_edit(
                    {
                        "mode": "adjust",
                        "action_token": selected["selection"]["event"]["action_token"],
                    }
                )
                preview = session.preview_event_edit(
                    {"aim_token": aim["aim_token"], "click_time_min": 31.0 / 60.0}
                )
                self.assertEqual(preview["change"]["before"]["time_min"], 0.5)
                self.assertAlmostEqual(preview["change"]["after"]["time_min"], 31.0 / 60.0)
                applied = session.apply_event_edit(
                    {"preview_token": preview["preview_token"], "note": "重新定位"}
                )
                adjusted = applied["workspace"]["selection"]["event"]
                self.assertEqual(adjusted["status"], "accepted")
                self.assertEqual(adjusted["origin"], "manual_adjusted")
                self.assertAlmostEqual(adjusted["apex_time_sec"], 31.0)

                undone = session.undo_review({"note": "撤销定位"})["workspace"]
                self.assertEqual(undone["selection"]["event"]["origin"], "automatic")
                self.assertEqual(undone["selection"]["event"]["status"], "accepted")
                redone = session.redo_review({"note": "重做定位"})["workspace"]
                self.assertEqual(redone["selection"]["event"]["origin"], "manual_adjusted")
            finally:
                session.close()

            reopened = open_session(root, project)
            try:
                persisted = select_nearest(reopened, 31.0 / 60.0)["selection"]["event"]
                self.assertEqual(persisted["status"], "accepted")
                self.assertEqual(persisted["origin"], "manual_adjusted")
                self.assertAlmostEqual(persisted["apex_time_sec"], 31.0)
                with ProjectWindowService.open(project.project_dir) as service:
                    actions = [row["action"] for row in service.review_store.audit_events()]
                self.assertEqual(actions[-3:], ["adjust_apex", "undo", "redo"])
            finally:
                reopened.close()

    def test_adjust_preview_conflict_is_consumed_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _source, project = self.automatic_fixture(root)
            first = open_session(root, project)
            second = open_session(root, project)
            try:
                first_selected = select_nearest(first, 0.5)
                second_selected = select_nearest(second, 0.5)
                aim = first.begin_event_edit(
                    {
                        "mode": "adjust",
                        "action_token": first_selected["selection"]["event"]["action_token"],
                    }
                )
                preview = first.preview_event_edit(
                    {"aim_token": aim["aim_token"], "click_time_min": 31.0 / 60.0}
                )
                second.review_decision(
                    {
                        "action_token": second_selected["selection"]["event"]["action_token"],
                        "decision": "pending",
                        "note": "另一窗口先保存",
                    }
                )
                with self.assertRaises(WebBoundaryError) as conflict:
                    first.apply_event_edit(
                        {"preview_token": preview["preview_token"], "note": "不得覆盖"}
                    )
                self.assertEqual(conflict.exception.code, "review_conflict")
                self.assertEqual(conflict.exception.status.value, 409)
                with self.assertRaises(WebBoundaryError) as replay:
                    first.apply_event_edit({"preview_token": preview["preview_token"]})
                self.assertEqual(replay.exception.code, "stale_edit")
                refreshed = select_nearest(first, 0.5)["selection"]["event"]
                self.assertEqual(refreshed["status"], "pending")
                self.assertEqual(refreshed["origin"], "automatic")
                self.assertAlmostEqual(refreshed["apex_time_sec"], 30.0)
            finally:
                second.close()
                first.close()


class EventEditHTTPContractTest(unittest.TestCase):
    @staticmethod
    def request(server, method: str, path: str, *, payload=None, token=None):
        parsed = urlparse(server.base_url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["X-MS-Event-Token"] = token
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, json.loads(raw)

    def test_http_routes_require_token_close_fields_and_hide_failure_details(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            source, project = create_guided_project(root)
            session = open_session(root, project)
            server = create_http_server(session=session)
            try:
                server.start()
                status, bootstrap = self.request(server, "GET", "/api/bootstrap")
                self.assertEqual(status, 200)
                token = bootstrap["request_token"]
                status, error = self.request(
                    server,
                    "POST",
                    "/api/event-edits/aim",
                    payload={"mode": "add"},
                )
                self.assertEqual(status, 403)
                self.assertEqual(error["error"]["code"], "invalid_request_token")
                status, error = self.request(
                    server,
                    "POST",
                    "/api/event-edits/aim",
                    payload={"mode": "add", "raw_event_id": "private"},
                    token=token,
                )
                self.assertEqual(status, 400)

                status, aim = self.request(
                    server,
                    "POST",
                    "/api/event-edits/aim",
                    payload={"mode": "add"},
                    token=token,
                )
                self.assertEqual(status, 200)
                status, preview = self.request(
                    server,
                    "POST",
                    "/api/event-edits/preview",
                    payload={"aim_token": aim["aim_token"], "click_time_min": 0.75},
                    token=token,
                )
                self.assertEqual(status, 200)
                assert_workspace_safe(
                    self,
                    {"aim": aim, "preview": preview},
                    private_values=(str(root), str(source), str(project.project_dir)),
                )
                store = session._workspace._window_service.review_store
                with patch.object(store, "add_event", side_effect=OSError(str(source))):
                    status, failed = self.request(
                        server,
                        "POST",
                        "/api/event-edits/apply",
                        payload={"preview_token": preview["preview_token"], "note": "失败注入"},
                        token=token,
                    )
                self.assertEqual(status, 500)
                self.assertEqual(failed["error"]["code"], "operation_failed")
                self.assertNotIn(str(source), json.dumps(failed, ensure_ascii=False))
                self.assertEqual(len(store.list_events()), 3)
                self.assertEqual(store.audit_events(), [])
                status, replay = self.request(
                    server,
                    "POST",
                    "/api/event-edits/apply",
                    payload={"preview_token": preview["preview_token"]},
                    token=token,
                )
                self.assertEqual(status, 409)
                self.assertEqual(replay["error"]["code"], "stale_edit")
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()
